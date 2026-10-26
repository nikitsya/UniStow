import os

import stripe
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from payments import owned_rental, stripe_client

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


def retrieve_checkout(session_id: str):
    try:
        return stripe_client().v1.checkout.sessions.retrieve(session_id).to_dict()
    except stripe.StripeError:
        raise HTTPException(status_code=503, detail="Payment verification is temporarily unavailable.") from None


def apply_checkout(db: Session, checkout, event=None):
    payment = db.execute(text("""
        SELECT payments.rental_id, rentals.locker_id FROM payments
        JOIN rentals ON rentals.id = payments.rental_id
        WHERE checkout_session_id = :session
    """), {"session": checkout["id"]}).mappings().first()
    if payment is None:
        # Stripe may notify us before the create-checkout response has been saved.
        reference = checkout.get("client_reference_id", "") or ""
        if reference.isdigit() and db.execute(text(
            "SELECT rental_id FROM payments WHERE rental_id = :id AND checkout_session_id IS NULL"
        ), {"id": int(reference)}).first():
            raise HTTPException(status_code=503, detail="Checkout is still being saved. Please retry.")
        return

    db.execute(text("SELECT id FROM lockers WHERE id = :id FOR UPDATE"), {"id": payment["locker_id"]})
    rental = db.execute(text("SELECT * FROM rentals WHERE id = :id"), {"id": payment["rental_id"]}).mappings().one()
    if (checkout.get("livemode") is not False or checkout.get("mode") != "payment"
            or checkout.get("currency") != "eur"
            or checkout.get("amount_total") != rental["total_price_cents"]
            or checkout.get("client_reference_id") != str(rental["id"])
            or checkout.get("metadata", {}).get("rental_id") != str(rental["id"])):
        raise HTTPException(status_code=409, detail="Payment details do not match the reservation.")

    if event:
        inserted = db.execute(text("""
            INSERT INTO payment_events (event_id, event_type, checkout_session_id)
            VALUES (:id, :type, :session) ON CONFLICT (event_id) DO NOTHING RETURNING event_id
        """), {"id": event["id"], "type": event["type"], "session": checkout["id"]}).first()
        if not inserted:
            db.commit()
            return

    if checkout.get("status") == "complete" and checkout.get("payment_status") == "paid":
        if rental["status"] == "cancelled" or not checkout.get("payment_intent"):
            raise HTTPException(status_code=409, detail="This payment needs review before activation.")
        db.execute(text("""
            UPDATE payments SET status = 'paid', payment_intent_id = :intent,
                confirmed_at = COALESCE(confirmed_at, CURRENT_TIMESTAMP)
            WHERE rental_id = :id
        """), {"id": rental["id"], "intent": checkout["payment_intent"]})
        db.execute(text("""
            UPDATE rentals SET status = CASE WHEN paid_until <= clock_timestamp()
                THEN 'overdue' ELSE 'active' END
            WHERE id = :id AND status = 'pending'
        """), {"id": rental["id"]})
    elif checkout.get("status") == "expired" and checkout.get("payment_status") == "unpaid":
        expired = db.execute(text("""
            UPDATE payments SET status = 'expired'
            WHERE rental_id = :id AND status != 'paid' RETURNING rental_id
        """), {"id": rental["id"]}).first()
        if expired:
            db.execute(text("UPDATE rentals SET status = 'cancelled' WHERE id = :id AND status = 'pending'"),
                       {"id": rental["id"]})
    db.commit()


def process_event(db: Session, event):
    if event["type"] not in CHECKOUT_EVENTS:
        return
    if db.execute(text("SELECT event_id FROM payment_events WHERE event_id = :id"), {"id": event["id"]}).first():
        return
    # Retrieve current state so delayed or reordered events cannot undo a payment.
    checkout = retrieve_checkout(event["data"]["object"]["id"])
    apply_checkout(db, checkout, event)


def reconcile_rental(db: Session, rental_id: int, user_id: int):
    owned_rental(db, rental_id, user_id)
    payment = db.execute(text("SELECT checkout_session_id FROM payments WHERE rental_id = :id"),
                         {"id": rental_id}).mappings().first()
    if payment and payment["checkout_session_id"]:
        apply_checkout(db, retrieve_checkout(payment["checkout_session_id"]))
    return owned_rental(db, rental_id, user_id)
