# Database

UniStow uses PostgreSQL 16. [schema.sql](../backend/database/schema.sql) defines the current tables.

| Table             | Purpose                                                                                |
|-------------------|----------------------------------------------------------------------------------------|
| `users`           | Normalised, unique email addresses and Argon2 password hashes                          |
| `sessions`        | Hashed session tokens, user references and expiry times                                |
| `lockers`         | Number, size, daily price, enabled flag, door state and controller readiness           |
| `rentals`         | Owner, locker, booked dates, price, status and accepted overdue terms                  |
| `payments`        | One Stripe checkout request per rental, fixed parameters, retry key and payment status |
| `payment_events`  | Processed Stripe event IDs to prevent duplicate handling                               |
| `locker_commands` | Opening/collection commands, controller ID, expiry, dispatch and completion times      |
| `overdue_charges` | Daily charge records, durable Stripe requests and collection status                    |

A user and a locker can each have many past rentals. A unique index permits only one
unfinished (`pending`, `active` or `overdue`) rental per locker. Foreign keys preserve
rental history. Payment records contain Stripe references, not card details.

## Rental rules

- Lockers are numbered 1–9. Only Locker 4 is enabled; it costs 100 cents per day.
- Rent for 1–4 calendar days, with one day selected by default.
- The server calculates `starts_at` and `paid_until` when reserving. Payment confirmation
  enables access without extending the booked deadline.
- Before 18:00 in `Europe/Dublin`, the reservation date is day one. At or after 18:00,
  the next date is day one. `paid_until` is the exclusive 18:00 deadline on the last paid day.
- `reserved_until` is 15 minutes after reservation, or the rental deadline if sooner.
  Expired holds without a checkout can be released; they are marked `cancelled` when
  another reservation is made. A saved checkout must be reconciled before release.
- Stripe checkout currently expires 31 minutes after its creation. This differs from
  the initial reservation hold. Repeated payment requests reuse the saved checkout.
- Completion requires the collection cycle and controller acknowledgement. Expiry alone
  does not free an occupied locker.

Amounts use integer cents. Timestamps use `timestamptz` to represent instants;
calendar-day calculations and user-facing times use `Europe/Dublin`, including clock changes.
A database viewer may display timestamps in its own session time zone.

## Device state

`door_state` is `open`, `closed` or `unknown`. `last_seen_at`, `device_ready` and
`controller_id` identify the current controller and whether it can accept a command.
A report older than 15 seconds is treated as offline. The sensor measures the door,
not the latch position. Commands are stored in `locker_commands`; there is no separate
`device_events` table in the current schema.

## Overdue charges

The accepted terms specify EUR 1 at the unpaid deadline and each following local 18:00
until collection and completion. The server saves the terms version and consent time.
`overdue_charges` allows one record per rental and local charge date, with statuses
`pending`, `paid`, `failed` or `waived`.

New reservations that explicitly authorise payment-method storage use terms version
`2026-11-09`. The checkbox starts unchecked and its agreement covers Stripe storage and the agreed
EUR 1 charges for that rental. The server records the version and acceptance time.
Requests without payment-method consent retain version `2026-10-25`.

For new consenting reservations, Checkout creates a Stripe Customer and requests
`setup_future_usage=off_session`. After a verified test payment, the server checks
the PaymentIntent and its attached card PaymentMethod, then saves
`stripe_customer_id`, `stripe_payment_method_id` and `payment_method_saved_at`
on the rental's payment record. Card details stay in Stripe. Each rental has its
own Customer created by Checkout. Retries reuse the saved Checkout parameters
and idempotency key.

Existing reservations and Checkout requests retain their original consent and
parameters. To exercise the new flow, create a new reservation and accept the
updated agreement. Daily test billing is available as an opt-in worker described below.

## Initial setup

Docker runs `schema.sql` only when initialising an empty database volume. It creates
tables without inserting lockers. For a new database, run this from the project folder:

```sh
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < backend/database/migrations/001_add_lockers.sql
```

This adds nine lockers with only Locker 4 enabled. Repeating it leaves existing records
unchanged. It does not reset availability or change existing locker settings.

