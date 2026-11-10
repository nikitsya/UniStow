import json
import os
from datetime import timedelta
from uuid import uuid4

import stripe
from fastapi import HTTPException
from sqlalchemy import text

from services.overdue_billing import validate_intent
from services.payments import owned_rental, stripe_client


def apply_recovery(db, checkout):
    recovery_id = (checkout.get('metadata') or {}).get('overdue_payment_id')
    if not recovery_id:
        return False
    payment = db.execute(text('SELECT * FROM overdue_payments WHERE id=:id'),
                         {'id': recovery_id}).mappings().first()
    if not payment:
        raise HTTPException(409, 'Unknown outstanding payment.')
    locker = db.scalar(text('SELECT locker_id FROM rentals WHERE id=:id'), {'id': payment['rental_id']})
    db.execute(text('SELECT id FROM lockers WHERE id=:id FOR UPDATE'), {'id': locker})
    payment = db.execute(text('SELECT * FROM overdue_payments WHERE id=:id FOR UPDATE'),
                         {'id': recovery_id}).mappings().one()
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
        intent = stripe_client().v1.payment_intents.retrieve(checkout['payment_intent']).to_dict()
        if (intent.get('livemode') is not False or intent.get('status') != 'succeeded'
                or intent.get('amount_received') != payment['amount_cents']
                or intent.get('currency') != 'eur'
                or intent.get('metadata') != params['metadata']):
            raise HTTPException(409, 'Outstanding payment needs verification.')
        if payment['status'] == 'expired':
            raise HTTPException(409, 'Expired payment needs review.')
        if payment['status'] != 'paid':
            charges = db.execute(text('SELECT * FROM overdue_charges WHERE id = ANY(:ids) FOR UPDATE'),
                                 {'ids': payment['charge_ids']}).mappings().all()
            if (len(charges) != len(payment['charge_ids'])
                    or any(c['rental_id'] != payment['rental_id'] or c['status'] != 'failed' for c in charges)
                    or sum(c['amount_cents'] for c in charges) != payment['amount_cents']):
                raise HTTPException(409, 'Outstanding charges changed; payment needs review.')
            # Preserve original declined intents for audit; the recovery owns the successful intent.
            db.execute(text("""
                UPDATE overdue_charges SET status='paid', paid_at=clock_timestamp(), failure_reason=NULL
                WHERE id = ANY(:ids)
            """), {'ids': payment['charge_ids']})
            db.execute(text("""
                UPDATE overdue_payments SET status='paid', payment_intent_id=:intent,
                    paid_at=clock_timestamp() WHERE id=:id
            """), {'id': recovery_id, 'intent': intent['id']})
    elif checkout.get('status') == 'expired' and checkout.get('payment_status') == 'unpaid':
        db.execute(text("UPDATE overdue_payments SET status='expired' WHERE id=:id AND status='pending'"),
                   {'id': recovery_id})
    db.commit()
    return True


def reconcile_outstanding(db, rental_id, user_id):
    owned_rental(db, rental_id, user_id)
    payments = db.execute(text("""
        SELECT checkout_session_id FROM overdue_payments
        WHERE rental_id=:id AND status='pending' AND checkout_session_id IS NOT NULL
    """), {'id': rental_id}).scalars().all()
    for session_id in payments:
        apply_recovery(db, stripe_client().v1.checkout.sessions.retrieve(session_id).to_dict())
    return {'status': 'checked'}


def create_outstanding_checkout(db, rental_id, user_id):
    client = stripe_client()
    rental = owned_rental(db, rental_id, user_id)
    db.execute(text('SELECT id FROM lockers WHERE id=:id FOR UPDATE'), {'id': rental['locker_id']})
    payment = db.execute(text("SELECT * FROM overdue_payments WHERE rental_id=:id AND status='pending'"),
                         {'id': rental_id}).mappings().first()
    now = db.scalar(text('SELECT clock_timestamp()'))
    if not payment:
        charges = db.execute(text("""
            SELECT * FROM overdue_charges WHERE rental_id=:id AND status='failed' ORDER BY id FOR UPDATE
        """), {'id': rental_id}).mappings().all()
        if not charges:
            raise HTTPException(409, 'There is no unpaid balance to collect.')
        # Cancel declined intents before a replacement can collect the same debt.
        for charge in charges:
            if not charge['stripe_payment_intent_id'] or not charge['request_params']:
                raise HTTPException(409, 'This charge needs operator review before payment.')
            intent = client.v1.payment_intents.retrieve(charge['stripe_payment_intent_id']).to_dict()
            try:
                validate_intent(intent, charge)
            except ValueError:
                # A cancelled intent can retain no selected method after a decline.
                error_method = (intent.get('last_payment_error') or {}).get('payment_method') or {}
                probe = dict(intent, payment_method=error_method.get('id'))
                validate_intent(probe, charge)
            if intent['status'] == 'requires_payment_method':
                intent = client.v1.payment_intents.cancel(
                    intent['id'], options={'idempotency_key': f"overdue-cancel-{charge['id']}"}
                ).to_dict()
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
                                         'product_data': {'name': f'UniStow rental {rental_id} — outstanding extra days'}},
                            'quantity': 1}],
            'success_url': f'{base}/rentals/{rental_id}?outstanding_returned=1',
            'cancel_url': f'{base}/rentals/{rental_id}',
            'expires_at': int((now + timedelta(minutes=31)).timestamp()),
        }
        payment = db.execute(text("""
            INSERT INTO overdue_payments(id,rental_id,charge_ids,amount_cents,checkout_params)
            VALUES(:id,:rental,CAST(:charges AS jsonb),:amount,CAST(:params AS jsonb)) RETURNING *
        """), {'id': recovery_id, 'rental': rental_id, 'charges': json.dumps([c['id'] for c in charges]),
               'amount': amount, 'params': json.dumps(params)}).mappings().one()
    payment = dict(payment)
    db.commit()
    if payment['checkout_session_id']:
        checkout = client.v1.checkout.sessions.retrieve(payment['checkout_session_id']).to_dict()
    else:
        if now - payment['created_at'] >= timedelta(hours=23):
            raise HTTPException(409, 'This payment needs reconciliation before retrying.')
        checkout = client.v1.checkout.sessions.create(
            payment['checkout_params'], options={'idempotency_key': payment['id']}
        ).to_dict()
        db.execute(text('UPDATE overdue_payments SET checkout_session_id=:session WHERE id=:id'),
                   {'session': checkout['id'], 'id': payment['id']})
        db.commit()
    apply_recovery(db, checkout)
    if checkout['status'] != 'open' or not checkout.get('url'):
        raise HTTPException(409, 'Refresh the page to check the outstanding payment.')
    return {'url': checkout['url']}
