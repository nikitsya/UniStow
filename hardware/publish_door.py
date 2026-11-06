"""Publish one physical door reading to the Locker 4 PubNub channel."""
import json
from pathlib import Path
from time import sleep
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, build_opener

from run_locker import NoRedirects


def publish_request(config, state):
    values = [config[name] for name in ('publish_key', 'subscribe_key', 'token')]
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError('Missing PubNub credentials.')
    publish_key, subscribe_key, token = values
    query = urlencode({'uuid': 'unistow-locker-4', 'auth': token, 'store': 0})
    url = (f'https://ps.pndsn.com/publish/{quote(publish_key, safe="")}/'
           f'{quote(subscribe_key, safe="")}/0/unistow.locker.4.events/0?{query}')
    return Request(url, data=json.dumps({'door_state': state}).encode(),
                   headers={'Content-Type': 'application/json'}, method='POST')


def main():
    from gpiozero import Button
    from gpiozero.pins.pigpio import PiGPIOFactory

    config_path = Path(__file__).with_name('device_config.json')
    with PiGPIOFactory(host='localhost') as factory:
        with Button(17, pull_up=True, bounce_time=0.1, pin_factory=factory) as door:
            sleep(0.2)
            state = 'closed' if door.is_pressed else 'open'
    try:
        request = publish_request(json.loads(config_path.read_text())['pubnub'], state)
    except (KeyError, ValueError, OSError, TypeError):
        raise SystemExit('Check the pubnub settings in device_config.json.')
    try:
        with build_opener(NoRedirects()).open(request, timeout=5) as response:
            receipt = json.load(response)
    except HTTPError as error:
        raise SystemExit(f'PubNub returned HTTP {error.code}. Check the keys and token permissions.')
    except (OSError, ValueError):
        raise SystemExit('Could not confirm delivery. Check the network and try again.')
    if not isinstance(receipt, list) or receipt[:2] != [1, 'Sent']:
        raise SystemExit('PubNub did not acknowledge the reading.')
    print(f'PubNub accepted door state: {state}.')


if __name__ == '__main__':
    main()
