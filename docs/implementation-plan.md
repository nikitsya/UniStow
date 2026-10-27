# Implementation plan

See the [README](../README.md) for the current stack and startup instructions.

## Working now

- Mobile-friendly locker list, registration, sign-in and account menu.
- PostgreSQL reservations, ownership checks and **Continue rental** for the owner.
- Stripe test checkout, verified webhooks and payment reconciliation.
- Automatic updates for availability, rental status and door state.
- Raspberry Pi 400 with an SG92R servo and magnetic door sensor.
- Authorised opening, closure checks and rental completion after collection.
- Automatic startup of pigpiod and the Pi controller.

The Pi currently polls the local FastAPI backend over HTTP. PubNub and AWS were part
of the initial proposal and are not implemented. The Checkpoint 1 presentation is a
record of that proposal, not a description of the current system.

## Next steps

1. Implement and test overdue status updates, payment-method setup and EUR 1 daily
   test charges. Consent is already saved; automatic charging is not implemented.
2. Add operator contact details and a recovery process for failed payments,
   interrupted collection and hardware faults.
3. Test the complete journey on mobile, including two users competing for a locker,
   network loss, restarts and repeated button presses.
4. Choose hosting and deploy with HTTPS, restricted database access and backups.
   Update the Pi server address and Stripe webhook destination for deployment.
5. Check the assessment brief against the finished work and prepare an up-to-date
   architecture diagram, wiring diagram, demonstration and submission materials.
