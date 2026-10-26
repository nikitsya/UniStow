"""Run the paid-rental controller on the Raspberry Pi, replacing report_door.py."""
import json
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, Thread
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


class LockerCycle:
    def __init__(self):
        self.phase = 'starting'
        self.closed_since = None

    def tick(self, closed, now, open_requested=False):
        if self.phase == 'idle' and open_requested and closed:
            self.phase = 'await_open'
            self.closed_since = None
            return 'open'
        if self.phase == 'await_open':
            if not closed:
                self.phase = 'await_close'
            return None
        if not closed:
            self.closed_since = None
            if self.phase == 'idle':
                self.phase = 'await_close'
            return None
        if self.phase in ('starting', 'await_close'):
            if self.closed_since is None:
                self.closed_since = now
            elif now - self.closed_since >= 1:
                self.phase = 'idle'
                self.closed_since = None
                return 'close'
        return None


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_config():
    config = json.loads(Path(__file__).with_name('device_config.json').read_text())
    server = config['server_url'].rstrip('/')
    key = config['device_key']
    url = urlsplit(server)
    if (url.scheme not in ('http', 'https') or not url.hostname or url.path
            or url.username or url.password or url.query or url.fragment
            or not isinstance(key, str) or not key.strip()):
        raise ValueError('Invalid device configuration.')
    return server, key


def communicate(server, key, shared, lock, commands, stop):
    opener = build_opener(NoRedirects())
    controller_id = str(uuid4())
    previous = None

    def post(path, data):
        request = Request(server + path, data=json.dumps(data).encode(), method='POST',
                          headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
        with opener.open(request, timeout=2) as response:
            return json.load(response)

    while not stop.is_set():
        with lock:
            report = dict(shared)
        report['controller_id'] = controller_id
        try:
            post('/api/device/heartbeat', report)
            message = f"Connected to PayBox. Door: {report['door_state']}."
            if report['ready']:
                started = monotonic()
                result = post('/api/device/commands/next', {'controller_id': controller_id})
                command = result.get('command')
                if command and command.get('action') == 'open':
                    ttl = float(command['valid_for_seconds'])
                    # Start timing before the request, so network delays shorten validity.
                    deadline = started + min(10, max(0, ttl))
                    if monotonic() < deadline and not stop.is_set():
                        with lock:
                            shared['ready'] = False
                        commands.put((command['id'], deadline))
        except HTTPError as error:
            message = f'PayBox returned HTTP {error.code}. Check the server and device key.'
        except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError):
            message = 'PayBox is unreachable or returned an invalid response. Local closing remains active.'
        if message != previous:
            print(message, flush=True)
            previous = message
        stop.wait(2)


def main():
    from gpiozero import Button, Servo
    from gpiozero.pins.pigpio import PiGPIOFactory

    try:
        server, key = load_config()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print('Check device_config.json beside this script.')
        return
    stop = Event()
    lock = Lock()
    shared = {'door_state': 'closed', 'ready': False}
    commands = Queue()
    cycle = LockerCycle()
    worker = None
    factory = PiGPIOFactory(host='localhost')
    try:
        # GPIO17: sensor (physical 11). GPIO18: servo (physical 12).
        with Button(17, pull_up=True, bounce_time=0.1, pin_factory=factory) as door, \
                Servo(18, initial_value=None, min_pulse_width=0.0015,
                      max_pulse_width=0.00225, pin_factory=factory) as servo:
            sleep(0.2)
            shared['door_state'] = 'closed' if door.is_pressed else 'open'
            worker = Thread(target=communicate,
                            args=(server, key, shared, lock, commands, stop), daemon=True)
            worker.start()
            print('Controller started. Close the door to prepare the latch.')
            last_command = None
            while True:
                now = monotonic()
                closed = door.is_pressed
                requested = False
                try:
                    command_id, deadline = commands.get_nowait()
                    if command_id != last_command:
                        last_command = command_id
                        requested = now < deadline and cycle.phase == 'idle' and closed
                except Empty:
                    pass
                action = cycle.tick(closed, now, requested)
                if action == 'open':
                    servo.max()
                    print('Opening commanded (2250 us). Open the door, then close it.', flush=True)
                elif action == 'close':
                    servo.min()
                    print('Closing commanded (1500 us).', flush=True)
                with lock:
                    shared['door_state'] = 'closed' if closed else 'open'
                    shared['ready'] = cycle.phase == 'idle' and closed
                sleep(0.05)
    except KeyboardInterrupt:
        print('Controller stopped. No additional latch movement commanded.')
    finally:
        stop.set()
        if worker:
            worker.join(timeout=5)
        factory.close()


if __name__ == '__main__':
    main()
