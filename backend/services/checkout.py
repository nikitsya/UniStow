from datetime import timedelta
from typing import Any

import stripe
from fastapi import HTTPException
from sqlalchemy import DateTime, func, select, update

from models import Payment, Rental

CHECKOUT_WINDOW = timedelta(minutes=31)
CHECKOUT_DEADLINE_MESSAGE = (
    "There is not enough time to pay before this rental ends at 18:00. "
    "Cancel this reservation and choose a longer period, or reserve again "
    "after 18:00 (Ireland time)."
)
SHORT_RENTAL_MESSAGE = (
    "One-day rentals are unavailable until 18:00 (Ireland time). "
    "Choose 2–4 days or return after 18:00."
)


def checkout_fits(paid_until, now):
    return now + CHECKOUT_WINDOW < paid_until


async def checkout_now(db):
    return await db.scalar(select(func.clock_timestamp(type_=DateTime(timezone=True))))


async def resume_checkout(db, client, model, payment_id, *, paid_until=None, refresh_expiry=True):
    """Replay a durable request; repair expiry only after Stripe rejects it."""
    identity = model.rental_id if model is Payment else model.id
    expiry_refreshed = False
    while True:
        # Serialise creation and parameter repairs across tabs and app workers.
        payment = await db.scalar(select(model).where(identity == payment_id).with_for_update()
                                  .execution_options(populate_existing=True))
        if payment.checkout_session_id:
            session_id = payment.checkout_session_id
            await db.commit()
            return await client.v1.checkout.sessions.retrieve_async(session_id)
        if payment.status != 'pending':
            await db.commit()
            return None
        now = await checkout_now(db)
        if now - payment.created_at >= timedelta(hours=23):
            raise HTTPException(409, 'This payment needs operator review before retrying.')
        key = payment.request_key if model is Payment else payment.id
        params: dict[str, Any] = dict(payment.checkout_params)
        try:
            checkout = await client.v1.checkout.sessions.create_async(
                params, options={'idempotency_key': key},
            )
        except stripe.InvalidRequestError as error:
            now = await checkout_now(db)
            rejected_expiry = (
                    error.param == 'expires_at' and error.http_status in (None, 400)
                    and params.get('expires_at', float('inf')) <= now.timestamp() + 1800
            )
            if not rejected_expiry:
                raise
            if not refresh_expiry or (paid_until is not None and not checkout_fits(paid_until, now)):
                # A validation rejection did not create a session. A timeout never reaches this path.
                payment.status = 'expired'
                if model is Payment:
                    await db.execute(update(Rental).where(
                        Rental.id == payment_id, Rental.status == 'pending',
                    ).values(status='cancelled'))
                await db.commit()
                return None
            if expiry_refreshed:
                raise
            # Stripe does not cache validation failures. Keep the key so a previously
            # accepted request can never be replaced by a second successful session.
            payment.checkout_params = params | {'expires_at': int((now + CHECKOUT_WINDOW).timestamp())}
            await db.commit()
            expiry_refreshed = True
            continue
        payment.checkout_session_id = checkout.id
        await db.commit()
        return checkout
