from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text


def request_open(db, rental_id, user_id, action="open"):
    rental = db.execute(text('SELECT * FROM rentals WHERE id=:id AND user_id=:user'),
                        {'id': rental_id, 'user': user_id}).mappings().first()
    if rental is None:
        raise HTTPException(404, 'Rental not found.')
    locker = db.execute(text('SELECT * FROM lockers WHERE id=:id FOR UPDATE'),
                        {'id': rental['locker_id']}).mappings().one()
    now = db.execute(text('SELECT clock_timestamp()')).scalar_one()
    valid = db.execute(text("""
        SELECT 1 FROM rentals r JOIN payments p ON p.rental_id=r.id
        WHERE r.id=:id AND r.user_id=:user AND r.status='active'
          AND p.status='paid' AND r.starts_at<=:now AND r.paid_until>:now
    """), {'id': rental_id, 'user': user_id, 'now': now}).scalar_one_or_none()
    if not valid:
        raise HTTPException(409, 'Opening requires an active, paid rental within its paid period.')
    if locker['number'] != 4 or not locker['is_enabled']:
        raise HTTPException(409, 'This locker cannot be opened remotely.')
    if (not locker['device_ready'] or not locker['controller_id']
            or locker['last_seen_at'] is None
            or not 0 <= (now - locker['last_seen_at']).total_seconds() < 15):
        raise HTTPException(409, 'The locker controller is offline or busy. Refresh and try again.')
    if locker['door_state'] != 'closed':
        raise HTTPException(409, 'The door is already open or its state is unknown.')
    recent = db.execute(text('SELECT 1 FROM locker_commands WHERE locker_id=:id AND expires_at>:now LIMIT 1'),
                        {'id': locker['id'], 'now': now}).first()
    if recent:
        raise HTTPException(409, 'An opening request was already sent. Wait before trying again.')
    command_id = str(uuid4())
    db.execute(text('''
                    INSERT INTO locker_commands(id, rental_id, locker_id, controller_id, created_at, expires_at, action)
                    VALUES (:id, :rental, :locker, :controller, :now, :expires, :action)
    '''), {'id': command_id, 'rental': rental_id, 'locker': locker['id'],
           'controller': locker['controller_id'], 'now': now, 'action': action,
          'expires': min(now + timedelta(seconds=10), rental['paid_until'])})
    db.commit()
    return {'status': 'queued', 'command_id': command_id}


def take_command(db, controller_id, supports_finish=False):
    locker = db.execute(text('SELECT * FROM lockers WHERE number=4 FOR UPDATE')).mappings().first()
    now = db.execute(text('SELECT clock_timestamp()')).scalar_one()
    if (not locker or not locker['is_enabled'] or not locker['device_ready']
            or locker['controller_id'] != controller_id or locker['door_state'] != 'closed'
            or locker['last_seen_at'] is None
            or not 0 <= (now - locker['last_seen_at']).total_seconds() < 15):
        db.commit()
        return {'command': None}
    command = db.execute(text('''
                              SELECT c.id, c.expires_at, c.action
                              FROM locker_commands c
        JOIN rentals r ON r.id=c.rental_id JOIN payments p ON p.rental_id=r.id
        WHERE c.locker_id=:locker AND c.controller_id=:controller
          AND (c.action = 'open' OR :supports_finish)
          AND c.dispatched_at IS NULL AND c.expires_at>:now
          AND r.status='active' AND p.status='paid' AND r.starts_at<=:now AND r.paid_until>:now
        ORDER BY c.created_at LIMIT 1
                              '''), {'locker': locker['id'], 'controller': controller_id, 'now': now,
                                     'supports_finish': supports_finish}).mappings().first()
    if not command:
        db.commit()
        return {'command': None}
    # At-most-once dispatch: an uncertain delivery is never replayed automatically.
    db.execute(text('UPDATE locker_commands SET dispatched_at=:now WHERE id=:id'),
               {'id': command['id'], 'now': now})
    db.execute(text('UPDATE lockers SET device_ready=FALSE WHERE id=:id'), {'id': locker['id']})
    db.commit()
    return {'command': {'id': command['id'], 'action': command['action'],
                        'valid_for_seconds': max(0, (command['expires_at'] - now).total_seconds())}}


def complete_collection(db, command_id, controller_id):
    # Match the acknowledgement to the dispatched collection, never just a closed door.
    db.execute(text('SELECT id FROM lockers WHERE number=4 FOR UPDATE')).first()
    command = db.execute(text("""
                              SELECT *
                              FROM locker_commands
                              WHERE id = :id
                                AND controller_id = :controller
                                AND action ='finish'
                                AND dispatched_at IS NOT NULL FOR
                              UPDATE
                              """), {'id': command_id, 'controller': controller_id}).mappings().first()
    if command is None:
        raise HTTPException(409, 'No matching collection command.')
    if command['completed_at'] is None:
        db.execute(text("""
                        UPDATE rentals
                        SET status='completed',
                            completed_at=clock_timestamp()
                        WHERE id = :id
                          AND status IN ('active', 'overdue')
                        """), {'id': command['rental_id']})
        db.execute(text('UPDATE locker_commands SET completed_at=clock_timestamp() WHERE id=:id'),
                   {'id': command_id})
    db.commit()
    return {'status': 'completed'}
