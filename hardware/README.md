# Raspberry Pi hardware

## Report the door state to PayBox

This first connection step reports the sensor only; it does not unlock the locker.
No database migration is required.

1. Connect the Mac and Pi to the same trusted local network. Stop the existing backend,
   then run from `backend` on the Mac:

   ```bash
   uv run uvicorn main:app --host 0.0.0.0 --port 8000
   ```

2. Find the Mac's local IP in System Settings > Wi-Fi > Details > TCP/IP.
   On the Pi, open `http://MAC_LOCAL_IP:8000` to check connectivity.
   `localhost` on the Pi refers to the Pi, not the Mac. Keep `APP_BASE_URL` unchanged.
3. Copy `report_door.py` and `device_config.example.json` to the same folder on the Pi.
   Rename the JSON file to `device_config.json`. Set `server_url` to the Mac's address
   and `device_key` to `PAYBOX_DEVICE_KEY` from the Mac project's `.env`.
   Copy only that key, not database or Stripe secrets. Do not commit the JSON file.
4. Stop other GPIO tests, then run `report_door.py` in Thonny. Expect
   `Connected to PayBox. Door: closed.` Open and close the door, then refresh
   My rental in PayBox to see the latest state.

Reports arrive every two seconds. After 15 seconds without a report, refreshing
My rental shows `Offline` and `Unknown`. Door closed does not prove the latch is locked.
HTTP is for this trusted local prototype only; use HTTPS on untrusted networks.
Do not forward port 8000 on the router or expose pigpiod to the network.
Stop the reporting script before running the locker test.

## Open a paid locker

1. On the Mac, apply the migration from the project root, then restart the backend:

   ```bash
   docker compose exec -T db psql -U paybox -d paybox -v ON_ERROR_STOP=1 < backend/database/migrations/002_locker_commands.sql
   ```

2. Stop `report_door.py` and any hardware tests on the Pi. Copy `run_locker.py`
   beside the existing `device_config.json`, then run it in Thonny with the door closed.
3. Sign in to PayBox as the owner of an active, paid rental. Refresh My rental
   and press **Open locker**. Open the door after the latch moves, then close it.
   After one second continuously closed, the controller commands the latch closed.

The controller uses the tested 1500 us closed and 2250 us open pulses.
After closing, it checks the door for another two seconds before acknowledging collection.
If the door opens during this check in an authorised cycle, it releases the latch
and waits for closure again, with at most three automatic retries. It then stops
for assistance without completing the rental. An unexpected open door outside
that cycle does not automatically unlock the latch.
For an active paid rental, **Open locker** lets the owner retract the latch
while the door is open and the controller is ready. **Finish rental** still requires
a closed door. Replace the Pi's `run_locker.py` and restart `paybox-locker` to use this behaviour.
Local closing continues if the Mac connection drops. Opening requests expire after
10 seconds and are dispatched only once; refresh before requesting another opening.
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

Apply `backend/database/migrations/003_finish_rental.sql` using the same migration
command above with the new filename, then restart the backend. Replace the Pi's
`run_locker.py` with the updated file and restart it; keep `device_config.json`.

Choose **Finish rental** in My rental and confirm that you will collect everything.
Open the door, remove your belongings and close it. The Pi commands the latch closed
and sends a completion acknowledgement. Refresh My rental to see `completed`;
Locker 4 becomes available. **Open locker** alone does not finish the rental.

If the network drops, keep the script running: it retries the acknowledgement.
If the script or Pi stops before acknowledgement, the rental stays occupied and
needs checking by the operator. Overdue access still requires operator assistance.

## Start the controller automatically

This service uses the Pi user `nikitsya` and expects `run_locker.py` and
`device_config.json` in `/home/nikitsya/Desktop`. Check both files exist first.
Stop all GPIO scripts in Thonny and close the door before starting the service.
Copy `paybox-locker.service` to the Pi Desktop, then run in the Pi terminal:

```bash
sudo install -m 644 ~/Desktop/paybox-locker.service /etc/systemd/system/paybox-locker.service
sudo systemctl daemon-reload
sudo systemctl enable --now paybox-locker.service
systemctl is-active paybox-locker.service
```

Expected: `active`. Check messages with:

```bash
journalctl -u paybox-locker.service -b --no-pager -n 20
```

After reboot, check the service again and test PayBox. The Mac backend must still
be running. The controller retries network connections and systemd restarts it if
it exits. Starting it prepares the closed latch; it does not request an opening.

Do not run a second controller or GPIO test in Thonny while the service is active.
Before a manual test, use `sudo systemctl stop paybox-locker.service`.
After stopping the test, use `sudo systemctl start paybox-locker.service`.
After updating the script, use `sudo systemctl restart paybox-locker.service`.
To remove automatic startup, use `sudo systemctl disable --now paybox-locker.service`.
