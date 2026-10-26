"""Forward Stripe test events locally without printing secret keys."""
import os
from pathlib import Path
import re
import shutil
import subprocess

from dotenv import dotenv_values, set_key


def main():
    env_file = Path(__file__).resolve().parent.parent / '.env'
    settings = dotenv_values(env_file)
    key = settings.get('STRIPE_SECRET_KEY', '')
    if not key or not key.startswith('sk_test_'):
        raise SystemExit('Set STRIPE_SECRET_KEY to a test key in .env first.')
    if not shutil.which('stripe'):
        raise SystemExit('Install Stripe CLI with: brew install stripe')
    environment = os.environ.copy()
    environment['STRIPE_API_KEY'] = key
    command = ['stripe', 'listen', '--skip-update', '--events',
               'checkout.session.completed,checkout.session.expired,checkout.session.async_payment_succeeded,checkout.session.async_payment_failed',
               '--forward-to', 'http://127.0.0.1:8000/api/stripe/webhook']
    process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    try:
        for line in process.stdout:
            secret = re.search(r'whsec_[A-Za-z0-9]+', line)
            if secret:
                set_key(str(env_file), 'STRIPE_WEBHOOK_SECRET', secret.group())
                print('Webhook secret saved. Restart the backend, then keep this terminal open.', flush=True)
            line = re.sub(r'(?:whsec_|(?:sk|rk)_(?:test|live)_)[A-Za-z0-9]+', '[hidden]', line)
            print(line.rstrip(), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        process.terminate()
        process.wait()


if __name__ == '__main__':
    main()
