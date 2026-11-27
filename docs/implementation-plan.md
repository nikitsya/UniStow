# Implementation plan

See the [README](../README.md) for the current implementation and
[testing record](usability-testing.md) for completed checks. This file tracks
work still needed for the individual IoT submission.

## Assessment checklist

The individual IoT assessment is due on **8 December 2026 at 17:00 (Ireland time)**.
Use this checklist alongside the original brief before submitting to Moodle.

- [x] Check the [architecture diagram](../README.md#current-stack)
  against the deployed system. The
  [27 November record](../deploy/README.md#architecture-check-on-27-november-2026)
  covers EC2 code, TLS, AWS network rules and EBS, plus Pi service status, file
  permissions, storage, configuration, established broker TCP connection and
  fresh device API heartbeats and MQTT telemetry from the running Pi.
- [ ] Check the existing [Fritzing sketch](../hardware/unistow-wiring.fzz) and
  [wiring guide](../hardware/README.md#wiring-diagram) against the physical leads,
  including the Pi 5V and ground connections.
- [ ] Record the screencast with an empty locker: explain the code and architecture,
  then sign in, reserve, pay in Stripe test mode, open, collect and close. Show
  the secured MQTT exchange, sensor reading, resulting PostgreSQL rental state
  and restored availability. Explain authentication, topic permissions and
  encryption, including the public MQTT port and Pi storage limitations.
  Use the [rental #59 walkthrough](usability-testing.md#27-november-2026--locker-4-rental-walkthrough-59)
  as the recording sequence; its screenshots do not replace the video.
- [ ] Review the completed [CA cover sheet](submission/ca-cover-sheet.pdf) and the
  [AI usage cover sheet](submission/ai-usage-cover-sheet.pdf). Bring the
  [AI chat log](AI_Chat_Log.md) up to date before submission.
- [ ] Verify that the [repository link](https://github.com/nikitsya/UniStow) in the
  README works for the lecturer and commit the final project files.
- [ ] Package the source, diagrams, recording and required documents in one ZIP
  that extracts to `iot_ica_nikita_smiichyk`. Check the extracted contents
  for credentials before uploading. Keep the deployed site available for testing
  after submission.

The viva is on **9 December 2026 at 14:00 in P1111**.

## Additional prototype testing

- [x] Repeat the disconnected-controller test with Mosquitto. The
  [27 November check](usability-testing.md#27-november-2026--controller-interruption-and-restart-61)
  records the offline state, an interrupted opening and service recovery. The
  author confirmed no delayed opening after restart and successful opening only
  after a fresh request.
- [x] Repeat the light-closure test. The
  [27 November check](usability-testing.md#27-november-2026--light-closure-and-latch-recovery)
  records the Pi journal and the author's confirmation of automatic latch
  retraction when the door came ajar and secure closure after a full close.
- [x] Check availability from a second account during an unpaid reservation and
  after payment.
  The [27 November two-account check](usability-testing.md#27-november-2026--availability-between-two-accounts-60)
  records the author's reservation result and screenshots of the paid phase.
- [ ] On a phone, check pull-to-refresh, dialogue dismissal, cancellation and
  resuming a valid unpaid reservation after signing out and back in. Verify
  that recent history remains visible after signing back in.
- [ ] Check the extra-day summary and outstanding-payment flow on a physical phone;
  the existing billing screenshots and browser previews do not establish this.
