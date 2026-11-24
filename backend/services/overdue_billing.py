import asyncio
import json
import logging
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from uuid import uuid4

import stripe
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import engine
from services.booking_eligibility import lock_rental_account
from services.payments import stripe_client
from services.rentals import IRELAND, PAYMENT_METHOD_TERMS_VERSION

log = logging.getLogger(__name__)
WORKER_LOCK = 781493014
RETRY_WINDOW = timedelta(hours=23)


def due_dates(paid_until: datetime, now: datetime) -> Iterator[date]:
    day = paid_until.astimezone(IRELAND).date()
    while datetime.combine(day, time(18), tzinfo=IRELAND) <= now:
        yield day
        day += timedelta(days=1)


async def accrue_charges(db, rental_id):
    await lock_rental_account(db, rental_id)
    locker_id = await db.scalar(text("SELECT locker_id FROM rentals WHERE id=:id"), {"id": rental_id})
    await db.execute(text("SELECT id FROM lockers WHERE id=:id FOR UPDATE"), {"id": locker_id})
    rental = (await db.execute(text("""
                             SELECT r.*,
                                    p.stripe_customer_id,
                                    p.stripe_payment_method_id,
                                    p.payment_method_saved_at,
                                    p.status AS payment_status
                             FROM rentals r
                                      JOIN payments p ON p.rental_id = r.id
                             WHERE r.id = :id
                                    """), {"id": rental_id})).mappings().one()
    now = await db.scalar(text("SELECT clock_timestamp()"))
    if (rental["status"] not in ("active", "overdue") or rental["paid_until"] > now
            or rental["payment_status"] != "paid"
            or rental["overdue_terms_version"] != PAYMENT_METHOD_TERMS_VERSION
            or not rental["payment_method_saved_at"]
            or not rental["stripe_customer_id"] or not rental["stripe_payment_method_id"]):
        await db.commit()
        return
    await db.execute(text("UPDATE rentals SET status='overdue' WHERE id=:id AND status='active'"), {"id": rental_id})
    for day in due_dates(rental["paid_until"], now):
        request_key = str(uuid4())
        params = {
            "amount": 100, "currency": "eur", "customer": rental["stripe_customer_id"],
            "payment_method": rental["stripe_payment_method_id"],
            "payment_method_types": ["card"], "confirm": False,
            "metadata": {"rental_id": str(rental_id), "charge_date": day.isoformat(),
                         "overdue_request": request_key},
        }
        await db.execute(text("""
                        INSERT INTO overdue_charges(rental_id, charge_date, request_key, request_params)
                        VALUES (:rental, :day, :key, CAST(:params AS jsonb))
                        ON CONFLICT (rental_id,charge_date) DO NOTHING
                        """), {"rental": rental_id, "day": day, "key": request_key, "params": json.dumps(params)})
    await db.commit()


def validate_intent(intent, charge):
    params = charge["request_params"]
    payment_method = intent.get("payment_method")
    if payment_method is None and intent.get("status") == "requires_payment_method":
        # Stripe clears the selected method after a decline; verify the failed one.
        error = intent.get("last_payment_error") or {}
        payment_method = (error.get("payment_method") or {}).get("id")
    if (intent.get("livemode") is not False
            or not isinstance(intent.get("id"), str) or not intent["id"].startswith("pi_")
            or intent.get("amount") != 100 or intent.get("currency") != "eur"
            or intent.get("customer") != params["customer"]
            or payment_method != params["payment_method"]
            or intent.get("metadata") != params["metadata"]
            or (charge["stripe_payment_intent_id"] and intent["id"] != charge["stripe_payment_intent_id"])):
        raise ValueError("Overdue payment details do not match the saved request.")


async def save_status(db, charge_id, status, reason=None):
    rental_id = await db.scalar(text('SELECT rental_id FROM overdue_charges WHERE id=:id'), {'id': charge_id})
    await lock_rental_account(db, rental_id)
    await db.execute(text("""
                    UPDATE overdue_charges
                    SET status=:status,
                        failure_reason=:reason,
                        paid_at=CASE WHEN :status = 'paid' THEN COALESCE(paid_at, clock_timestamp()) END
                    WHERE id = :id
                      AND status = 'pending'
                    """), {"id": charge_id, "status": status, "reason": reason})
    await db.commit()


