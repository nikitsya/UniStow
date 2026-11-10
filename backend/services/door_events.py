"""Exchange short-lived Locker 4 messages with the self-hosted MQTT broker."""
import json
import logging
import os
from contextlib import asynccontextmanager
from threading import Event, Lock
from time import time
from uuid import UUID, uuid4

import paho.mqtt.client as mqtt
from fastapi import HTTPException

EVENTS = 'unistow/locker-4/events'
COMMANDS = 'unistow/locker-4/commands'
log = logging.getLogger('uvicorn.error')


def fresh_reading(timestamp):
    return -1 <= time() - timestamp < 15


class DoorEvents:
    def __init__(self):
        self.lock = Lock()
        self.timestamp = 0
        self.state = None
        self.controller = None
        self.pending = {}

    def connected(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning('MQTT broker refused the server connection: %s', reason_code)
            return
        client.subscribe(EVENTS, qos=0)

    def disconnected(self, client, userdata, flags, reason_code, properties):
        with self.lock:
            self.timestamp = 0
            self.controller = None
            self.state = None

    def message(self, client, userdata, event):
        if event.topic != EVENTS or event.retain:
            return
        try:
            data = json.loads(event.payload)
        except (UnicodeDecodeError, ValueError):
            return
        if not isinstance(data, dict):
            return
        kind = data.get('type')
        if kind in ('test_open_ack', 'open_ack'):
            command_id = data.get('id')
            if isinstance(command_id, str):
                with self.lock:
                    received = self.pending.get((kind, command_id))
                    if received:
                        received.set()
            return
        state, stamp = data.get('door_state'), data.get('observed_at')
        if state not in ('open', 'closed') or not isinstance(stamp, (int, float)) or not fresh_reading(stamp):
            return
        controller = data.get('controller_id')
        try:
            ready = data.get('ready') is True and isinstance(controller, str) and str(UUID(controller)) == controller
        except ValueError:
            ready = False
        with self.lock:
            if stamp > self.timestamp:
                self.timestamp = stamp
                self.state = state
                self.controller = controller if ready else None

    def ready_controller(self):
        with self.lock:
            return self.controller if fresh_reading(self.timestamp) else None

    def latest(self, last_heartbeat):
        with self.lock:
            if last_heartbeat and last_heartbeat.timestamp() >= self.timestamp:
                return None
            return self.state if fresh_reading(self.timestamp) else None

    def test_open(self, client, expires_at):
        command_id = str(uuid4())
        payload = {'type': 'test_open', 'id': command_id, 'expires_at': expires_at}
        self.send_and_wait(client, payload, 'The controller did not acknowledge the test in time.')
        return {'status': 'test_acknowledged', 'command_id': command_id}

    def open(self, client, command_id, expires_at, controller_id):
        payload = {'type': 'open', 'id': command_id, 'expires_at': expires_at, 'controller_id': controller_id}
        self.send_and_wait(client, payload, 'Delivery is uncertain. Check the locker.')
        return {'status': 'opening', 'command_id': command_id}

    def send_and_wait(self, client, payload, timeout_message):
        key = (payload['type'] + '_ack', payload['id'])
        received = Event()
        with self.lock:
            self.pending[key] = received
        try:
            receipt = client.publish(COMMANDS, json.dumps(payload), qos=0, retain=False)
            if receipt.rc != mqtt.MQTT_ERR_SUCCESS:
                raise HTTPException(503, 'The locker broker is unavailable.')
            if not received.wait(max(0, min(5, payload['expires_at'] - time()))):
                raise HTTPException(504, timeout_message)
        finally:
            with self.lock:
                self.pending.pop(key, None)


@asynccontextmanager
async def lifespan(app):
    host = os.getenv('MQTT_HOST')
    physical = os.getenv('UNISTOW_MQTT_OPEN') == '1'
    test_mode = os.getenv('UNISTOW_OPEN_TEST') == '1'
    if physical and test_mode:
        raise RuntimeError('Choose either physical opening or delivery testing.')
    if not host and not (physical or test_mode):
        yield
        return
    password = os.getenv('MQTT_APP_PASSWORD')
    if not host or not password:
        raise RuntimeError('Set MQTT_HOST and MQTT_APP_PASSWORD for Locker 4.')
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, clean_session=True)
    client.username_pw_set('unistow-app', password)
    client.tls_set(ca_certs=os.getenv('MQTT_CA_FILE') or None)
    events = DoorEvents()
    client.on_connect = events.connected
    client.on_disconnect = events.disconnected
    client.on_message = events.message
    app.state.open_client = client if physical else None
    app.state.open_test_client = client if test_mode else None
    app.state.door_events = events
    client.connect_async(host, int(os.getenv('MQTT_PORT', '8883')), keepalive=30)
    client.loop_start()
    try:
        yield
    finally:
        client.disconnect()
        client.loop_stop()
        app.state.open_client = app.state.open_test_client = app.state.door_events = None
