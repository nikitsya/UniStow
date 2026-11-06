"""Receive read-only door telemetry for the physical locker."""
import logging
import os
from contextlib import asynccontextmanager
from threading import Event, Lock
from time import monotonic, time
from uuid import uuid4

from fastapi import HTTPException
from pubnub.callbacks import SubscribeCallback
from pubnub.enums import PNStatusCategory
from pubnub.pnconfiguration import PNConfiguration
from pubnub.pubnub import PubNub

CHANNEL = 'unistow.locker-4.events'
COMMAND_CHANNEL = 'unistow.locker-4.commands'
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
        received = Event()
        with self.lock:
            self.pending_tests[command_id] = received
        try:
            client.publish().channel(COMMAND_CHANNEL).message(payload).should_store(False).pn_async(
                self.ping_published
            )
            if not received.wait(max(0, min(5, expires_at - time()))):
                raise HTTPException(504, 'The controller did not acknowledge the test in time.')
            return {'status': 'test_acknowledged', 'command_id': command_id}
        finally:
            with self.lock:
                self.pending_tests.pop(command_id, None)

    def message(self, client, event):
        if event.channel != CHANNEL or event.publisher != 'unistow-locker-4':
            return
        if isinstance(event.message, dict) and event.message.get('type') == 'test_open_ack':
            command_id = event.message.get('id')
            if isinstance(command_id, str):
                with self.lock:
                    received = self.pending_tests.get(command_id)
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
        log.info('Locker 4 door via PubNub: %s', event.message['door_state'])

    def latest(self, last_heartbeat):
        with self.lock:
            timestamp = self.timetoken / 10_000_000
            if last_heartbeat and last_heartbeat.timestamp() >= timestamp:
                return None
            if fresh_reading(timestamp):
                return self.state
        return None


@asynccontextmanager
async def lifespan(app):
    subscribe_key = os.getenv('PUBNUB_SUBSCRIBE_KEY')
    token = os.getenv('PUBNUB_READ_TOKEN')
    test_mode = os.getenv('UNISTOW_OPEN_TEST') == '1'
    if test_mode and not (subscribe_key and token and os.getenv('PUBNUB_PROBE_PUBLISH_KEY')):
        raise RuntimeError('Open test mode requires PubNub publishing and subscription credentials.')
    if not (subscribe_key or token):
        yield
        return
    if not (subscribe_key and token):
        raise RuntimeError('Set both PUBNUB_SUBSCRIBE_KEY and PUBNUB_READ_TOKEN.')
    config = PNConfiguration()
    config.publish_key = os.getenv('PUBNUB_PROBE_PUBLISH_KEY')
    config.subscribe_key = subscribe_key
    config.user_id = 'unistow-mac-reader'
    reader = PubNub(config)
    try:
        reader.set_token(token)
        events = DoorEvents(probe_enabled=bool(config.publish_key) and not test_mode)
        app.state.open_test_client = reader if test_mode else None
        app.state.door_events = events
        reader.add_listener(events)
        reader.subscribe().channels(CHANNEL).execute()
        yield
    finally:
        app.state.door_events = None
        app.state.open_test_client = None
        reader.stop()
