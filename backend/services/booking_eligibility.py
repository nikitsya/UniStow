from fastapi import HTTPException
from sqlalchemy import text


def lock_account(db, user_id):
    # All booking and balance writes take the account lock before any locker lock.
    db.execute(text('SELECT id FROM users WHERE id=:id FOR UPDATE'), {'id': user_id})


def lock_rental_account(db, rental_id):
    user_id = db.scalar(text('SELECT user_id FROM rentals WHERE id=:id'), {'id': rental_id})
    lock_account(db, user_id)


def booking_eligibility(db, user_id):
    rows = db.execute(text("""
        SELECT r.id, l.number,
               COALESCE(SUM(c.amount_cents) FILTER (
                   WHERE c.status = 'failed' AND NOT EXISTS (
                       SELECT 1 FROM overdue_payments p
                       WHERE p.rental_id = r.id AND p.status = 'pending'
                         AND p.charge_ids @> jsonb_build_array(c.id)
                   )), 0) AS unpaid_cents,
               COALESCE(SUM(c.amount_cents) FILTER (
                   WHERE c.status IN ('pending', 'review') OR EXISTS (
                       SELECT 1 FROM overdue_payments p
                       WHERE p.rental_id = r.id AND p.status = 'pending'
                         AND p.charge_ids @> jsonb_build_array(c.id)
                   )), 0) AS processing_cents
        FROM rentals r
        JOIN lockers l ON l.id = r.locker_id
        JOIN overdue_charges c ON c.rental_id = r.id
        WHERE r.user_id = :user_id AND r.status IN ('active', 'overdue', 'completed')
          AND c.status IN ('failed', 'pending', 'review')
        GROUP BY r.id, l.number
        ORDER BY r.id
    """), {'user_id': user_id}).mappings().all() if user_id is not None else []
    unpaid = sum(row['unpaid_cents'] for row in rows)
    processing = sum(row['processing_cents'] for row in rows)
    return {'can_book': not (unpaid or processing), 'unpaid_cents': unpaid,
            'processing_cents': processing, 'rentals': [dict(row) for row in rows]}


def require_booking_eligibility(db, user_id):
    lock_account(db, user_id)
    eligibility = booking_eligibility(db, user_id)
    if not eligibility['can_book']:
        message = (f"Please settle your unpaid rental balance of €{eligibility['unpaid_cents'] / 100:.2f} "
                   'before starting a new rental.' if eligibility['unpaid_cents'] else
                   'Your rental payment is awaiting confirmation. New rentals will be available '
                   'once your balance is confirmed as settled.')
        raise HTTPException(409, {'code': 'account_booking_restricted', 'message': message,
                                  'rentals_url': '/rentals#account-balance', **eligibility})
