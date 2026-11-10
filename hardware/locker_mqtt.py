"""Receive Locker 4 commands and publish sensor readings over authenticated MQTT."""
import json
from threading import Event
from time import monotonic, time
from uuid import UUID, uuid4

import paho.mqtt.client as mqtt

COMMANDS = 'unistow/locker-4/commands'
EVENTS = 'unistow/locker-4/events'


def acknowledge(data, seen, mode, controller_id, commands):
    kind = data.get('type')
    expected = {'physical': 'open', 'test': 'test_open'}.get(mode)
    if not expected or kind != expected:
        return None
    if kind == 'open' and (not controller_id or data.get('controller_id') != controller_id):
        return None
    now = time()
    deadline = monotonic()
    expires = data.get('expires_at')
    command_id = data.get('id')
    if not isinstance(expires, (int, float)) or not now < expires <= now + 10:
        return None
    try:
        if not isinstance(command_id, str) or str(UUID(command_id)) != command_id:
            return None
    except ValueError:
        return None
    for previous, expiry in list(seen.items()):
        if expiry <= now:
            del seen[previous]
    if command_id in seen:
        return None
    seen[command_id] = expires
    if kind == 'open':
        moved = Event()
        ttl = expires - now
        commands.put((command_id, deadline + ttl, 'open', moved))
        if not moved.wait(ttl):
            return None
    return {'type': kind + '_ack', 'id': command_id}


def run(config, shared, lock, commands, stop):
    mode = config.get('mode', 'telemetry')
    if mode not in ('physical', 'test', 'telemetry'):
        raise ValueError('Unknown MQTT controller mode.')
    controller_id = str(uuid4()) if mode == 'physical' else None
    seen = {}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, clean_session=True)
    client.username_pw_set('unistow-locker-4', config['password'])
    client.tls_set(ca_certs=config.get('ca_file') or None)

    def connected(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print('Locker MQTT connection refused:', reason_code, flush=True)
        elif mode != 'telemetry':
            client.subscribe(COMMANDS, qos=0)

    def message(client, userdata, event):
        if event.topic != COMMANDS or event.retain or stop.is_set():
            return
        try:
            payload = json.loads(event.payload)
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(payload, dict):
            ack = acknowledge(payload, seen, mode, controller_id, commands)
            if ack and not stop.is_set():
                client.publish(EVENTS, json.dumps(ack), qos=0, retain=False)

    client.on_connect = connected
    client.on_message = message
    client.connect_async(config['host'], int(config.get('port', 8883)), keepalive=30)
    client.loop_start()
    try:
        previous = None
        next_report = 0
        while not stop.is_set():
            with lock:
                reading = {'door_state': shared['door_state']}
                if controller_id:
                    reading.update(controller_id=controller_id, ready=shared['ready'])
            if client.is_connected() and (reading != previous or monotonic() >= next_report):
                report = dict(reading, observed_at=time())
                if client.publish(EVENTS, json.dumps(report), qos=0, retain=False).rc == mqtt.MQTT_ERR_SUCCESS:
                    previous = reading
                    next_report = monotonic() + 5
            stop.wait(0.1)
    finally:
        client.disconnect()
        client.loop_stop()
