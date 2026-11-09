from sqlalchemy import text
from sqlalchemy.orm import Session

LOCKER_QUERY = """
               SELECT lockers.id,
                      lockers.number,
                      lockers.size,
                      lockers.price_per_day_cents,
                      lockers.door_state,
                      lockers.last_seen_at,
                      lockers.is_enabled,
                      (lockers.last_seen_at > CURRENT_TIMESTAMP - INTERVAL '15 seconds'
                          AND lockers.last_seen_at <= CURRENT_TIMESTAMP) AS device_online,
                      lockers.is_enabled AND NOT EXISTS (SELECT 1
                                                         FROM rentals
                                                         WHERE rentals.locker_id = lockers.id
                                                           AND (rentals.status IN ('active', 'overdue')
                                                             OR (rentals.status = 'pending'
                                                                 AND (rentals.reserved_until > CURRENT_TIMESTAMP
                                                                     OR EXISTS (SELECT 1
                                                                                FROM payments
                                                                                WHERE payments.rental_id = rentals.id))))) AS is_available
               FROM lockers \
               """


def read_lockers(db: Session, locker_id: int | None = None):
    query = LOCKER_QUERY
    parameters = {}
    if locker_id is not None:
        query += " WHERE lockers.id = :locker_id"
        parameters["locker_id"] = locker_id
    query += " ORDER BY lockers.number"
    rows = db.execute(text(query), parameters).mappings().all()
    return [
        {
            "id": row["id"],
            "number": row["number"],
            "name": f"Locker {row['number']}",
            "size": row["size"].capitalize(),
            "price_per_day_cents": row["price_per_day_cents"],
            "status": ("offline" if row["is_enabled"] and not row["device_online"]
                       else "available" if row["is_available"] else "unavailable"),
            "door_state": row["door_state"],
            "last_seen_at": row["last_seen_at"],
        }
        for row in rows
    ]
