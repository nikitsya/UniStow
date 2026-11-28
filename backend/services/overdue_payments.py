import os
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import DateTime, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import uuid4

from models import Locker, OverdueCharge, OverduePayment, Rental
from services.booking_eligibility import lock_account, lock_rental_account
from services.checkout import CHECKOUT_WINDOW, resume_checkout
from services.overdue_billing import validate_intent
from services.payments import owned_rental, stripe_client


async def apply_recovery(db: AsyncSession, checkout):
    recovery_id = (checkout.get('metadata') or {}).get('overdue_payment_id')
    if not recovery_id:
        return False
    payment = (await db.execute(select(*OverduePayment.__table__.columns).where(
        OverduePayment.id == recovery_id,
    ))).mappings().first()
    if not payment:
        raise HTTPException(409, 'Unknown outstanding payment.')
    await lock_rental_account(db, payment['rental_id'])
    locker = await db.scalar(select(Rental.locker_id).where(Rental.id == payment['rental_id']))
    await db.execute(select(Locker.id).where(Locker.id == locker).with_for_update())
    payment = (await db.execute(select(*OverduePayment.__table__.columns).where(
        OverduePayment.id == recovery_id,
    ).with_for_update())).mappings().one()
    params = payment['checkout_params']
    if (checkout.get('livemode') is not False or checkout.get('mode') != 'payment'
            or checkout.get('amount_total') != payment['amount_cents']
            or checkout.get('currency') != 'eur'
            or checkout.get('client_reference_id') != recovery_id
            or checkout.get('metadata') != params['metadata']
            or (payment['checkout_session_id'] and checkout['id'] != payment['checkout_session_id'])):
        raise HTTPException(409, 'Outstanding payment details do not match.')
    if not payment['checkout_session_id']:
        raise HTTPException(503, 'Outstanding payment is still being saved.')
    if checkout.get('status') == 'complete' and checkout.get('payment_status') == 'paid':
        intent = (await stripe_client().v1.payment_intents.retrieve_async(checkout['payment_intent'])).to_dict()
        if (intent.get('livemode') is not False or intent.get('status') != 'succeeded'
                or intent.get('amount_received') != payment['amount_cents']
                or intent.get('currency') != 'eur'
                or intent.get('metadata') != params['metadata']):
            raise HTTPException(409, 'Outstanding payment needs verification.')
        if payment['status'] == 'expired':
            raise HTTPException(409, 'Expired payment needs review.')
        if payment['status'] != 'paid':
            charges = (await db.execute(select(*OverdueCharge.__table__.columns).where(
                OverdueCharge.id.in_(payment['charge_ids']),
            ).with_for_update())).mappings().all()
            if (len(charges) != len(payment['charge_ids'])
                    or any(c['rental_id'] != payment['rental_id'] or c['status'] != 'failed' for c in charges)
                    or sum(c['amount_cents'] for c in charges) != payment['amount_cents']):
                raise HTTPException(409, 'Outstanding charges changed; payment needs review.')
            # Preserve original declined intents for audit; the recovery owns the successful intent.
            await db.execute(update(OverdueCharge).where(
                OverdueCharge.id.in_(payment['charge_ids']),
            ).values(status='paid', paid_at=func.clock_timestamp(), failure_reason=None))
            await db.execute(update(OverduePayment).where(OverduePayment.id == recovery_id).values(
                status='paid', payment_intent_id=intent['id'], paid_at=func.clock_timestamp(),
            ))
    elif checkout.get('status') == 'expired' and checkout.get('payment_status') == 'unpaid':
        await db.execute(update(OverduePayment).where(
            OverduePayment.id == recovery_id, OverduePayment.status == 'pending',
        ).values(status='expired'))
    await db.commit()
    return True


