from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import DateTime, and_, func, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import Locker, LockerCommand, Payment, Rental


async def authorised_locker(db: AsyncSession, rental_id, user_id, *, action="open"):
    rental = (await db.execute(select(*Rental.__table__.columns).where(
        Rental.id == rental_id, Rental.user_id == user_id,
    ))).mappings().first()
    if rental is None:
        raise HTTPException(404, 'Rental not found.')
    locker = (await db.execute(select(*Locker.__table__.columns).where(
        Locker.id == rental['locker_id'],
    ).with_for_update())).mappings().one()
    now: datetime = (await db.execute(
        select(func.clock_timestamp(type_=DateTime(timezone=True)))
    )).scalar_one()
    valid = await db.scalar(select(Rental.id).join(Payment, Payment.rental_id == Rental.id).where(
        Rental.id == rental_id,
        Rental.user_id == user_id,
        or_(Rental.status == 'active', and_(literal(action == 'finish'), Rental.status == 'overdue')),
        Payment.status == 'paid',
        Rental.starts_at <= now,
        or_(literal(action == 'finish'), Rental.paid_until > now),
    ))
    if not valid:
        if action == 'finish':
            raise HTTPException(409, 'Collection requires an unfinished, paid rental.')
        raise HTTPException(409, 'Opening requires an active, paid rental within its paid period.')
    if locker['number'] != 4 or not locker['is_enabled']:
        raise HTTPException(409, 'This locker cannot be opened remotely.')
    return locker, rental, now


async def request_open(db: AsyncSession, rental_id, user_id, action="open"):
    locker, rental, now = await authorised_locker(db, rental_id, user_id, action=action)
    if (not locker['device_ready'] or not locker['controller_id']
            or locker['last_seen_at'] is None
            or not 0 <= (now - locker['last_seen_at']).total_seconds() < 15):
        raise HTTPException(409, 'The locker controller is offline or busy. Refresh and try again.')
    if locker['door_state'] not in ('open', 'closed') or (action == 'finish' and locker['door_state'] != 'closed'):
        raise HTTPException(409, 'The door is already open or its state is unknown.')
    command_id, _ = await record_command(db, locker, rental, now, locker['controller_id'], action)
    return {'status': 'queued', 'command_id': command_id}


async def record_command(db: AsyncSession, locker, rental, now: datetime, controller_id, action, *, dispatched=False):
    # The caller holds the locker row lock until this transaction commits.
    recent = await db.scalar(select(LockerCommand.id).where(
        LockerCommand.locker_id == locker['id'], LockerCommand.expires_at > now,
    ).limit(1))
    if recent:
        raise HTTPException(409, 'An opening request was already sent. Wait before trying again.')
    command_id = str(uuid4())
    expiry = now + timedelta(seconds=10)
    if action != 'finish':
        expiry = min(expiry, rental['paid_until'])
    db.add(LockerCommand(
        id=command_id, rental_id=rental['id'], locker_id=locker['id'],
        controller_id=controller_id, created_at=now, expires_at=expiry,
        action=action, dispatched_at=now if dispatched else None,
    ))
    await db.commit()
    return command_id, expiry.timestamp()


async def take_command(db: AsyncSession, controller_id, supports_finish=False):
    locker = (await db.execute(select(*Locker.__table__.columns).where(
        Locker.number == 4,
    ).with_for_update())).mappings().first()
    now: datetime = (await db.execute(
        select(func.clock_timestamp(type_=DateTime(timezone=True)))
    )).scalar_one()
    if (not locker or not locker['is_enabled'] or not locker['device_ready']
            or locker['controller_id'] != controller_id or locker['door_state'] not in ('open', 'closed')
            or locker['last_seen_at'] is None
            or not 0 <= (now - locker['last_seen_at']).total_seconds() < 15):
        await db.commit()
        return {'command': None}
    command = (await db.execute(
        select(LockerCommand.id, LockerCommand.expires_at, LockerCommand.action)
        .join(Rental, Rental.id == LockerCommand.rental_id)
        .join(Payment, Payment.rental_id == Rental.id)
        .where(
            LockerCommand.locker_id == locker['id'],
            LockerCommand.controller_id == controller_id,
            or_(LockerCommand.action == 'open', literal(supports_finish and locker['door_state'] == 'closed')),
            LockerCommand.dispatched_at.is_(None),
            LockerCommand.expires_at > now,
            or_(Rental.status == 'active',
                and_(LockerCommand.action == 'finish', Rental.status == 'overdue')),
            Payment.status == 'paid',
            Rental.starts_at <= now,
            or_(LockerCommand.action == 'finish', Rental.paid_until > now),
        ).order_by(LockerCommand.created_at).limit(1)
    )).mappings().first()
    if not command:
        await db.commit()
        return {'command': None}
    # At-most-once dispatch: an uncertain delivery is never replayed automatically.
    await db.execute(update(LockerCommand).where(LockerCommand.id == command['id']).values(dispatched_at=now))
    await db.execute(update(Locker).where(Locker.id == locker['id']).values(device_ready=False))
    await db.commit()
    return {'command': {'id': command['id'], 'action': command['action'],
                        'valid_for_seconds': max(0, (command['expires_at'] - now).total_seconds())}}


async def complete_collection(db: AsyncSession, command_id, controller_id):
    # Match the acknowledgement to the dispatched collection, never just a closed door.
    await db.execute(select(Locker.id).where(Locker.number == 4).with_for_update())
    command = (await db.execute(select(*LockerCommand.__table__.columns).where(
        LockerCommand.id == command_id,
        LockerCommand.controller_id == controller_id,
        LockerCommand.action == 'finish',
        LockerCommand.dispatched_at.is_not(None),
    ).with_for_update())).mappings().first()
    if command is None:
        raise HTTPException(409, 'No matching collection command.')
    if command['completed_at'] is None:
        await db.execute(update(Rental).where(
            Rental.id == command['rental_id'], Rental.status.in_(('active', 'overdue')),
        ).values(status='completed', completed_at=func.clock_timestamp()))
        await db.execute(update(LockerCommand).where(LockerCommand.id == command_id).values(
            completed_at=func.clock_timestamp(),
        ))
    await db.commit()
    return {'status': 'completed'}
