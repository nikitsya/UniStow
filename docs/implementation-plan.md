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

The production site runs on AWS EC2 at https://unistow.app. Nginx verifies the
FastAPI certificate, and FastAPI verifies the PostgreSQL certificate. Eclipse Mosquitto runs on the same EC2 host.
The production app and Locker 4 connect to the broker over TLS. The Pi
acknowledged a test command and resumed reporting its door state after a
controller service restart. It uses authenticated HTTPS for heartbeats and
collection acknowledgements. See the [deployment guide](../deploy/README.md)
for the security settings and EC2 evidence.

## Assessment checklist

The individual IoT assessment is due on **8 December 2026 at 23:55**.
Use this checklist alongside the original brief before submitting to Moodle.

- [ ] Record an end-to-end run with the empty physical locker: sign in, reserve,
  pay in Stripe test mode, open, collect and close. Show the sensor reading,
  the resulting rental state in PostgreSQL and when the locker becomes available.
- [ ] Review the architecture diagram in the project README. Label the internal
  TLS connections and explain device access, channel permissions, database and
  web server access, encryption in transit and EBS encryption at rest.
- [ ] Prepare a Fritzing sketch showing the actual Raspberry Pi, servo and door
  sensor connections. Check it against the physical wiring.
- [ ] Record the screencast: explain the code and architecture, demonstrate the
  deployed application and secured MQTT exchange live, and discuss known issues.
- [ ] Complete the CA cover sheet and AI usage cover sheet. Bring the
  [AI chat log](AI_Chat_Log.md) up to date.
- [ ] Include the GitHub repository link in the submission README, verify the
  lecturer has access and commit the final project files.
- [ ] Package the source, diagrams, recording and required documents in one ZIP
  that extracts to `iot_ica_firstname_lastname`. Check the extracted contents
  for credentials before uploading. Keep the deployed site available for testing
  after submission.

## Additional prototype testing

Repeat
the [disconnected-controller test](usability-testing.md#7-november-2026--opening-locker-4-while-its-controller-was-stopped-7)
with Mosquitto and an empty locker. Let an opening command expire while the Pi
is offline, reconnect it and verify that the latch moves only after a fresh
authorised request.
