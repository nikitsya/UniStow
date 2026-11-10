BEGIN;

CREATE TABLE IF NOT EXISTS overdue_payments
(
    id                  TEXT PRIMARY KEY,
    rental_id           INTEGER     NOT NULL REFERENCES rentals (id),
    charge_ids          JSONB       NOT NULL,
    amount_cents        INTEGER     NOT NULL CHECK (amount_cents > 0),
    checkout_params     JSONB       NOT NULL,
    checkout_session_id TEXT UNIQUE,
    payment_intent_id   TEXT UNIQUE,
    status              TEXT        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'expired')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at             TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_overdue_payment ON overdue_payments (rental_id)
    WHERE status = 'pending';

COMMIT;
