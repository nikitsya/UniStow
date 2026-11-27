# Database

UniStow uses PostgreSQL 16. [schema.sql](../database/schema.sql) defines the current tables.

| Table              | Purpose                                                                                |
|--------------------|----------------------------------------------------------------------------------------|
| `users`            | Normalised, unique email addresses and Argon2 password hashes                          |
| `sessions`         | Hashed session tokens, user references and expiry times                                |
| `lockers`          | Number, size, daily price, enabled flag, door state and controller readiness           |
| `rentals`          | Owner, locker, booked dates, price, status and accepted overdue terms                  |
| `payments`         | One Stripe checkout request per rental, fixed parameters, retry key and payment status |
| `payment_events`   | Processed Stripe event IDs to prevent duplicate handling                               |
| `locker_commands`  | Opening/collection commands, controller ID, expiry, dispatch and completion times      |
| `overdue_charges`  | Daily charge records, durable Stripe requests and collection status                    |
| `overdue_payments` | Checkout requests for failed charges, retry parameters and payment confirmation        |

A user and a locker can each have many past rentals. A unique index permits only one
unfinished (`pending`, `active` or `overdue`) rental per locker. Foreign keys preserve
rental history. Payment records contain Stripe references, not card details.

## Application database access

[database.py](../backend/database.py) configures the async SQLAlchemy engine,
`async_sessionmaker` and the `get_db` request dependency. The backend uses
`AsyncSession` with the psycopg driver and closes the engine during shutdown.

[models.py](../backend/models.py) maps all nine tables using `DeclarativeBase`,
`Mapped` and `mapped_column`. Account, rental, device and payment queries use
these models with SQLAlchemy `select`, `update`, `delete` and inserts. Some
queries select individual columns and return mappings for existing services
and templates; others load ORM objects directly.

PostgreSQL-specific operations remain where needed: JSONB containment,
conflict-safe inserts and session-level advisory locks for the billing worker.
Account and locker row locks, transaction boundaries and durable Stripe retry
parameters are preserved.

`database/schema.sql` remains the source of truth for database structure,
including check constraints and partial indexes. The model declarations do not
reproduce every constraint or index. Application startup does not call
`Base.metadata.create_all`; model changes alone do not update an existing
PostgreSQL database. See the initial setup instructions below.

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
`pending`, `paid`, `failed`, `waived` or `review`.

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
tables without inserting lockers. Production Compose then runs the locker seed
automatically. For a new local database, run this from the project folder:

```sh
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < database/seed.sql
```

This adds nine lockers with only Locker 4 enabled. Repeating it leaves existing records
unchanged. It does not reset availability or change existing locker settings.

The repository keeps the current schema and locker seed rather than incremental
upgrade scripts. Historical scripts remain available in Git history. Restarting
Docker does not update an existing database. Before changing an existing database,
back it up, compare its structure with `database/schema.sql` and prepare the
required SQL changes. Do not rerun the initial schema or delete the volume to
update it.

## Verification records