async def process_charge(db, charge_id, client):
    rental_id = await db.scalar(text('SELECT rental_id FROM overdue_charges WHERE id=:id'), {'id': charge_id})
    await lock_rental_account(db, rental_id)
    charge = (await db.execute(text("SELECT * FROM overdue_charges WHERE id=:id"),
                               {"id": charge_id})).mappings().one()
    if charge["status"] != "pending" or not charge["request_key"]:
        await db.commit()
        return
    # The worker's session-level advisory lock survives these durable commits.
    locker_id = await db.scalar(text("SELECT locker_id FROM rentals WHERE id=:id"), {"id": charge["rental_id"]})
    await db.execute(text("SELECT id FROM lockers WHERE id=:id FOR UPDATE"), {"id": locker_id})
    rental_status = await db.scalar(text("SELECT status FROM rentals WHERE id=:id"), {"id": charge["rental_id"]})
    now = await db.scalar(text("SELECT clock_timestamp()"))
    if not charge["stripe_payment_intent_id"]:
        if rental_status not in ("active", "overdue"):
            await save_status(db, charge_id, "waived", "Rental completed before collection of this charge.")
            return
        if charge["first_requested_at"] and now - charge["first_requested_at"] >= RETRY_WINDOW:
            await save_status(db, charge_id, "review", "Unconfirmed payment creation needs operator review.")
            return
        await db.execute(text("""
                        UPDATE overdue_charges
                        SET first_requested_at=COALESCE(first_requested_at, clock_timestamp())
                        WHERE id = :id
                        """), {"id": charge_id})
        await db.commit()
        # Creation never collects money. A lost response can safely reuse this key.
        intent = (await client.v1.payment_intents.create_async(
            charge["request_params"], options={"idempotency_key": charge["request_key"]}
        )).to_dict()
        validate_intent(intent, charge)
        await lock_rental_account(db, rental_id)
        await db.execute(text("UPDATE overdue_charges SET stripe_payment_intent_id=:intent WHERE id=:id"),
                   {"id": charge_id, "intent": intent["id"]})
        await db.commit()
    else:
        await db.commit()
        intent = (await client.v1.payment_intents.retrieve_async(charge["stripe_payment_intent_id"])).to_dict()
        validate_intent(intent, charge)

    # Collection completion takes the same locker lock. Recheck after the API call.
    await lock_rental_account(db, rental_id)
    await db.execute(text("SELECT id FROM lockers WHERE id=:id FOR UPDATE"), {"id": locker_id})
    if await db.scalar(text('SELECT status FROM overdue_charges WHERE id=:id'), {'id': charge_id}) != 'pending':
        await db.commit()
        return
    rental_status = await db.scalar(text("SELECT status FROM rentals WHERE id=:id"), {"id": charge["rental_id"]})
    if intent["status"] == "requires_confirmation":
        if rental_status not in ("active", "overdue"):
            await save_status(db, charge_id, "waived", "Rental completed before payment confirmation.")
            return
        intent = (await client.v1.payment_intents.confirm_async(
            intent["id"], {"off_session": True},
            options={"idempotency_key": charge["request_key"] + "-confirm"}
        )).to_dict()
        validate_intent(intent, charge)
    if intent["status"] == "succeeded":
        await save_status(db, charge_id, "paid")
    elif intent["status"] in ("requires_payment_method", "requires_action", "canceled"):
        await save_status(db, charge_id, "failed", "Payment needs renter or operator attention.")
    else:
        # A processing payment is reconciled on the next pass using the same ID.
        await db.commit()


async def run_billing_once(database=engine):
    client = stripe_client()  # Enforces a test-mode secret key.
    async with database.connect() as connection:
        try:
            acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_LOCK})
            await connection.commit()
            if not acquired:
                return
            try:
                async with AsyncSession(bind=connection) as db:
                    rentals = (await db.scalars(text("""
                                                     SELECT r.id
                                                     FROM rentals r
                                                              JOIN payments p ON p.rental_id = r.id
                                                     WHERE r.status IN ('active', 'overdue')
                                                       AND r.paid_until <= clock_timestamp()
                                                       AND p.status = 'paid'
                                                       AND p.payment_method_saved_at IS NOT NULL
                                                       AND r.overdue_terms_version = :version
                                                     ORDER BY r.id
                                                     """), {"version": PAYMENT_METHOD_TERMS_VERSION})).all()
                    for rental_id in rentals:
                        await accrue_charges(db, rental_id)
                    charges = (await db.scalars(text("""
                                                     SELECT id
                                                     FROM overdue_charges
                                                     WHERE status = 'pending'
                                                       AND request_key IS NOT NULL
                                                     ORDER BY charge_date, id
                                                     """))).all()
                    for charge_id in charges:
                        try:
                            await process_charge(db, charge_id, client)
                        except stripe.StripeError:
                            await db.rollback()
                            log.warning("Overdue charge %s awaits Stripe reconciliation.", charge_id)
                        except ValueError:
                            await db.rollback()
                            await save_status(db, charge_id, "review", "Payment verification needs operator review.")
                            log.error("Overdue charge %s failed verification.", charge_id)
            finally:
                await connection.rollback()
                await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK})
                await connection.commit()
        except BaseException:
            # A cancelled lock query may have succeeded on PostgreSQL. Discard
            # the connection rather than return a session-level lock to the pool.
            await connection.invalidate()
            raise


async def billing_loop(stopped):
    while not stopped.is_set():
        try:
            await run_billing_once()
        except Exception as error:
            log.error("Overdue billing pass failed (%s).", type(error).__name__)
        try:
            await asyncio.wait_for(stopped.wait(), timeout=60)
        except TimeoutError:
            pass


@asynccontextmanager
async def billing_worker(enabled):
    if not enabled:
        yield
        return
    stripe_client()
    stopped = asyncio.Event()
    task = asyncio.create_task(billing_loop(stopped))
    try:
        yield
    finally:
        stopped.set()
        await task
