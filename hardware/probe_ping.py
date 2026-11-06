"""Reply to one server connectivity probe without accessing the GPIO pins."""
import json
from pathlib import Path
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


def main():
    config = json.loads(Path(__file__).with_name('device_config.json').read_text())['pubnub']
    url = f"https://ps.pndsn.com/v2/subscribe/{quote(config['subscribe_key'], safe='')}/{CHANNEL}/0"
    query = {'uuid': 'unistow-locker-4', 'auth': config['token'], 'tt': '0'}
    opener = build_opener(NoRedirects())
    while True:
        with opener.open(url + '?' + urlencode(query), timeout=320) as response:
            batch = json.load(response)
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
