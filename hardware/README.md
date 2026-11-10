# Raspberry Pi hardware

## Connect Locker 4 to Mosquitto

The controller sends door changes and a fresh reading every five seconds to
`unistow/locker-4/events`. It subscribes to `unistow/locker-4/commands`.
Use the device password provisioned in the [broker setup](../deploy/README.md#self-hosted-mqtt).
The HTTPS device key remains separate and authenticates heartbeats, collection
polling and completion acknowledgements.

On the Pi, install Paho in a virtual environment that can import the existing
system GPIO packages. The supplied systemd unit uses this interpreter.

```sh
python3 -m venv --system-site-packages ~/.venvs/unistow
~/.venvs/unistow/bin/python -m pip install 'paho-mqtt>=2.1,<3'
```

Copy `run_locker.py` and `locker_mqtt.py` to the controller directory. Back up the
private configuration and replace its old PubNub section with `mqtt` from
`device_config.example.json`. Keep the HTTPS device key and server address.
Set the new device password, run `chmod 600 device_config.json` and synchronise
the Pi clock. With a private test CA, set `mqtt.ca_file` to its public
certificate. Production uses the Pi's system trust store for `unistow.app`.

Start with `mqtt.mode` set to `test` on the Pi and select test mode on the
server. On an active paid rental select **Test open delivery**. A matching
acknowledgement confirms the round trip without actuating the test command.
For a physical test, use an empty locker, set Pi mode to `physical`, switch
the server to physical opening and restart both services. Confirm that opening,
sensor updates and collection work together. A lost acknowledgement leaves
delivery uncertain. Check the door before issuing a new request.

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
   and `locker_mqtt.py` beside the existing `device_config.json`, then run
   `~/.venvs/unistow/bin/python run_locker.py` with the door closed.
3. Sign in to UniStow as the owner of an active, paid rental and press **Open locker**.
   Open the door after the latch moves, then close it. After one second continuously
   closed, the controller commands the latch closed.

The controller uses the tested 1500 us closed and 2250 us open pulses.
After closing, it checks the door for another two seconds before acknowledging collection.
An open sensor reading retracts the latch at startup, during a closing attempt,
or after the latch has settled. The controller waits for a full second of
continuous closed readings before lowering it again. After three failed retries
it leaves the latch retracted for assistance. Mount the sensor so it reports
closed only when the door is seated and the latch can clear the frame. **Finish rental** requires
a closed door. Replace the Pi's `run_locker.py` and restart `uniflow-locker` to use this behaviour.
Local closing continues if the Mac connection drops. Opening requests expire after
10 seconds and are dispatched only once. Wait for the button to become available
before requesting another opening.
Opening is limited to the paid rental period. An overdue renter can still request collection.
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
needs checking by the operator. Use the Contact page if the controller is unavailable.

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