Apply `002_save_payment_method.sql` to an existing database before starting the
updated application. From the project folder, for the local Docker database:

```sh
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < backend/database/migrations/002_save_payment_method.sql
```

For EC2, use `docker compose --env-file .env.production -f compose.production.yaml`
in the same command. Back up the database first. The migration adds nullable
columns and supports repeated execution.

Migrations are manual; restarting Docker does not apply SQL changes. Device and
payment tables are already in the current schema. An older database needs a migration based on its existing
structure; do not rerun the initial schema or delete its volume to update it.

## Saved payment method verification

On 9 November 2026, all 70 automated backend tests passed against local
PostgreSQL. Stripe responses were mocked. Checks cover consent-gated Checkout
parameters, verified method storage, duplicate events, lookup failure and retry,
a mismatched Customer, and legacy single-payment behaviour.

A real Stripe test-mode Checkout is still pending. Create a new reservation with
the updated agreement, complete Checkout using a Stripe test card and verify that
the payment record contains the Customer ID, PaymentMethod ID and saved timestamp.
Confirm that the rental activates and that a repeated reconciliation preserves
the timestamp. Never include card details or secret keys in test evidence.

See [Stripe's documentation on saving payment details during payment](https://docs.stripe.com/payments/checkout/save-during-payment).

## Daily test billing

The application starts a background worker when `UNISTOW_OVERDUE_BILLING=1`.
It runs immediately and checks again every 60 seconds. Eligible rentals have
confirmed payment, the new storage consent and a verified saved payment method.
The first EUR 1 charge becomes due at `paid_until`, then at each following 18:00
in Europe/Dublin. The worker uses the database clock and local calendar dates,
including daylight saving changes. Active eligible rentals become overdue.

After a restart, the worker creates missing daily records for still-unfinished
rentals. Several missed days can therefore produce several EUR 1 test charges
in one pass. Completed rentals accrue no new records. Pending requests which
have not been confirmed are waived when the rental has completed. Previously
submitted payments are reconciled by their existing Stripe IDs.

### Duplicate protection

- The database permits one charge per rental and local date.
- A PostgreSQL advisory lock permits one billing worker at a time across processes.
- Every charge has an immutable request payload and UUID idempotency key, saved
  before the Stripe call.
- The worker creates an unconfirmed PaymentIntent, saves its ID durably, then
  confirms that same intent off-session.
- A lost confirmation response is resolved by retrieving that intent. The
  worker validates its amount, currency, Customer, PaymentMethod, metadata and
  test-mode flag before recording success.
- Creation with an unknown outcome is retried with its saved key for up to
  23 hours. After that it requires operator review. Stripe can expire idempotency
  keys after 24 hours.
- Confirmation and controller-reported completion take the same locker row lock.
  The worker checks the rental status again immediately before confirming.
- Declines and requests for additional authentication become failed records.
  Processing payments remain pending until Stripe confirms their outcome.

Existing charge rows without the new request fields require operator review.
The worker selects new requests carrying their own idempotency key.

### Enable the worker

Back up the database and apply migration `003_overdue_billing_requests.sql`
after `002_save_payment_method.sql`, before enabling the worker. Locally:

```sh
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < backend/database/migrations/003_overdue_billing_requests.sql
```

On EC2, use the production Compose file and environment file as for migration 002.
Set `UNISTOW_OVERDUE_BILLING=1` in the application's environment and restart the
application. Production Compose passes this variable through, defaulting to 0.
The worker accepts only a Stripe test secret key. Setting the flag to 0 and
restarting the application disables the worker.

### Verification on 9 November 2026

All 86 backend tests passed with local PostgreSQL. Billing tests use isolated
schemas and mocked Stripe responses. They cover the exact deadline, both Dublin
clock changes, missed days, repeat runs, competing workers, lost creation and
confirmation responses, the creation retry window, declines, processing payments,
legacy consent, completion races and reconciliation after completion. Migration
003 was applied twice locally to verify repeatability.

Live Stripe test-mode verification is pending. The new worker has not been enabled
on EC2. Renter-facing debt totals and payment recovery remain later work in #14.

References: [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests)
and [PaymentIntent confirmation](https://docs.stripe.com/api/payment_intents/confirm).
