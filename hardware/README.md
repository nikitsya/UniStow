# Raspberry Pi hardware

## Test a door reading through PubNub

Use this one-shot test on an idle Locker 4. It reads GPIO17 and publishes the
door position to `unistow.locker-4.events`. The rental controller still uses
the HTTP API for commands.

1. Enable Access Manager on the PubNub keyset. From a trusted machine, grant
   a short-lived token bound to user ID `unistow-locker-4` with write permission
   only on `unistow.locker-4.events`. Keep the secret key on the trusted machine.
2. On the Pi, copy `publish_door.py` beside `run_locker.py`. Add the `pubnub`
   section from `device_config.example.json` to the private `device_config.json`.
   Fill in the publish key, subscribe key and token. Run `chmod 600 device_config.json`.
3. Start a subscriber on the events channel with a separate read token.
   Stop the locker service with `sudo systemctl stop uniflow-locker` if it is
   running, and close any GPIO tests. Keep pigpiod running.
4. With the sensor contacts together, run `python3 publish_door.py` and check
   the subscriber receives `{"door_state": "closed"}`. Separate the contacts
   and run it again to check for `{"door_state": "open"}`. The script reads
   the sensor once per run and leaves the servo alone.
5. Close the door and restart the locker service if you stopped it.

## Publish from the running controller

Copy both `run_locker.py` and `publish_door.py` to the Pi Desktop. With a
publish token configured, the controller sends the first door reading, changes
of state and a fresh reading every five seconds. It retries failed publishes
after five seconds and reads the current credentials for each attempt.
A separate thread handles publishing while GPIO and HTTP commands continue.

## Receive the reading in local FastAPI

Set `PUBNUB_SUBSCRIBE_KEY` and `PUBNUB_READ_TOKEN` in the local server
environment before starting FastAPI. Grant the token to `unistow-mac-reader`
with read permission on `unistow.locker-4.events`. Restart with a fresh token
after expiry. When the server logs `Listening on unistow.locker-4.events`,
run the sensor test on the Pi and look for `Locker 4 door via PubNub: closed`
or `open` in the server log.

The Locker 4 rental page uses the latest fresh reading from PubNub or the
HTTP heartbeat. PubNub readings expire after 15 seconds and are held in
memory for this local test. Command authorisation still uses the controller
heartbeat and the database.

## Check the return path with a ping

Grant the Pi user read access to `unistow.locker-4.commands` alongside its
existing write access to the events channel. Give the FastAPI reader write
access to the commands channel alongside its existing read access to events.
Copy `probe_ping.py` and the matching `publish_door.py` beside the controller.
Run `python3 probe_ping.py` on the Pi while the locker service is running.

After `Pi ready for ping.`, start local FastAPI with its scoped token and
set `PUBNUB_PROBE_PUBLISH_KEY` to the keyset's publish key for that run.
FastAPI sends one ping after subscribing. The probe replies on the events
channel and exits. Compare the ID in the Pi's `Sent pong` line with the
server's `Locker 4 pong` line. Restart the probe and server to repeat.
The probe handles only ping messages and uses no GPIO pins.

## Report the door state to UniStow

