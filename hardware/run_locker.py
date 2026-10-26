"""Run the paid-rental controller on the Raspberry Pi, replacing report_door.py."""
from pathlib import Path

import json
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
        self.retries = 0
        self.authorised = False

    def ready(self, closed):
        return self.phase == 'idle' or (not closed and self.phase in ('starting', 'await_close', 'blocked'))

    def tick(self, closed, now, open_requested=False):
        if open_requested and self.ready(closed):
            self.phase = 'await_open' if closed else 'await_close'
            self.closed_since = None
            self.retries = 0
            self.authorised = True
            return 'open'
        if self.phase == 'confirm_close':
            if not closed:
                self.closed_since = None
                if self.authorised and self.retries < 3:
                    self.retries += 1
                    self.phase = 'await_close'
                    return 'open'
                self.phase = 'blocked'
            elif now - self.closed_since >= 2:
                self.phase = 'idle'
                self.closed_since = None
                self.authorised = False
                return 'secured'
            return None
        if self.phase == 'blocked':
            return None
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
                self.phase = 'confirm_close'
                self.closed_since = now
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


def communicate(server, key, shared, lock, commands, collections, finishing, stop):
    opener = build_opener(NoRedirects())
    controller_id = str(uuid4())
    previous = None
    pending_collection = None

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
            if pending_collection is None:
                try:
                    pending_collection = collections.get_nowait()
                except Empty:
                    pass
            if pending_collection is not None:
                post(f'/api/device/commands/{pending_collection}/completed',
                     {'controller_id': controller_id})
                pending_collection = None
                finishing.clear()
                print('Rental completed. Refresh My rental.', flush=True)
            post('/api/device/heartbeat', report)
            message = f"Connected to PayBox. Door: {report['door_state']}."
            if report['ready']:
                started = monotonic()
                result = post('/api/device/commands/next', {'controller_id': controller_id, 'supports_finish': True})
                command = result.get('command')
                if command and command.get('action') in ('open', 'finish'):
                    ttl = float(command['valid_for_seconds'])
                    # Start timing before the request, so network delays shorten validity.
                    deadline = started + min(10, max(0, ttl))
                    if monotonic() < deadline and not stop.is_set():
                        with lock:
                            shared['ready'] = False
                        commands.put((command['id'], deadline, command['action']))
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
    collections = Queue()
    finishing = Event()
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
                            args=(server, key, shared, lock, commands, collections, finishing, stop), daemon=True)
            worker.start()
            print('Controller started. Close the door to prepare the latch.')
            last_command = None
            collection_command = None
            while True:
                now = monotonic()
                closed = door.is_pressed
                requested = False
                try:
                    command_id, deadline, command_action = commands.get_nowait()
                    if command_id != last_command:
                        last_command = command_id
                        requested = now < deadline and cycle.ready(closed) and not finishing.is_set()
                        requested = requested and (command_action != 'finish' or closed)
                        if requested and command_action == 'finish':
                            collection_command = command_id
                            finishing.set()
                except Empty:
                    pass
                action = cycle.tick(closed, now, requested)
                if action == 'open':
                    servo.max()
                    print('Opening commanded (2250 us). Open the door, then close it.', flush=True)
                elif action == 'close':
                    servo.min()
                    print('Closing commanded (1500 us).', flush=True)
                elif action == 'secured':
                    if collection_command is not None:
                        collections.put(collection_command)
                        collection_command = None
                with lock:
                    shared['door_state'] = 'closed' if closed else 'open'
                    shared['ready'] = cycle.ready(closed) and not finishing.is_set()
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
