import os

import stripe
from fastapi import HTTPException
from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import Locker, Payment, PaymentEvent, Rental
from services.checkout import resume_checkout
from services.payments import owned_rental, stripe_client
from services.rentals import PAYMENT_METHOD_TERMS_VERSION

CHECKOUT_EVENTS = {
    "checkout.session.completed", "checkout.session.expired",
    "checkout.session.async_payment_succeeded", "checkout.session.async_payment_failed",
}


def verify_event(payload: bytes, signature: str):
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not secret.startswith("whsec_"):
        raise HTTPException(status_code=503, detail="Webhook verification is not configured.")
    try:
        event = stripe.Webhook.construct_event(payload, signature, secret).to_dict()
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid webhook signature or payload.") from None
    if event.get("livemode") is not False:
        raise HTTPException(status_code=400, detail="Only test events are supported.")
    return event


async def retrieve_checkout(session_id: str):
    try:
        return (await stripe_client().v1.checkout.sessions.retrieve_async(session_id)).to_dict()
    except stripe.StripeError:
        raise HTTPException(status_code=503, detail="Payment verification is temporarily unavailable.") from None


async def saved_payment_method(checkout, rental):
    try:
        intent = (await stripe_client().v1.payment_intents.retrieve_async(
            checkout["payment_intent"], {"expand": ["payment_method"]}
        )).to_dict()
    except stripe.StripeError:
        raise HTTPException(503, "Payment verification is temporarily unavailable.") from None
    method = intent.get("payment_method")
    customer = checkout.get("customer")
    if (intent.get("id") != checkout["payment_intent"]
            or intent.get("livemode") is not False
            or intent.get("status") != "succeeded"
            or intent.get("amount") != rental["total_price_cents"]
            or intent.get("currency") != "eur"
            or intent.get("setup_future_usage") != "off_session"
            or not isinstance(customer, str) or not customer.startswith("cus_")
            or intent.get("customer") != customer
            or not isinstance(method, dict)
            or method.get("livemode") is not False
            or method.get("type") != "card"
            or method.get("customer") != customer
            or not isinstance(method.get("id"), str) or not method["id"].startswith("pm_")):
        raise HTTPException(409, "The saved payment method needs review.")
    return {"customer": customer, "method": method["id"]}


async def apply_checkout(db: AsyncSession, checkout, event=None):
    payment = (await db.execute(
        select(Payment.rental_id, Payment.checkout_params, Rental.locker_id)
        .join(Rental, Rental.id == Payment.rental_id)
        .where(Payment.checkout_session_id == checkout["id"])
    )).mappings().first()
    if payment is None:
        # Stripe may notify us before the create-checkout response has been saved.
        reference = checkout.get("client_reference_id", "") or ""
        if reference.isdigit() and await db.scalar(select(Payment.rental_id).where(
                Payment.rental_id == int(reference), Payment.checkout_session_id.is_(None),
        )) is not None:
            raise HTTPException(status_code=503, detail="Checkout is still being saved. Please retry.")
        return

    await db.execute(select(Locker.id).where(Locker.id == payment["locker_id"]).with_for_update())
    rental = (await db.execute(
        select(Rental).where(Rental.id == payment["rental_id"])
        .execution_options(populate_existing=True)
    )).scalar_one()
    if (checkout.get("livemode") is not False or checkout.get("mode") != "payment"
            or checkout.get("currency") != "eur"
            or checkout.get("amount_total") != rental.total_price_cents
            or checkout.get("client_reference_id") != str(rental.id)
            or checkout.get("metadata", {}).get("rental_id") != str(rental.id)):
        raise HTTPException(status_code=409, detail="Payment details do not match the reservation.")

    if event:
        inserted = await db.scalar(
            insert(PaymentEvent).values(event_id=event["id"], event_type=event["type"],
                                        checkout_session_id=checkout["id"])
            .on_conflict_do_nothing(index_elements=[PaymentEvent.event_id])
            .returning(PaymentEvent.event_id)
        )
        if inserted is None:
            await db.commit()
            return

    if checkout.get("status") == "complete" and checkout.get("payment_status") == "paid":
        if rental.status == "cancelled" or not checkout.get("payment_intent"):
            raise HTTPException(status_code=409, detail="This payment needs review before activation.")
        saved = None
        if (rental.overdue_terms_version == PAYMENT_METHOD_TERMS_VERSION
                and payment["checkout_params"].get("payment_intent_data", {}).get(
                    "setup_future_usage") == "off_session"):
            saved = await saved_payment_method(checkout, {"total_price_cents": rental.total_price_cents})
        await db.execute(update(Payment).where(Payment.rental_id == rental.id).values(
            status="paid", payment_intent_id=checkout["payment_intent"],
            confirmed_at=func.coalesce(Payment.confirmed_at, func.current_timestamp()),
        ))
        if saved:
            await db.execute(update(Payment).where(Payment.rental_id == rental.id).values(
                stripe_customer_id=saved["customer"], stripe_payment_method_id=saved["method"],
                payment_method_saved_at=func.coalesce(Payment.payment_method_saved_at, func.current_timestamp()),
            ))
        await db.execute(update(Rental).where(Rental.id == rental.id, Rental.status == "pending").values(
            status=case((Rental.paid_until <= func.clock_timestamp(), "overdue"), else_="active"),
        ).execution_options(synchronize_session="fetch"))
    elif checkout.get("status") == "expired" and checkout.get("payment_status") == "unpaid":
        expired = await db.scalar(update(Payment).where(
            Payment.rental_id == rental.id, Payment.status != "paid",
        ).values(status="expired").returning(Payment.rental_id))
        if expired is not None:
            await db.execute(update(Rental).where(
                Rental.id == rental.id, Rental.status == "pending",
            ).values(status="cancelled"))
    await db.commit()