The [billing verification record](usability-testing.md#billing-verification--910-november-2026)
contains the dated local and EC2 results. Current test commands are in the
[README](../README.md#tests).

Reference: [saving payment details during payment](https://docs.stripe.com/payments/checkout/save-during-payment).

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

Ensure the database matches the current schema before enabling the worker.
Set `UNISTOW_OVERDUE_BILLING=1` in the application's environment and restart the
application. Production Compose passes this variable through, defaulting to 0.
The worker accepts only a Stripe test secret key. Setting the flag to 0 and
restarting the application disables the worker.

The worker was enabled on EC2 on 10 November 2026. Preserve the production
flag during updates. The [rental 45 record](usability-testing.md#after-the-automatic-billing-deadline--rental-45)
documents the real deadline check and repeated observations of the same charge.

References: [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests)
and [PaymentIntent confirmation](https://docs.stripe.com/api/payment_intents/confirm).

### Rental payment summary

The rental page includes recorded extra-day charges in its total. Where extra
charges exist, it shows the original rental cost and separate amounts for paid,
processing and unpaid extra days. Waived charges are excluded. The summary
remains visible after completion and refreshes with the existing live page
updates. It does not estimate days for which the worker has not created a record.

Pending charges display **Payment processing**; failed charges display **Payment incomplete**. Neither is presented as
collected money. Failed
charges can be paid through the recovery flow below.

See the [rental 43 screenshot](usability-testing.md#extra-day-payment-summary--10-november-2026)
for the paid-charge presentation.

### Paying an outstanding balance

The current schema includes `overdue_payments`. Each row
stores a frozen list of failed charge IDs, the server-calculated amount, durable
Checkout parameters and the resulting Stripe session and PaymentIntent IDs.
Only one pending recovery payment is allowed per rental.

The owner can open a test-mode Stripe Checkout session from Pay outstanding,
including after collection has completed. Pending automatic charges are not
eligible. Before creating a replacement payment, the service verifies and
cancels the original declined intents. An unresolved or processing original
payment requires review instead of another collection attempt.

Repeated clicks reuse the pending session. An unknown creation response reuses
the same idempotency key for less than 23 hours; later ambiguity requires review.
An expired unpaid session releases the pending slot for another attempt. If a
new failed daily charge appears while Checkout is open, it is not silently added
to that session: the button shows the existing session amount and the remaining
balance can be paid afterwards.

A signed Checkout webhook or owner-requested reconciliation retrieves Stripe's
current state. The service verifies test mode, session identity, metadata,
amount, currency and successful collection before marking the selected charges
paid. Returning to the success URL alone does not clear a balance. Duplicate
notifications cannot collect money or apply the payment twice. Original declined
intent IDs remain on the daily charge records for audit; the successful
replacement intent is stored in `overdue_payments`.

Recovery does not reopen a completed rental, waive other debt or replace the
saved method used for future automatic charges. Completed rentals with failed,
pending or review charges remain listed in My rentals. Finish rental remains available
under the existing ownership and controller checks.

The [rental 44 record](usability-testing.md#paying-after-collection--rental-44)
shows successful sandbox recovery after collection, including the cancelled
original intent and unchanged completed rental status.

References: [Stripe Checkout Sessions](https://docs.stripe.com/api/checkout/sessions/create)
and [Checkout fulfilment](https://docs.stripe.com/checkout/fulfillment).

### Booking with an outstanding balance

Booking eligibility is calculated from the account's current `overdue_charges`,
across active, overdue and completed rentals. It does not depend on the five
recently completed rentals shown in history, browser state or a stored restriction
flag. A confirmed failed charge of any amount, including one EUR 1 day, prevents
new reservations and initial checkout requests for an existing unpaid reservation.
Paid and waived charges, cancelled rentals and unpaid initial reservations are
excluded. Historical failures do not restrict an account after settlement.

Pending automatic charges and `review` outcomes temporarily pause new bookings
without being labelled unpaid debt. Failed charges included in a pending recovery
Checkout are shown as awaiting confirmation, separately from any remaining unpaid
amount. Cancelling or expiring an unpaid recovery restores the unpaid wording;
verified settlement clears only the charge IDs covered by that payment. Remaining
balances on the same rental or another rental continue to restrict booking.

The home page, booking page, My rentals and pending rental pages show the
account balance notice, affected rental links and Contact. Active and completed
rental details show their own payment summary without repeating that notice.
Live updates refresh the displayed state. Signing out, returning from Stripe or
changing client-side controls cannot clear a balance.
Signed webhooks and server reconciliation retain their amount, identity, test-mode
and successful-payment checks. Repeated or reordered callbacks do not reapply a
settlement or reinstate a cleared balance.

Reservation creation, initial checkout authorisation, automatic charge creation
and balance transitions all lock the account's `users` row before any locker row.
Recovery creation and settlement follow the same order. The eligibility check and
reservation or durable checkout insert share that transaction. A concurrent
booking waits for settlement to commit, then reads the new balance under
PostgreSQL's default READ COMMITTED isolation. Any future operator adjustment
must use this same account-first lock order. Physical collection still takes only
the locker lock; it does not waive charges or depend on booking eligibility.

**Checkout already authorised:** the durable initial payment request is the
authorisation boundary. If a balance appears after that request was committed,
an already issued Checkout (including an in-flight creation request) may still
complete. Its verified payment activates that reserved rental under the existing
paid-period rules; it is not collected and then rejected because of the new debt.
The reservation remains held until Checkout is reconciled. New requests to start
or reopen initial checkout are blocked while the account is restricted, but
confirmation and cancellation remain available. The pending rental page explains
this exception. This policy avoids racing Stripe expiry against payment collection.

Restriction never blocks sign-in, account pages, support, recovery payments or
Finish rental. Another already paid rental can still open within its paid period.
The normal ownership, controller readiness, door and overdue-access checks remain
in force. No additional penalties, strikes or charges are introduced.

For an incorrect balance or an unresolved payment, use Contact and quote the
rental number. The operator checks the charge and Stripe references before making
a verified correction. `review` means the result needs investigation, not a new
collection attempt. There is no operator dashboard or automatic waiver; the
restriction clears once all relevant records are verified as settled or corrected.

New installations include the `review` state in the current schema. For an older
database, back it up and check its constraints and uncertain payment outcomes
before starting the application. The historical upgrade scripts are available in
Git history; see [initial setup](#initial-setup) for the current approach.

Automated tests use isolated PostgreSQL schemas and mocked Stripe responses.
They cover an EUR 1 balance, account-wide and older debt, pending/review wording,
direct reservation and checkout requests, unaffected paid access and collection,
partial and duplicate settlements, account-lock races
and a checkout authorised immediately before another rental's debt appears.

The [deployed-site visual evidence for issue #19](usability-testing.md#22-november-2026--unpaid-balance-restriction)
records the unpaid Locker 5 balance, zero available new rentals, the notice
above rental history and the recovery action on the completed rental page.
