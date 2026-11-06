"""Receive read-only door telemetry for the physical locker."""
import logging
import os
from contextlib import asynccontextmanager
from threading import Lock
from time import monotonic, time
from uuid import uuid4

from pubnub.callbacks import SubscribeCallback
from pubnub.enums import PNStatusCategory
from pubnub.pnconfiguration import PNConfiguration
from pubnub.pubnub import PubNub

CHANNEL = 'unistow.locker-4.events'
COMMAND_CHANNEL = 'unistow.locker-4.commands'
log = logging.getLogger('uvicorn.error')


class DoorEvents(SubscribeCallback):
    def __init__(self, probe_enabled=False):
        self.probe_enabled = probe_enabled
        self.probe_id = None
        self.probe_started = 0
        self.lock = Lock()
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

    def message(self, client, event):
        if event.channel != CHANNEL or event.publisher != 'unistow-locker-4':
            return
        if isinstance(event.message, dict) and event.message.get('type') == 'pong':
            if self.probe_started and event.message.get('id') == self.probe_id:
                log.info('Locker 4 pong %s (%.2f s)', self.probe_id, monotonic() - self.probe_started)
                self.probe_started = 0
            return
        if not isinstance(event.message, dict) or event.message.get('door_state') not in ('open', 'closed'):
            return
        stamp = int(event.timetoken)
        if not 0 <= time() - stamp / 10_000_000 < 15:
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
            if 0 <= time() - timestamp < 15:
                return self.state
        return None


@asynccontextmanager
async def lifespan(app):
    subscribe_key = os.getenv('PUBNUB_SUBSCRIBE_KEY')
    token = os.getenv('PUBNUB_READ_TOKEN')
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
        events = DoorEvents(probe_enabled=bool(config.publish_key))
        app.state.door_events = events
        reader.add_listener(events)
        reader.subscribe().channels(CHANNEL).execute()
        yield
    finally:
        app.state.door_events = None
        reader.stop()