async def process_event(db: AsyncSession, event):
    if event["type"] not in CHECKOUT_EVENTS:
        return
    if await db.scalar(select(PaymentEvent.event_id).where(PaymentEvent.event_id == event["id"])) is not None:
        return
    # Retrieve current state so delayed or reordered events cannot undo a payment.
    checkout = await retrieve_checkout(event["data"]["object"]["id"])
    if checkout.get("metadata", {}).get("overdue_payment_id"):
        from services.overdue_payments import apply_recovery
        await apply_recovery(db, checkout)
    else:
        await apply_checkout(db, checkout, event)


async def reconcile_rental(db: AsyncSession, rental_id: int, user_id: int):
    await owned_rental(db, rental_id, user_id)
    session_id = await db.scalar(select(Payment.checkout_session_id).where(Payment.rental_id == rental_id))
    if session_id:
        await apply_checkout(db, await retrieve_checkout(session_id))
    return await owned_rental(db, rental_id, user_id)


async def cancel_pending_reservation(db: AsyncSession, rental_id: int, user_id: int):
    rental = await owned_rental(db, rental_id, user_id)
    await db.execute(select(Locker.id).where(Locker.id == rental["locker_id"]).with_for_update())
    rental = await owned_rental(db, rental_id, user_id)
    if rental["status"] == "cancelled":
        return
    if rental["status"] != "pending" or rental["payment_status"] == "paid":
        raise HTTPException(409, "Only an unpaid reservation can be cancelled.")
    if rental["payment_status"] is None:
        await db.execute(update(Rental).where(Rental.id == rental_id).values(status="cancelled"))
        await db.commit()
        return

    session_id = await db.scalar(select(Payment.checkout_session_id).where(Payment.rental_id == rental_id))
    await db.commit()
    if not session_id:
        try:
            checkout = await resume_checkout(db, stripe_client(), Payment, rental_id,
                                             paid_until=rental['paid_until'], refresh_expiry=False)
        except stripe.StripeError:
            raise HTTPException(503, 'Could not confirm cancellation. Please try again.') from None
        if checkout is None:
            return
        session_id = checkout.id
    try:
        checkout = (await stripe_client().v1.checkout.sessions.expire_async(session_id)).to_dict()
    except stripe.InvalidRequestError:
        # Payment or expiry may have won the race with cancellation.
        checkout = await retrieve_checkout(session_id)
    except stripe.StripeError:
        raise HTTPException(503, "Could not confirm cancellation. Please try again.") from None
    await apply_checkout(db, checkout)
    if (await owned_rental(db, rental_id, user_id))["status"] != "cancelled":
        raise HTTPException(409, "Check your payment status before cancelling.")