This first connection step reports the sensor only; it does not unlock the locker.
These instructions assume the current database schema and locker records are installed;
see [database setup](../docs/database.md#initial-setup).

1. Connect the Mac and Pi to the same trusted local network. Stop the existing backend,
   then run from `backend` on the Mac:

   ```bash
   uv run uvicorn main:app --host 0.0.0.0 --port 8000
   ```

2. Find the Mac's local IP in System Settings > Wi-Fi > Details > TCP/IP.
   On the Pi, open `http://MAC_LOCAL_IP:8000` to check connectivity.
   `localhost` on the Pi refers to the Pi, not the Mac. `APP_BASE_URL` is the
   browser checkout return address; it is separate from the Pi server address.
3. Copy `report_door.py` and `device_config.example.json` to the same folder on the Pi.
   Rename the JSON file to `device_config.json`. Set `server_url` to the Mac's address
   and `device_key` to `UNISTOW_DEVICE_KEY` from the Mac project's `.env`.
   Copy only that key, not database or Stripe secrets. Do not commit the JSON file.
4. Stop other GPIO tests, then run `report_door.py` in Thonny. Expect
   `Connected to UniStow. Door: closed.` Open and close the door; the paid rental page updates its door state
   automatically.

Reports arrive every two seconds. After 15 seconds without a report, the device is
treated as offline, the door reads `Unknown` and opening is unavailable. Door closed does not prove the latch is locked.
HTTP is for this trusted local prototype only; use HTTPS on untrusted networks.
Do not forward port 8000 on the router or expose pigpiod to the network.
Stop the reporting script before running the locker test.

## Open a paid locker

1. Start Docker PostgreSQL and the backend on the Mac using the [README](../README.md).
2. Stop `report_door.py` and any hardware tests on the Pi. Copy `run_locker.py`
   beside the existing `device_config.json`, then run it in Thonny with the door closed.
3. Sign in to UniStow as the owner of an active, paid rental and press **Open locker**.
   Open the door after the latch moves, then close it. After one second continuously
   closed, the controller commands the latch closed.

The controller uses the tested 1500 us closed and 2250 us open pulses.
After closing, it checks the door for another two seconds before acknowledging collection.
If the door opens during this check in an authorised cycle, it releases the latch
and waits for closure again, with at most three automatic retries. It then stops
for assistance without completing the rental. An unexpected open door outside
that cycle does not automatically unlock the latch.
For an active paid rental, **Open locker** lets the owner retract the latch
while the door is open and the controller is ready. **Finish rental** still requires
a closed door. Replace the Pi's `run_locker.py` and restart `uniflow-locker` to use this behaviour.
Local closing continues if the Mac connection drops. Opening requests expire after
10 seconds and are dispatched only once. Wait for the button to become available
before requesting another opening.
Opening is limited to the paid rental period. Overdue access needs operator assistance.
For automatic controller startup, follow the section below.
The sensor reports the door position, not confirmation that the latch is locked.

## Start pigpiod automatically

The service expects pigpiod at `/usr/local/bin/pigpiod` and its library installed.
Copy `pigpiod.service` from this folder to the Raspberry Pi Desktop, then run:

```bash
sudo install -m 644 ~/Desktop/pigpiod.service /etc/systemd/system/pigpiod.service
sudo systemctl daemon-reload
sudo systemctl enable pigpiod
```

Stop the Thonny test and save your work, then reboot:

```bash
sudo reboot
```

Enabling without starting avoids conflicting with the manually started daemon.
After reboot, check:

```bash
systemctl is-active pigpiod
python3 -c "import pigpio; pi = pigpio.pi('localhost'); print('Connected:', pi.connected); pi.stop()"
```

Expected: `active` and `Connected: True`. If it fails:

```bash
journalctl -u pigpiod -b --no-pager -n 30
```

This service starts pigpiod only. The controller has its own service below.
The service accepts local socket connections only and disables the FIFO interface.
See the [pigpiod options](https://github.com/joan2937/pigpio/blob/master/pigpiod.1).

## Finish a rental

Choose **Finish rental** in My rental and confirm that you will collect everything.
Open the door, remove your belongings and close it. The Pi commands the latch closed
and checks the door for another two seconds before sending a completion acknowledgement.
The rental status updates automatically to `completed`; Locker 4 becomes available. **Open locker** alone does not
finish the rental.

If the network drops, keep the script running: it retries the acknowledgement.
If the script or Pi stops before acknowledgement, the rental stays occupied and
needs checking by the operator. Overdue access still requires operator assistance.

## Start the controller automatically

This service uses the Pi user `nikitsya` and expects `run_locker.py` and
`device_config.json` in `/home/nikitsya/Desktop`. Check both files exist first.
Stop all GPIO scripts in Thonny and close the door before starting the service.
Copy `uniflow-locker.service` to the Pi Desktop, then run in the Pi terminal:

When upgrading an existing installation, stop and disable the previously installed
locker controller service before enabling this one. Only one controller service
must run at a time. Keep the existing device key in `device_config.json`.

```bash
sudo install -m 644 ~/Desktop/uniflow-locker.service /etc/systemd/system/uniflow-locker.service
sudo systemctl daemon-reload
# On older installations, disable the existing unit before starting UniFlow.
for unit in paybox-locker.service unistow-locker.service; do
    if systemctl cat "$unit" >/dev/null 2>&1; then
        sudo systemctl disable --now "$unit" || exit 1
    fi
done
sudo systemctl enable --now uniflow-locker.service
systemctl is-active uniflow-locker.service
```

Expected: `active`. Check messages with:

```bash
journalctl -u uniflow-locker.service -b --no-pager -n 20
```

After reboot, check the service again and test UniStow. The Mac backend must still
be running, with PostgreSQL available and the Mac awake. Screen sharing does not
need to be open. The controller retries network connections and systemd restarts it if
it exits. Starting it prepares the closed latch; it does not request an opening.

Do not run a second controller or GPIO test in Thonny while the service is active.
Before a manual test, use `sudo systemctl stop uniflow-locker.service`.
After stopping the test, use `sudo systemctl start uniflow-locker.service`.
After updating the script, use `sudo systemctl restart uniflow-locker.service`.
To remove automatic startup, use `sudo systemctl disable --now uniflow-locker.service`.

## Test open command delivery from the rental page

Use the same scoped tokens as the ping test. Set `open_test` to `true` in
the Pi's private `pubnub` configuration and install the matching
`probe_ping.py`, `publish_door.py` and `run_locker.py` before restarting the
controller. Start local FastAPI with `UNISTOW_OPEN_TEST=1` and the PubNub
credentials used for the ping test. Refresh the paid Locker 4 rental and
select **Test open delivery**. The page reports success only after the Pi
acknowledges the matching command ID. The Pi clock must be synchronised.

The test command expires within ten seconds or at the rental deadline,
whichever comes first. The controller handles it in a separate thread
without calling the servo. To resume physical commands in this server,
restart FastAPI without the test flag and reload the rental page.
