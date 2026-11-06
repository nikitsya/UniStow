"""Refresh the controller token through the authenticated UniStow device API."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, build_opener

from run_locker import NoRedirects


def renew_tokens(server, key, stop):
    if not server.startswith('https://'):
        print('Token renewal requires HTTPS.', flush=True)
        return
    path = Path(__file__).with_name('device_config.json')
    opener = build_opener(NoRedirects())
    request = Request(server + '/api/device/pubnub-token', method='POST',
                      headers={'Authorization': 'Bearer ' + key})
    while not stop.is_set():
        try:
            with opener.open(request, timeout=10) as response:
                grant = json.load(response)
            token = grant['token']
            delay = grant['refresh_after']
            if not isinstance(token, str) or not token or not isinstance(delay, int) or not 1 <= delay <= 2700:
                raise ValueError('Invalid token response.')
            data = json.loads(path.read_text())
            data['pubnub']['token'] = token
            with NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
                tmp = Path(output.name)
                try:
                    json.dump(data, output, indent=2)
                    output.write('\n')
                    output.flush()
                    os.fsync(output.fileno())
                    os.replace(tmp, path)
                finally:
                    tmp.unlink(missing_ok=True)
            print('Renewed the Locker 4 device token.', flush=True)
        except Exception:
            print('Device token renewal failed. Retrying in 30 seconds.', flush=True)
            delay = 30
        stop.wait(delay)
