"""Grant Locker 4 a one-hour physical opening token from the trusted Mac."""
import json
import re
import shlex
import subprocess
from getpass import getpass

from pubnub.models.consumer.v3.channel import Channel
from pubnub.pnconfiguration import PNConfiguration
from pubnub.pubnub import PubNub

remote = """
import json, os, sys, tempfile
from pathlib import Path
path = Path.home() / 'Desktop/device_config.json'
config = json.loads(path.read_text())
if not isinstance(config, dict) or not all(config.get(k) for k in ('server_url', 'device_key')):
    sys.exit('Expected existing locker configuration on the Pi Desktop.')
config['pubnub'] = json.load(sys.stdin)
with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
    name = output.name
    try:
        json.dump(config, output, indent=2)
        output.write('\\n')
        output.flush()
        os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
print('Updated', path)
"""
host = input('Pi hostname or IP: ').strip()
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]*', host):
    raise SystemExit('Enter only the Pi hostname or IP address.')
config = PNConfiguration()
config.user_id = 'unistow-test-admin'
config.publish_key = getpass('Publish Key: ').strip()
config.subscribe_key = getpass('Subscribe Key: ').strip()
config.secret_key = getpass('Secret Key: ').strip()
client = PubNub(config)
try:
    reply = (client.grant_token()
             .authorized_uuid('unistow-locker-4')
             .channels([Channel.id('unistow.locker-4.events').write(), Channel.id('unistow.locker-4.commands').read()])
             .ttl(60)
             .sync())
    if reply.status.is_error():
        raise SystemExit(f'Grant failed (HTTP {reply.status.status_code}).')
    device = dict(publish_key=config.publish_key, subscribe_key=config.subscribe_key,
                  token=reply.result.token, open_locker=True)
    subprocess.run(['ssh', '-T', 'nikitsya@' + host, 'python3 -c ' + shlex.quote(remote)],
                   input=json.dumps(device), text=True, check=True)
finally:
    client.stop()
