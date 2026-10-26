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
Local closing continues if the Mac connection drops. Opening requests expire after
10 seconds and are dispatched only once; refresh before requesting another opening.
Opening is limited to the paid rental period. Overdue access needs operator assistance.
This step does not end the rental. Only pigpiod starts automatically; run this script
manually. The sensor reports the door position, not confirmation that the latch is locked.

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

Only pigpiod starts automatically; run `test_locker.py` separately in Thonny.
The service accepts local socket connections only and disables the FIFO interface.
See the [pigpiod options](https://github.com/joan2937/pigpio/blob/master/pigpiod.1).
