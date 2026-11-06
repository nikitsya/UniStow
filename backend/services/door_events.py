"""Receive read-only door telemetry for the physical locker."""
import logging
import os
from contextlib import asynccontextmanager
from threading import Event, Lock, Thread
from time import monotonic, time
from uuid import UUID, uuid4

from fastapi import HTTPException
from pubnub.callbacks import SubscribeCallback
from pubnub.enums import PNStatusCategory
from pubnub.pnconfiguration import PNConfiguration
from pubnub.pubnub import PubNub

from services.pubnub_tokens import COMMANDS as COMMAND_CHANNEL, EVENTS as CHANNEL, READER, REFRESH_SECONDS, LockerTokens

log = logging.getLogger('uvicorn.error')


def fresh_reading(timestamp):
    # Allow a small clock difference between PubNub and the local server.
    return -1 <= time() - timestamp < 15


class DoorEvents(SubscribeCallback):
    def __init__(self, probe_enabled=False):
        self.probe_enabled = probe_enabled
        self.probe_id = None
        self.probe_started = 0
        self.lock = Lock()
        self.pending_tests = {}
        self.timetoken = 0
        self.state = None
        self.controller = None
        self.pending_opens = {}

    def status(self, client, status):
        if status.category == PNStatusCategory.PNConnectedCategory:
            log.info('Listening on %s', CHANNEL)
            if self.probe_enabled and self.probe_id is None:
                self.probe_id = str(uuid4())
                self.probe_started = monotonic()
                payload = {'type': 'ping', 'id': self.probe_id}
                log.info('Sending locker ping %s', self.probe_id)
                client.publish().channel(COMMAND_CHANNEL).message(payload).should_store(False).pn_async(
                    self.ping_published
                )
        elif status.is_error():
            log.warning('Door subscription: %s', status.category.name)

    def ping_published(self, result, status):
        if status.is_error():
            log.warning('Locker ping publish failed: HTTP %s', status.status_code)

    def test_open(self, client, expires_at):
        command_id = str(uuid4())
        payload = {'type': 'test_open', 'id': command_id, 'expires_at': expires_at}
        self.send_and_wait(client, payload, self.pending_tests, 'The controller did not acknowledge the test in time.')
        return {'status': 'test_acknowledged', 'command_id': command_id}

    def open(self, client, command_id, expires_at, controller_id):
        payload = {'type': 'open', 'id': command_id, 'expires_at': expires_at, 'controller_id': controller_id}
        self.send_and_wait(client, payload, self.pending_opens, 'Delivery is uncertain. Check the locker.')
        return {'status': 'opening', 'command_id': command_id}

    def send_and_wait(self, client, payload, pending, timeout_message):
        command_id = payload['id']
        received = Event()
        with self.lock:
            pending[command_id] = received
        try:
            client.publish().channel(COMMAND_CHANNEL).message(payload).should_store(False).pn_async(
                self.ping_published
            )
            if not received.wait(max(0, min(5, payload['expires_at'] - time()))):
                raise HTTPException(504, timeout_message)
        finally:
            with self.lock:
                pending.pop(command_id, None)

    def message(self, client, event):
        if event.channel != CHANNEL or event.publisher != 'unistow-locker-4':
            return
        if isinstance(event.message, dict) and event.message.get('type') in ('test_open_ack', 'open_ack'):
            command_id = event.message.get('id')
            if isinstance(command_id, str):
                with self.lock:
                    pending = self.pending_tests if event.message['type'] == 'test_open_ack' else self.pending_opens
                    received = pending.get(command_id)
                    if received:
                        received.set()
            return
        if isinstance(event.message, dict) and event.message.get('type') == 'pong':
            if self.probe_started and event.message.get('id') == self.probe_id:
                log.info('Locker 4 pong %s (%.2f s)', self.probe_id, monotonic() - self.probe_started)
                self.probe_started = 0
            return
        if not isinstance(event.message, dict) or event.message.get('door_state') not in ('open', 'closed'):
            return
        stamp = int(event.timetoken)
        if not fresh_reading(stamp / 10_000_000):
            return
        with self.lock:
            if stamp <= self.timetoken:
                return
            self.timetoken = stamp
            self.state = event.message['door_state']
            self.controller = None
            controller = event.message.get('controller_id')
            if event.message.get('ready') is True and isinstance(controller, str):
                try:
                    if str(UUID(controller)) == controller:
                        self.controller = controller
                except ValueError:
                    pass
        log.info('Locker 4 door via PubNub: %s', event.message['door_state'])

    def ready_controller(self):
        with self.lock:
            if fresh_reading(self.timetoken / 10_000_000):
                return self.controller
        return None

    def latest(self, last_heartbeat):
        with self.lock:
            timestamp = self.timetoken / 10_000_000
            if last_heartbeat and last_heartbeat.timestamp() >= timestamp:
                return None
            if fresh_reading(timestamp):
                return self.state
        return None


def renew_reader(reader, issuer, stopped):
    while not stopped.is_set():
        try:
            reader.set_token(issuer.reader())
            # Subscribing again reconnects the native SDK with its current cursor.
            reader.subscribe().channels(CHANNEL).execute()
            log.info('Renewed the Locker 4 server token.')
            delay = REFRESH_SECONDS
        except Exception:
            log.warning('Locker 4 server token renewal failed. Retrying in 30 seconds.')
            delay = 30
        stopped.wait(delay)


@asynccontextmanager
async def lifespan(app):
    subscribe_key = os.getenv('PUBNUB_SUBSCRIBE_KEY')
    token = os.getenv('PUBNUB_READ_TOKEN')
    test_mode = os.getenv('UNISTOW_OPEN_TEST') == '1'
    physical = os.getenv('UNISTOW_PUBNUB_OPEN') == '1'
    automatic = physical and bool(os.getenv('PUBNUB_SECRET_KEY'))
    if test_mode and physical:
        raise RuntimeError('Choose either physical opening or delivery testing.')
    if (test_mode or physical) and not (
            subscribe_key and (automatic or token) and os.getenv('PUBNUB_PROBE_PUBLISH_KEY')):
        raise RuntimeError('Locker command delivery requires PubNub publishing and subscription credentials.')
    if not (subscribe_key or token):
        yield
        return
    if not (subscribe_key and (automatic or token)):
        raise RuntimeError('Set both PUBNUB_SUBSCRIBE_KEY and PUBNUB_READ_TOKEN.')
    config = PNConfiguration()
    config.publish_key = os.getenv('PUBNUB_PROBE_PUBLISH_KEY')
    config.subscribe_key = subscribe_key
    config.user_id = READER
    reader = PubNub(config)
    issuer = LockerTokens() if automatic else None
    stopped = Event()
    renewal = None
    try:
        if token:
            reader.set_token(token)
        events = DoorEvents(probe_enabled=bool(config.publish_key) and not (test_mode or physical))
        app.state.open_client = reader if physical else None
        app.state.open_test_client = reader if test_mode else None
        app.state.door_events = events
        reader.add_listener(events)
        if issuer:
            app.state.locker_tokens = issuer
            renewal = Thread(target=renew_reader, args=(reader, issuer, stopped), daemon=True)
            renewal.start()
        else:
            reader.subscribe().channels(CHANNEL).execute()
        yield
    finally:
        app.state.door_events = None
        app.state.open_test_client = None
        app.state.open_client = None
        stopped.set()
        if renewal:
            renewal.join()
            app.state.locker_tokens = None
            issuer.admin.stop()
        reader.stop()
