"""Issue scoped one-hour tokens for the two Locker 4 clients."""
import os
from threading import Lock
from time import monotonic

from pubnub.models.consumer.v3.channel import Channel
from pubnub.pnconfiguration import PNConfiguration
from pubnub.pubnub import PubNub

EVENTS = 'unistow.locker-4.events'
COMMANDS = 'unistow.locker-4.commands'
READER = 'unistow-mac-reader'
DEVICE = 'unistow-locker-4'
REFRESH_SECONDS = 45 * 60


class LockerTokens:
    def __init__(self):
        config = PNConfiguration()
        config.user_id = 'unistow-grant-admin'
        config.publish_key = os.environ['PUBNUB_PROBE_PUBLISH_KEY']
        config.subscribe_key = os.environ['PUBNUB_SUBSCRIBE_KEY']
        config.secret_key = os.environ['PUBNUB_SECRET_KEY']
        self.admin = PubNub(config)
        self.lock = Lock()
        self.device_token = None
        self.refresh_at = 0

    def grant(self, user, read_channel, write_channel):
        reply = (self.admin.grant_token().authorized_uuid(user).ttl(60)
                 .channels([Channel.id(read_channel).read(), Channel.id(write_channel).write()]).sync())
        if reply.status.is_error() or not reply.result.token:
            raise RuntimeError('PubNub token grant failed.')
        return reply.result.token

    def reader(self):
        return self.grant(READER, EVENTS, COMMANDS)

    def device(self):
        with self.lock:
            now = monotonic()
            if now >= self.refresh_at or self.device_token is None:
                self.device_token = self.grant(DEVICE, COMMANDS, EVENTS)
                self.refresh_at = now + REFRESH_SECONDS
            return {'token': self.device_token, 'refresh_after': max(1, int(self.refresh_at - monotonic()))}
