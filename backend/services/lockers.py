from datetime import timedelta

from sqlalchemy import and_, column, exists, func, or_, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from models import Locker

# These tables remain SQL expressions until their ORM migration.
rentals = table("rentals", column("id"), column("locker_id"), column("status"), column("reserved_until"))
payments = table("payments", column("rental_id"))


async def read_lockers(db: AsyncSession, locker_id: int | None = None):
    has_payment = exists(select(payments.c.rental_id).where(payments.c.rental_id == rentals.c.id))
    occupied = exists(select(rentals.c.id).where(
        rentals.c.locker_id == Locker.id,
        or_(
            rentals.c.status.in_(("active", "overdue")),
            and_(
                rentals.c.status == "pending",
                or_(rentals.c.reserved_until > func.current_timestamp(), has_payment),
            ),
        ),
    ))
    online = and_(
        Locker.last_seen_at > func.current_timestamp() - timedelta(seconds=15),
        Locker.last_seen_at <= func.current_timestamp(),
    )
    query = select(Locker, online.label("device_online"),
                   and_(Locker.is_enabled, ~occupied).label("is_available"))
    if locker_id is not None:
        query = query.where(Locker.id == locker_id)
    rows = (await db.execute(query.order_by(Locker.number))).all()
    return [
        {
            "id": locker.id,
            "number": locker.number,
            "name": f"Locker {locker.number}",
            "size": locker.size.capitalize(),
            "price_per_day_cents": locker.price_per_day_cents,
            "status": ("offline" if locker.is_enabled and not device_online
                       else "available" if is_available else "unavailable"),
            "door_state": locker.door_state,
            "last_seen_at": locker.last_seen_at,
        }
        for locker, device_online, is_available in rows
    ]
