from typing import TypedDict

from fastapi import HTTPException
from sqlalchemy import and_, exists, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Locker, OverdueCharge, OverduePayment, Rental, User


class BookingEligibility(TypedDict):
    can_book: bool
    unpaid_cents: int
    processing_cents: int
    rentals: list[dict[str, int]]


async def lock_account(db: AsyncSession, user_id):
    # All booking and balance writes take the account lock before any locker lock.
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())


async def lock_rental_account(db: AsyncSession, rental_id):
    user_id = await db.scalar(select(Rental.user_id).where(Rental.id == rental_id))
    await lock_account(db, user_id)


async def booking_eligibility(db: AsyncSession, user_id) -> BookingEligibility:
    recovery_pending = exists(select(OverduePayment.id).where(
        OverduePayment.rental_id == Rental.id,
        OverduePayment.status == 'pending',
        OverduePayment.charge_ids.contains(func.jsonb_build_array(OverdueCharge.id)),
    ).correlate(Rental, OverdueCharge))
    unpaid_total = func.coalesce(func.sum(OverdueCharge.amount_cents).filter(
        and_(OverdueCharge.status == 'failed', not_(recovery_pending)),
    ), 0)
    processing_total = func.coalesce(func.sum(OverdueCharge.amount_cents).filter(
        or_(OverdueCharge.status.in_(('pending', 'review')), recovery_pending),
    ), 0)
    rows = (await db.execute(
        select(Rental.id, Locker.number, unpaid_total.label('unpaid_cents'),
               processing_total.label('processing_cents'))
        .join(Locker, Locker.id == Rental.locker_id)
        .join(OverdueCharge, OverdueCharge.rental_id == Rental.id)
        .where(
            Rental.user_id == user_id,
            Rental.status.in_(('active', 'overdue', 'completed')),
            OverdueCharge.status.in_(('failed', 'pending', 'review')),
        ).group_by(Rental.id, Locker.number).order_by(Rental.id)
    )).mappings().all() if user_id is not None else []
    unpaid = sum(row['unpaid_cents'] for row in rows)
    processing = sum(row['processing_cents'] for row in rows)
    return {'can_book': not (unpaid or processing), 'unpaid_cents': unpaid,
            'processing_cents': processing, 'rentals': [dict(row) for row in rows]}


async def require_booking_eligibility(db: AsyncSession, user_id):
    await lock_account(db, user_id)
    eligibility = await booking_eligibility(db, user_id)
    if not eligibility['can_book']:
        message = (f"Please settle your unpaid rental balance of €{eligibility['unpaid_cents'] / 100:.2f} "
                   'before starting a new rental.' if eligibility['unpaid_cents'] else
                   'Your rental payment is awaiting confirmation. New rentals will be available '
                   'once your balance is confirmed as settled.')
        raise HTTPException(409, {'code': 'account_booking_restricted', 'message': message,
                                  'rentals_url': '/rentals#account-balance', **eligibility})
