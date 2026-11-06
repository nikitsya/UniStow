"""Reply to one server connectivity probe without accessing the GPIO pins."""
import json
from http.client import HTTPException
from pathlib import Path
from threading import Event
from time import monotonic, time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import build_opener
from uuid import UUID

from publish_door import event_request, publish_error
from run_locker import NoRedirects

CHANNEL = 'unistow.locker-4.commands'


def pong_for(event):
    data = event.get('d')
    if event.get('c') != CHANNEL or event.get('i') != 'unistow-mac-reader':
        return None
    if not isinstance(data, dict) or data.get('type') != 'ping':
        return None
    probe_id = data.get('id')
    if not isinstance(probe_id, str):
        return None
    try:
        if str(UUID(probe_id)) != probe_id:
            return None
    except ValueError:
        return None
    return {'type': 'pong', 'id': probe_id}


def subscribe_batch(config, opener, cursor):
    url = f"https://ps.pndsn.com/v2/subscribe/{quote(config['subscribe_key'], safe='')}/{CHANNEL}/0"
    query = {'uuid': 'unistow-locker-4', 'auth': config['token'], **cursor}
    with opener.open(url + '?' + urlencode(query), timeout=320) as response:
        return json.load(response)


def validated_command(event, seen, now, kind, controller_id=None):
    data = event.get('d')
    if event.get('c') != CHANNEL or event.get('i') != 'unistow-mac-reader':
        return None
    if not isinstance(data, dict) or data.get('type') != kind:
        return None
    if kind == 'open' and (not controller_id or data.get('controller_id') != controller_id):
        return None
    command_id = data.get('id')
    expires = data.get('expires_at')
    if not isinstance(expires, (float, int)) or not now < expires <= now + 10:
        return None
    if not isinstance(command_id, str):
        return None
    try:
        if str(UUID(command_id)) != command_id:
            return None
    except ValueError:
        return None
    for previous in list(seen):
        if seen[previous] <= now:
            del seen[previous]
    if command_id in seen:
        return None
    seen[command_id] = expires
    return data


def open_test_ack(event, seen, now):
    command = validated_command(event, seen, now, 'test_open')
    if command:
        return {'type': 'test_open_ack', 'id': command['id']}
    return None


def handle_open(event, seen, controller_id, commands):
    now = time()
    started = monotonic()
    command = validated_command(event, seen, now, 'open', controller_id)
    if command:
        moved = Event()
        ttl = command['expires_at'] - now
        commands.put((command['id'], started + ttl, 'open', moved))
        if moved.wait(ttl):
            return {'type': 'open_ack', 'id': command['id']}
    return None


def listen_commands(stop, controller_id=None, commands=None):
    opener = build_opener(NoRedirects())
    cursor = {'tt': '0'}
    seen = {}
    path = Path(__file__).with_name('device_config.json')
    while not stop.is_set():
        try:
            config = json.loads(path.read_text())['pubnub']
            batch = subscribe_batch(config, opener, cursor)
            cursor.update(tt=batch['t']['t'], tr=batch['t']['r'])
            for event in batch['m']:
                if controller_id:
                    ack = handle_open(event, seen, controller_id, commands)
                else:
                    ack = open_test_ack(event, seen, time())
                if ack and not stop.is_set():
                    with opener.open(event_request(config, ack), timeout=5) as response:
                        if json.load(response)[:2] != [1, 'Sent']:
                            raise ValueError('Acknowledgement rejected.')
                    print('Acknowledged', ack['type'], ack['id'], flush=True)
        except (OSError, HTTPException, ValueError, TypeError, KeyError):
            print('Command subscription unavailable. Check the token and network.', flush=True)
            stop.wait(5)


def main():
    config = json.loads(Path(__file__).with_name('device_config.json').read_text())['pubnub']
    query = {'tt': '0'}
    opener = build_opener(NoRedirects())
    while True:
        batch = subscribe_batch(config, opener, query)
        if query['tt'] == '0':
            print('Pi ready for ping.', flush=True)
        query.update(tt=batch['t']['t'], tr=batch['t']['r'])
        for event in batch['m']:
            pong = pong_for(event)
            if pong:
                with opener.open(event_request(config, pong), timeout=5) as response:
                    if json.load(response)[:2] != [1, 'Sent']:
                        raise SystemExit('Pong was not acknowledged.')
                print('Sent pong', pong['id'])
                return


if __name__ == '__main__':
    try:
        main()
    except HTTPError as error:
        raise SystemExit(publish_error(error))
    except (KeyError, ValueError, OSError):
        raise SystemExit('Check the Pi configuration and network, then retry the probe.')
