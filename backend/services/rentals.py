from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import DateTime, column, exists, func, select, table, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import Locker, Rental
from services.booking_eligibility import require_booking_eligibility

IRELAND = ZoneInfo("Europe/Dublin")
TERMS_VERSION = "2026-10-25"
PAYMENT_METHOD_TERMS_VERSION = "2026-11-09"


def rental_end(starts_at: datetime, days: int) -> datetime:
    local_start = starts_at.astimezone(IRELAND)
    final_date = local_start.date() + timedelta(
        days=days - 1 + (local_start.hour >= 18)
    )
    return datetime.combine(final_date, time(18), tzinfo=IRELAND)


async def reserve_locker(db: AsyncSession, user_id: int, locker_id: int, days: int,
                         payment_method_consent: bool = False):
    await require_booking_eligibility(db, user_id)
    # Lock the locker row so simultaneous requests cannot both reserve it.
    locker = await db.scalar(
        select(Locker).where(Locker.id == locker_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if locker is None:
        raise HTTPException(status_code=404, detail="Locker not found.")
    if not locker.is_enabled:
        raise HTTPException(status_code=409, detail="This locker is unavailable.")

    now: datetime = (await db.execute(
        select(func.clock_timestamp(type_=DateTime(timezone=True)))
    )).scalar_one()
    # Never release a checkout until its payment status has been reconciled.
    payments = table("payments", column("rental_id"))
    has_payment = exists(select(payments.c.rental_id).where(payments.c.rental_id == Rental.id))
    await db.execute(
        update(Rental).where(
            Rental.locker_id == locker_id,
            Rental.status == "pending",
            Rental.reserved_until <= now,
            ~has_payment,
        ).values(status="cancelled").execution_options(synchronize_session="fetch")
    )
    existing = await db.scalar(select(Rental).where(
        Rental.locker_id == locker_id,
        Rental.status.in_(("pending", "active", "overdue")),
    ).execution_options(populate_existing=True))
    if existing:
        if existing.user_id == user_id and existing.status == "pending" and existing.prepaid_days == days:
            result = rental_details(existing)
            await db.commit()
            return result
        raise HTTPException(status_code=409, detail="This locker is already reserved or in use.")

    if (locker.last_seen_at is None
            or not 0 <= (now - locker.last_seen_at).total_seconds() < 15):
        raise HTTPException(status_code=409, detail="This locker is temporarily unavailable.")

    paid_until = rental_end(now, days)
    reserved_until = min(now + timedelta(minutes=15), paid_until)
    rental = Rental(
        user_id=user_id, locker_id=locker_id, status="pending", prepaid_days=days,
        reserved_until=reserved_until, starts_at=now, paid_until=paid_until,
        total_price_cents=locker.price_per_day_cents * days,
        overdue_terms_version=PAYMENT_METHOD_TERMS_VERSION if payment_method_consent else TERMS_VERSION,
        overdue_terms_accepted_at=now, created_at=now,
    )
    db.add(rental)
    await db.flush()
    result = rental_details(rental)
    await db.commit()
    return result


def rental_details(rental: Rental):
    return {
        "id": rental.id,
        "user_id": rental.user_id,
        "locker_id": rental.locker_id,
        "status": rental.status,
        "prepaid_days": rental.prepaid_days,
        "total_price_cents": rental.total_price_cents,
        "reserved_until": rental.reserved_until,
        "paid_until": rental.paid_until,
    }
