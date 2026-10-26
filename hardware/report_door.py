from pathlib import Path

import json
from gpiozero import Button
from gpiozero.pins.pigpio import PiGPIOFactory
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the device key to a redirected address.
        return None


def main():
    config_path = Path(__file__).with_name("device_config.json")
    try:
        config = json.loads(config_path.read_text())
        server = config["server_url"].rstrip("/")
        key = config["device_key"]
        parsed = urlsplit(server)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path or not isinstance(key, str) or not key.strip()):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        print("Check server_url and device_key in device_config.json beside this script.")
        return

    opener = build_opener(NoRedirects())
    factory = PiGPIOFactory(host="localhost")
    try:
        # Telemetry only: this script never controls the servo.
        with Button(17, pull_up=True, bounce_time=0.1, pin_factory=factory) as door:
            sleep(0.2)
            previous = None
            while True:
                state = "closed" if door.is_pressed else "open"
                request = Request(
                    server + "/api/device/heartbeat",
                    data=json.dumps({"door_state": state}).encode(),
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    with opener.open(request, timeout=5) as response:
                        response.read()
                    message = f"Connected to PayBox. Door: {state}."
                except HTTPError as error:
                    print(f"PayBox returned HTTP {error.code}. Check the server and device key.")
                    return
                except (URLError, TimeoutError, OSError):
                    message = "PayBox is unreachable. Retrying; no servo movement commanded."
                if message != previous:
                    print(message, flush=True)
                    previous = message
                sleep(2)
    except KeyboardInterrupt:
        print("Door reporting stopped.")
    finally:
        factory.close()


if __name__ == "__main__":
    main()
