import json
import os
from datetime import timedelta
from functools import lru_cache
from typing import Literal
from uuid import uuid4

import stripe
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from stripe.params.checkout import SessionCreateParams, SessionCreateParamsPaymentIntentData

from services.booking_eligibility import require_booking_eligibility
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
    rental = (await db.execute(text("""
                             SELECT rentals.*,
                                    lockers.number,
                                    lockers.door_state,
                                    lockers.last_seen_at,
                                    lockers.device_ready,
                                    EXISTS (SELECT 1
                                            FROM locker_commands c
                                            WHERE c.rental_id = rentals.id
                                              AND c.action = 'finish'
                                              AND c.completed_at IS NULL
                                              AND (c.dispatched_at IS NOT NULL OR c.expires_at > CURRENT_TIMESTAMP)) AS collecting,
                                    payments.status                                                                  AS payment_status
                             FROM rentals
                                      JOIN lockers ON lockers.id = rentals.locker_id
                                      LEFT JOIN payments ON payments.rental_id = rentals.id
                             WHERE rentals.id = :id
                               AND rentals.user_id = :user_id
                                    """), {"id": rental_id, "user_id": user_id})).mappings().first()
    if rental is None:
        raise HTTPException(status_code=404, detail="Reservation not found.")
    return dict(rental)


def checkout_parameters(rental, base_url: str, now) -> CheckoutParameters:
    # Stripe requires at least 30 minutes; allow a minute for the API request.
    expires_at = now + timedelta(minutes=31)
    if expires_at >= rental["paid_until"]:
        raise HTTPException(status_code=409, detail="Too close to the rental deadline to start checkout.")
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
    await db.execute(text("SELECT id FROM lockers WHERE id = :id FOR UPDATE"), {"id": rental["locker_id"]})
    rental = await owned_rental(db, rental_id, user_id)
    if rental["status"] != "pending":
        raise HTTPException(status_code=409, detail="This reservation is not awaiting payment.")
    now = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
    payment = (
        await db.execute(text("SELECT * FROM payments WHERE rental_id = :id"), {"id": rental_id})).mappings().first()
    if payment is None:
        if rental["reserved_until"] <= now:
            raise HTTPException(status_code=409, detail="This reservation has expired. Please reserve again.")
        base_url = os.environ.get("APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        params = checkout_parameters(rental, base_url, now)
        payment = (await db.execute(text("""
                                  INSERT INTO payments (rental_id, request_key, checkout_params)
                                  VALUES (:id, :key, CAST(:params AS jsonb))
                                  RETURNING *
                                  """),
                                    {"id": rental_id, "key": str(uuid4()),
                                     "params": json.dumps(params)})).mappings().one()
    payment = dict(payment)
    # Keep the durable request even if Stripe times out or the browser disconnects.
    await db.commit()
    try:
        if payment["checkout_session_id"]:
            checkout = await client.v1.checkout.sessions.retrieve_async(payment["checkout_session_id"])
        else:
            if now - payment["created_at"] > timedelta(hours=23):
                raise HTTPException(status_code=409, detail="This checkout needs reconciliation before retrying.")
            checkout = await client.v1.checkout.sessions.create_async(
                payment["checkout_params"], options={"idempotency_key": payment["request_key"]}
            )
            await db.execute(text("UPDATE payments SET checkout_session_id = :session WHERE rental_id = :id"),
                       {"id": rental_id, "session": checkout.id})
            await db.commit()
    except stripe.StripeError:
        raise HTTPException(status_code=503, detail="Could not open Stripe. Please retry this reservation.") from None
    if checkout.livemode:
        raise HTTPException(status_code=503, detail="Only test payments are supported.")
    if checkout.status != "open" or not checkout.url:
        raise HTTPException(status_code=409,
                            detail="Checkout is closed. Refresh this page to check its payment status.")
    return {"url": checkout.url}
