"""Publish one physical door reading to the Locker 4 PubNub channel."""
from pathlib import Path

import json
from http.client import HTTPException
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, build_opener


def publish_request(config, state):
    return event_request(config, {'door_state': state})


def event_request(config, payload):
    values = [config[name] for name in ('publish_key', 'subscribe_key', 'token')]
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError('Missing PubNub credentials.')
    publish_key, subscribe_key, token = values
    query = urlencode({'uuid': 'unistow-locker-4', 'auth': token, 'store': 0})
    url = (f'https://ps.pndsn.com/publish/{quote(publish_key, safe="")}/'
           f'{quote(subscribe_key, safe="")}/0/unistow.locker-4.events/0?{query}')
    return Request(url, data=json.dumps(payload).encode(),
                   headers={'Content-Type': 'application/json'}, method='POST')


def publish_error(error):
    try:
        payload = json.loads(error.read(8192))
    except (OSError, ValueError):
        payload = None
    reason = None
    if isinstance(payload, list) and len(payload) > 1:
        reason = payload[1]
    elif isinstance(payload, dict):
        reason = payload.get('message')
    # Only report known reasons. The response may echo private request data.
    reasons = ('wildcard maximum depth exceeded', 'invalid subscribe key',
               'invalid publish key', 'invalid key', 'invalid json',
               'invalid token', 'token is expired', 'forbidden')
    if isinstance(reason, str):
        for known in reasons:
            if known in reason.lower():
                return f'PubNub HTTP {error.code}: {known}.'
    return f'PubNub HTTP {error.code}: unrecognised error response.'


def publish(config_path, state, opener):
    try:
        request = publish_request(json.loads(config_path.read_text())['pubnub'], state)
    except (KeyError, ValueError, OSError, TypeError):
        raise ValueError('Check the pubnub settings in device_config.json.')
    try:
        with opener.open(request, timeout=5) as response:
            receipt = json.load(response)
    except HTTPError as error:
        raise ValueError(publish_error(error))
    except (OSError, ValueError, HTTPException):
        raise ValueError('Could not confirm delivery. Check the network and try again.')
    if not isinstance(receipt, list) or receipt[:2] != [1, 'Sent']:
        raise ValueError('PubNub did not acknowledge the reading.')


def report_changes(shared, lock, stop, opener):
    config_path = Path(__file__).with_name('device_config.json')
    published = None
    next_report = 0
    previous_error = None
    while not stop.is_set():
        with lock:
            state = shared['door_state']
        if state != published or monotonic() >= next_report:
            try:
                publish(config_path, state, opener)
            except ValueError as error:
                if str(error) != previous_error:
                    print(error, flush=True)
                    previous_error = str(error)
                stop.wait(5)
                continue
            published = state
            previous_error = None
            next_report = monotonic() + 5
        stop.wait(0.1)


def main():
    from gpiozero import Button
    from gpiozero.pins.pigpio import PiGPIOFactory

    config_path = Path(__file__).with_name('device_config.json')
    with PiGPIOFactory(host='localhost') as factory:
        with Button(17, pull_up=True, bounce_time=0.1, pin_factory=factory) as door:
            sleep(0.2)
            state = 'closed' if door.is_pressed else 'open'
    from run_locker import NoRedirects
    try:
        publish(config_path, state, build_opener(NoRedirects()))
    except ValueError as error:
        raise SystemExit(str(error))
    print(f'PubNub accepted door state: {state}.')


if __name__ == '__main__':
    main()
