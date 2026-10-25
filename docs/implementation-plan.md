# Implementation plan

The project overview and planned stack are in the [README](../README.md).

## First working version

1. Set up FastAPI with Jinja2 pages and PostgreSQL.
2. Add registration, login and ownership checks.
3. Add one demo locker and a device simulator.
4. Store door status and last-seen time, and show them in the web app.
5. Let the rental owner request an unlock and see the device acknowledgement.

Check that another user cannot access the rental, and that duplicate or expired commands
are ignored. Clearly label simulated device data.

## After that

- Connect the Raspberry Pi through secured pub-sub channels.
- Add Stripe test checkout, reservations and verified payment webhooks.
- Handle rental expiry, collection and cases where the device or payment fails.
- Deploy on AWS with HTTPS, restricted access and encrypted storage/backups.
- Finish the architecture diagram, Fritzing sketch, screencast and submission materials.

## Still to decide

- Available hardware and power supplies.
- AWS account, budget, domain and PostgreSQL hosting.
- Whether PubNub channel access controls are available in the account.
- Daily rental closing time and how users get help retrieving belongings.
