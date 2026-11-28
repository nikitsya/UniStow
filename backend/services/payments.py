import os
from datetime import datetime
from functools import lru_cache
from typing import Literal
from uuid import uuid4

import stripe
from fastapi import HTTPException
from sqlalchemy import DateTime, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from stripe.params.checkout import SessionCreateParams, SessionCreateParamsPaymentIntentData

from models import Locker, LockerCommand, Payment, Rental
from services.booking_eligibility import require_booking_eligibility
from services.checkout import CHECKOUT_WINDOW, CHECKOUT_DEADLINE_MESSAGE, checkout_fits, resume_checkout
from services.rentals import PAYMENT_METHOD_TERMS_VERSION


class CheckoutParameters(SessionCreateParams):
    # The pinned 2025-06-30.basil API uses this field for payment methods.
    payment_method_types: list[Literal["card"]]


@lru_cache(maxsize=1)
def stripe_http_client():
    return stripe.HTTPXClient(timeout=15)


async def close_stripe_http_client():
    if stripe_http_client.cache_info().currsize:
        await stripe_http_client().close_async()
        stripe_http_client.cache_clear()


def stripe_client():
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key.startswith("sk_test_"):
        raise HTTPException(status_code=503, detail="Stripe test payments are not configured.")
    return stripe.StripeClient(
        key, stripe_version="2025-06-30.basil",
        http_client=stripe_http_client(), max_network_retries=1,
    )


async def owned_rental(db: AsyncSession, rental_id: int, user_id: int):
    collecting = exists(select(LockerCommand.id).where(
        LockerCommand.rental_id == Rental.id,
        LockerCommand.action == "finish",
        LockerCommand.completed_at.is_(None),
        or_(LockerCommand.dispatched_at.is_not(None),
            LockerCommand.expires_at > func.current_timestamp()),
    ))
    rental = (await db.execute(
        select(*Rental.__table__.columns, Locker.number, Locker.door_state,
               Locker.last_seen_at, Locker.device_ready, collecting.label("collecting"),
               Payment.status.label("payment_status"))
        .select_from(Rental)
        .join(Locker, Locker.id == Rental.locker_id)
        .outerjoin(Payment, Payment.rental_id == Rental.id)
        .where(Rental.id == rental_id, Rental.user_id == user_id)
    )).mappings().first()
    if rental is None:
        raise HTTPException(status_code=404, detail="Reservation not found.")
    return dict(rental)


def checkout_parameters(rental, base_url: str, now) -> CheckoutParameters:
    # Stripe requires at least 30 minutes; allow a minute for the API request.
    expires_at = now + CHECKOUT_WINDOW
    if not checkout_fits(rental["paid_until"], now):
        raise HTTPException(409, {'code': 'checkout_deadline_passed', 'message': CHECKOUT_DEADLINE_MESSAGE})
    params: CheckoutParameters = {
        "mode": "payment", "payment_method_types": ["card"],
        "branding_settings": {"display_name": "UniStow"},
        "client_reference_id": str(rental["id"]),
        "metadata": {"rental_id": str(rental["id"])},
        "line_items": [{"price_data": {
            "currency": "eur", "unit_amount": rental["total_price_cents"],
            "product_data": {"name": f"UniStow Locker {rental['number']} — {rental['prepaid_days']} day(s)"},
        }, "quantity": 1}],
        "success_url": f"{base_url}/rentals/{rental['id']}?returned=1",
        "cancel_url": f"{base_url}/rentals/{rental['id']}",
        "expires_at": int(expires_at.timestamp()),
    }

    if rental.get("overdue_terms_version") == PAYMENT_METHOD_TERMS_VERSION:
        params["customer_creation"] = "always"
        params["payment_intent_data"] = SessionCreateParamsPaymentIntentData(
            setup_future_usage="off_session",
            metadata={"rental_id": str(rental["id"])},
        )
    return params


async def create_checkout(db: AsyncSession, rental_id: int, user_id: int):
    client = stripe_client()
    rental = await owned_rental(db, rental_id, user_id)
    # Use the same lock order as reservation creation.
    await require_booking_eligibility(db, user_id)
    await db.execute(select(Locker.id).where(Locker.id == rental["locker_id"]).with_for_update())
    rental = await owned_rental(db, rental_id, user_id)
    if rental["status"] != "pending":
        raise HTTPException(status_code=409, detail="This reservation is not awaiting payment.")
    now: datetime = (await db.execute(
        select(func.clock_timestamp(type_=DateTime(timezone=True)))
    )).scalar_one()
    payment = await db.scalar(select(Payment).where(Payment.rental_id == rental_id)
                              .execution_options(populate_existing=True))
    if payment is None:
        if rental["reserved_until"] <= now:
            raise HTTPException(status_code=409, detail="This reservation has expired. Please reserve again.")
        base_url = os.environ.get("APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        params = checkout_parameters(rental, base_url, now)
        payment = Payment(rental_id=rental_id, request_key=str(uuid4()), checkout_params=dict(params))
        db.add(payment)
        await db.flush()
    # Keep the durable request even if Stripe times out or the browser disconnects.
    await db.commit()
    try:
        checkout = await resume_checkout(db, client, Payment, rental_id, paid_until=rental['paid_until'])
    except stripe.StripeError:
        raise HTTPException(status_code=503, detail="Could not open Stripe. Please retry this reservation.") from None
    if checkout is None:
        raise HTTPException(409, {'code': 'checkout_expired', 'message':
            'This unpaid reservation has expired. Please choose a locker and rental period again.'})
    if checkout.livemode:
        raise HTTPException(status_code=503, detail="Only test payments are supported.")
    if checkout.status != "open" or not checkout.url:
        raise HTTPException(status_code=409,
                            detail="Checkout is closed. Refresh this page to check its payment status.")
    return {"url": checkout.url}
