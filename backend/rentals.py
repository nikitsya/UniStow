from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

IRELAND = ZoneInfo("Europe/Dublin")
TERMS_VERSION = "2026-10-25"


def rental_end(starts_at: datetime, days: int) -> datetime:
    local_start = starts_at.astimezone(IRELAND)
    final_date = local_start.date() + timedelta(
        days=days - 1 + (local_start.hour >= 18)
    )
    return datetime.combine(final_date, time(18), tzinfo=IRELAND)


def reserve_locker(db: Session, user_id: int, locker_id: int, days: int):
    # Lock the locker row so simultaneous requests cannot both reserve it.
    locker = db.execute(text("""
                             SELECT id, is_enabled, price_per_day_cents
                             FROM lockers
                             WHERE id = :id FOR UPDATE
                             """), {"id": locker_id}).mappings().first()
    if locker is None:
        raise HTTPException(status_code=404, detail="Locker not found.")
    if not locker["is_enabled"]:
        raise HTTPException(status_code=409, detail="This locker is unavailable.")

    now = db.execute(text("SELECT clock_timestamp()")).scalar_one()
    # There is no payment integration yet. Reconcile payments here when adding Stripe.
    db.execute(text("""
                    UPDATE rentals
                    SET status = 'cancelled'
                    WHERE locker_id = :locker_id
                      AND status = 'pending'
                      AND reserved_until <= :now
                    """), {"locker_id": locker_id, "now": now})
    existing = db.execute(text("""
                               SELECT id,
                                      user_id,
                                      locker_id,
                                      status,
                                      prepaid_days,
                                      total_price_cents,
                                      reserved_until,
                                      paid_until
                               FROM rentals
                               WHERE locker_id = :locker_id
                                 AND status IN ('pending', 'active', 'overdue')
                               """), {"locker_id": locker_id}).mappings().first()
    if existing:
        if existing["user_id"] == user_id and existing["status"] == "pending" and existing["prepaid_days"] == days:
            db.commit()
            return dict(existing)
        raise HTTPException(status_code=409, detail="This locker is already reserved or in use.")

    paid_until = rental_end(now, days)
    reserved_until = min(now + timedelta(minutes=15), paid_until)
    rental = db.execute(text("""
                             INSERT INTO rentals (user_id, locker_id, status, prepaid_days, reserved_until, starts_at,
                                                  paid_until, total_price_cents, overdue_terms_version,
                                                  overdue_terms_accepted_at, created_at)
                             VALUES (:user_id, :locker_id, 'pending', :days, :reserved_until, :now,
                                     :paid_until, :total, :terms, :now, :now) RETURNING id, user_id, locker_id, status, prepaid_days, total_price_cents,
                  reserved_until, paid_until
                             """), {"user_id": user_id, "locker_id": locker_id, "days": days,
                                    "reserved_until": reserved_until, "now": now, "paid_until": paid_until,
                                    "total": locker["price_per_day_cents"] * days,
                                    "terms": TERMS_VERSION}).mappings().one()
    db.commit()
    return dict(rental)
