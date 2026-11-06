"""Receive read-only door telemetry for the physical locker."""
import logging
import os
from contextlib import asynccontextmanager
from threading import Lock
from time import time

from pubnub.callbacks import SubscribeCallback
from pubnub.enums import PNStatusCategory
from pubnub.pnconfiguration import PNConfiguration
from pubnub.pubnub import PubNub

CHANNEL = 'unistow.locker-4.events'
log = logging.getLogger('uvicorn.error')


class DoorEvents(SubscribeCallback):
    def __init__(self):
        self.lock = Lock()
        self.timetoken = 0
        self.state = None

    def status(self, client, status):
        if status.category == PNStatusCategory.PNConnectedCategory:
            log.info('Listening on %s', CHANNEL)
        elif status.is_error():
            log.warning('Door subscription: %s', status.category.name)

    def message(self, client, event):
        if event.channel != CHANNEL or event.publisher != 'unistow-locker-4':
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
    config.subscribe_key = subscribe_key
    config.user_id = 'unistow-mac-reader'
    reader = PubNub(config)
    try:
        reader.set_token(token)
        events = DoorEvents()
        app.state.door_events = events
        reader.add_listener(events)
        reader.subscribe().channels(CHANNEL).execute()
        yield
    finally:
        app.state.door_events = None
        reader.stop()
