-- Initial PostgreSQL schema. Run once against an empty database.
BEGIN;
SET LOCAL TIME ZONE 'UTC';

CREATE TABLE users
(
    id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email         TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL CHECK (length(password_hash) > 0),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT normalised_email CHECK (email = lower(btrim(email)) AND email <> '')
);

CREATE TABLE sessions
(
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER     NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX sessions_user_id_idx ON sessions (user_id);

CREATE TABLE lockers
(
    id                  INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    number              INTEGER NOT NULL UNIQUE CHECK (number > 0),
    size                TEXT    NOT NULL CHECK (size IN ('small', 'large')
        ),
    price_per_day_cents INTEGER NOT NULL CHECK (price_per_day_cents >= 0),
    is_enabled          BOOLEAN NOT NULL DEFAULT FALSE,
    door_state          TEXT    NOT NULL DEFAULT 'unknown'
        CHECK (door_state IN ('open', 'closed', 'unknown')),
    last_seen_at        TIMESTAMPTZ
);

CREATE TABLE rentals
(
    id                        INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id                   INTEGER     NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    locker_id                 INTEGER     NOT NULL REFERENCES lockers (id) ON DELETE RESTRICT,
    status                    TEXT        NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'overdue', 'completed', 'cancelled')),
    prepaid_days              INTEGER     NOT NULL DEFAULT 1 CHECK (prepaid_days BETWEEN 1 AND 4),
    reserved_until            TIMESTAMPTZ NOT NULL,
    starts_at                 TIMESTAMPTZ NOT NULL,
    paid_until                TIMESTAMPTZ NOT NULL,
    completed_at              TIMESTAMPTZ,
    total_price_cents         INTEGER     NOT NULL CHECK (total_price_cents >= 0),
    overdue_daily_fee_cents   INTEGER     NOT NULL DEFAULT 100
        CHECK (overdue_daily_fee_cents = 100),
    overdue_terms_version     TEXT        NOT NULL CHECK (length(btrim(overdue_terms_version)) > 0),
    overdue_terms_accepted_at TIMESTAMPTZ NOT NULL,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT valid_consent_time CHECK (overdue_terms_accepted_at <= created_at),
    CONSTRAINT valid_reservation CHECK (reserved_until > created_at AND reserved_until <= paid_until),
    CONSTRAINT valid_rental_start CHECK (starts_at >= created_at),
    CONSTRAINT valid_paid_period CHECK (paid_until > starts_at),
    -- Each rental day ends at 18:00 in Ireland. The end is exclusive.
    -- Starts at or after 18:00 count the next date as day one.
    CONSTRAINT calendar_day_end CHECK (
        paid_until = (
            ((starts_at AT TIME ZONE 'Europe/Dublin'):: date
                 + prepaid_days - 1
                + CASE
                      WHEN (starts_at AT TIME ZONE 'Europe/Dublin'):: time >= TIME '18:00'
                          THEN 1
                      ELSE 0 END
                + TIME '18:00'
                )
                AT TIME ZONE 'Europe/Dublin'
            )
        ),
    CONSTRAINT completion_state CHECK (
        (status = 'completed' AND completed_at IS NOT NULL AND completed_at >= starts_at)
            OR (status <> 'completed' AND completed_at IS NULL)
        )
);

-- Active and overdue rentals block new bookings; expired unpaid holds are cancelled before reuse.
CREATE UNIQUE INDEX one_unfinished_rental_per_locker
    ON rentals (locker_id) WHERE status IN ('pending', 'active', 'overdue');
CREATE INDEX rentals_user_id_idx ON rentals (user_id);

-- Save the request before calling Stripe so retries cannot create a second checkout.
CREATE TABLE payments
(
    rental_id           INTEGER PRIMARY KEY REFERENCES rentals (id) ON DELETE RESTRICT,
    request_key         TEXT        NOT NULL UNIQUE,
    checkout_params     JSONB       NOT NULL,
    checkout_session_id TEXT UNIQUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- An amount due is not proof that Stripe has collected it.
CREATE TABLE overdue_charges
(
    id                       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rental_id                INTEGER     NOT NULL REFERENCES rentals (id) ON DELETE RESTRICT,
    charge_date              DATE        NOT NULL,
    amount_cents             INTEGER     NOT NULL DEFAULT 100 CHECK (amount_cents = 100),
    currency                 TEXT        NOT NULL DEFAULT 'eur' CHECK (currency = 'eur'),
    status                   TEXT        NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'paid', 'failed', 'waived')),
    stripe_payment_intent_id TEXT UNIQUE,
    paid_at                  TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT one_overdue_charge_per_day UNIQUE (rental_id, charge_date),
    CONSTRAINT paid_charge_details CHECK (
        (status = 'paid' AND paid_at IS NOT NULL AND stripe_payment_intent_id IS NOT NULL)
            OR (status <> 'paid' AND paid_at IS NULL)
        )
);

COMMIT;