async def reconcile_outstanding(db: AsyncSession, rental_id, user_id):
    await owned_rental(db, rental_id, user_id)
    payments = (await db.scalars(select(OverduePayment.checkout_session_id).where(
        OverduePayment.rental_id == rental_id,
        OverduePayment.status == 'pending',
        OverduePayment.checkout_session_id.is_not(None),
    ))).all()
    for session_id in payments:
        await apply_recovery(db, (await stripe_client().v1.checkout.sessions.retrieve_async(session_id)).to_dict())
    return {'status': 'checked'}


async def create_outstanding_checkout(db: AsyncSession, rental_id, user_id):
    client = stripe_client()
    rental = await owned_rental(db, rental_id, user_id)
    await lock_account(db, user_id)
    await db.execute(select(Locker.id).where(Locker.id == rental['locker_id']).with_for_update())
    payment = (await db.execute(select(*OverduePayment.__table__.columns).where(
        OverduePayment.rental_id == rental_id, OverduePayment.status == 'pending',
    ))).mappings().first()
    now: datetime = (await db.execute(
        select(func.clock_timestamp(type_=DateTime(timezone=True)))
    )).scalar_one()
    if not payment:
        charges = (await db.execute(select(*OverdueCharge.__table__.columns).where(
            OverdueCharge.rental_id == rental_id, OverdueCharge.status == 'failed',
        ).order_by(OverdueCharge.id).with_for_update())).mappings().all()
        if not charges:
            raise HTTPException(409, 'There is no unpaid balance to collect.')
        # Cancel declined intents before a replacement can collect the same debt.
        for charge in charges:
            if not charge['stripe_payment_intent_id'] or not charge['request_params']:
                raise HTTPException(409, 'This charge needs operator review before payment.')
            intent = (await client.v1.payment_intents.retrieve_async(charge['stripe_payment_intent_id'])).to_dict()
            try:
                validate_intent(intent, charge)
            except ValueError:
                # A cancelled intent can retain no selected method after a decline.
                error_method = (intent.get('last_payment_error') or {}).get('payment_method') or {}
                probe = dict(intent, payment_method=error_method.get('id'))
                validate_intent(probe, charge)
            if intent['status'] == 'requires_payment_method':
                intent = (await client.v1.payment_intents.cancel_async(
                    intent['id'], options={'idempotency_key': f"overdue-cancel-{charge['id']}"}
                )).to_dict()
            if intent['status'] != 'canceled':
                raise HTTPException(409, 'The previous payment must be resolved before retrying.')
        recovery_id = str(uuid4())
        amount = sum(c['amount_cents'] for c in charges)
        metadata = {'overdue_payment_id': recovery_id, 'rental_id': str(rental_id)}
        base = os.getenv('APP_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
        params = {
            'mode': 'payment', 'payment_method_types': ['card'],
            'branding_settings': {'display_name': 'UniStow'},
            'client_reference_id': recovery_id, 'metadata': metadata,
            'payment_intent_data': {'metadata': metadata},
            'line_items': [{'price_data': {'currency': 'eur', 'unit_amount': amount,
                                           'product_data': {
                                               'name': f'UniStow rental {rental_id} — outstanding extra days'}},
                            'quantity': 1}],
            'success_url': f'{base}/rentals/{rental_id}?outstanding_returned=1',
            'cancel_url': f'{base}/rentals/{rental_id}',
            'expires_at': int((now + CHECKOUT_WINDOW).timestamp()),
        }
        payment = (await db.execute(insert(OverduePayment).values(
            id=recovery_id, rental_id=rental_id, charge_ids=[c['id'] for c in charges],
            amount_cents=amount, checkout_params=params,
        ).returning(*OverduePayment.__table__.columns))).mappings().one()
    payment_id = payment['id']
    await db.commit()
    result = await resume_checkout(db, client, OverduePayment, payment_id)
    if result is None:
        raise HTTPException(409, 'This payment attempt has expired. Please try again.')
    checkout = result.to_dict()
    await apply_recovery(db, checkout)
    if checkout['status'] != 'open' or not checkout.get('url'):
        raise HTTPException(409, 'Refresh the page to check the outstanding payment.')
    return {'url': checkout['url']}
