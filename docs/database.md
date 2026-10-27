# Database

PayBox uses PostgreSQL 16. [schema.sql](../backend/database/schema.sql) defines the current tables.

| Table | Purpose |
|-------|---------|
| `users` | Normalised, unique email addresses and Argon2 password hashes |
| `sessions` | Hashed session tokens, user references and expiry times |
| `lockers` | Number, size, daily price, enabled flag, door state and controller readiness |
| `rentals` | Owner, locker, booked dates, price, status and accepted overdue terms |
| `payments` | One Stripe checkout request per rental, fixed parameters, retry key and payment status |
| `payment_events` | Processed Stripe event IDs to prevent duplicate handling |
| `locker_commands` | Opening/collection commands, controller ID, expiry, dispatch and completion times |
| `overdue_charges` | Daily charge records; the table exists but automatic billing is not implemented |

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

## Overdue charges: planned

The accepted terms specify EUR 1 at the unpaid deadline and each following local 18:00
until collection and completion. The server saves the terms version and consent time.
`overdue_charges` allows one record per rental and local charge date, with statuses
`pending`, `paid`, `failed` or `waived`.

Automatic charging, payment-method setup and a scheduled task to mark rentals overdue
remain to be added. A failed charge must not release the locker. Operator contact details
also need to be provided.

## Initial setup

Docker runs `schema.sql` only when initialising an empty database volume. It creates
tables without inserting lockers. For a new database, run this from the project folder:

```sh
docker compose exec -T db psql -U paybox -d paybox -v ON_ERROR_STOP=1 < backend/database/migrations/001_add_lockers.sql
```

This adds nine lockers with only Locker 4 enabled. Repeating it leaves existing records
unchanged. It does not reset availability or change existing locker settings.

Migrations are manual; restarting Docker does not apply SQL changes. The repository
currently contains only migration `001_add_lockers.sql`. Device and payment tables are
already in the current schema. An older database needs a migration based on its existing
structure; do not rerun the initial schema or delete its volume to update it.
