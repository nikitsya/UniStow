import hashlib
import hmac
import json
import os
import secrets
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import engine, get_db
from main import app
from services.rentals import reserve_locker

SECRET = 'whsec_test_only_not_a_real_secret'


def signed(payload, timestamp=None):
    timestamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(SECRET.encode(), f'{timestamp}.'.encode() + payload, hashlib.sha256).hexdigest()
    return f't={timestamp},v1={digest}'


class WebhookSignatureTests(unittest.TestCase):
    def test_invalid_and_stale_signatures(self):
        payload = b'{"object":"event","livemode":false,"type":"ignored","id":"evt_test"}'
        with patch.dict(os.environ, {'STRIPE_WEBHOOK_SECRET': SECRET}), TestClient(app) as client:
            for signature in ['', 'invalid', signed(payload, int(time.time()) - 600)]:
                self.assertEqual(client.post('/api/stripe/webhook', content=payload,
                                             headers={'stripe-signature': signature}).status_code, 400)
            self.assertEqual(client.post('/api/stripe/webhook', content=payload,
                                         headers={'stripe-signature': signed(payload)}).status_code, 200)
            live = payload.replace(b'false', b'true')
            self.assertEqual(client.post('/api/stripe/webhook', content=live,
                                         headers={'stripe-signature': signed(live)}).status_code, 400)


@unittest.skipUnless(os.environ.get('PAYBOX_TEST_POSTGRES') == '1',
                     'Requires local PostgreSQL; all fixture rows are rolled back')
class WebhookDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.connection = engine.connect()
        self.transaction = self.connection.begin()
        token = secrets.token_hex(10)
        number = self.connection.execute(text('SELECT coalesce(max(number),0)+100 FROM lockers')).scalar_one()
        self.locker = self.connection.execute(text(
            "INSERT INTO lockers(number,size,price_per_day_cents,is_enabled) VALUES(:number,'small',100,true) RETURNING id"),
            {'number': number}).scalar_one()
        user = self.connection.execute(
            text("INSERT INTO users(email,password_hash) VALUES(:email,'test-fixture') RETURNING id"),
            {'email': f'{token}@example.com'}).scalar_one()
        with Session(bind=self.connection, join_transaction_mode='create_savepoint') as db:
            self.rental = reserve_locker(db, user, self.locker, 2)
        self.session_id = 'cs_test_' + token
        self.connection.execute(text(
            "INSERT INTO payments(rental_id,request_key,checkout_params,checkout_session_id) VALUES(:id,:key,'{}',:session)"),
            {'id': self.rental['id'], 'key': token, 'session': self.session_id})
        self.checkout = {'id': self.session_id, 'livemode': False, 'mode': 'payment',
                         'amount_total': 200, 'currency': 'eur', 'client_reference_id': str(self.rental['id']),
                         'metadata': {'rental_id': str(self.rental['id'])}, 'status': 'complete',
                         'payment_status': 'paid', 'payment_intent': 'pi_test_' + token}

        def test_db():
            with Session(bind=self.connection, join_transaction_mode='create_savepoint') as db:
                yield db

        app.dependency_overrides[get_db] = test_db
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.transaction.rollback()
        self.connection.close()

    def send_event(self, suffix='paid', event_type='checkout.session.completed'):
        payload = json.dumps({'object': 'event', 'id': self.session_id + suffix,
                              'livemode': False, 'type': event_type,
                              'data': {'object': {'id': self.session_id}}}).encode()
        with patch.dict(os.environ, {'STRIPE_WEBHOOK_SECRET': SECRET}), patch(
                'services.payment_confirmation.retrieve_checkout',
                                                                              return_value=self.checkout):
            return self.client.post('/api/stripe/webhook', content=payload,
                                    headers={'stripe-signature': signed(payload)})

    def status(self):
        return self.connection.execute(text('SELECT status FROM rentals WHERE id=:id'),
                                       {'id': self.rental['id']}).scalar_one()

    def test_payment_duplicate_and_reordered_event(self):
        self.assertEqual(self.send_event().status_code, 200)
        self.assertEqual(self.status(), 'active')
        self.assertEqual(self.send_event().status_code, 200)
        self.assertEqual(
            self.connection.execute(text('SELECT count(*) FROM payment_events WHERE checkout_session_id=:session'),
                                    {'session': self.session_id}).scalar_one(), 1)
        self.assertEqual(self.send_event('old', 'checkout.session.expired').status_code, 200)
        self.assertEqual(self.status(), 'active')
        self.assertEqual(self.connection.execute(text('SELECT status FROM payments WHERE rental_id=:id'),
                                                 {'id': self.rental['id']}).scalar_one(), 'paid')

    def test_wrong_amount_currency_reference_and_live_mode(self):
        for field, value in [('amount_total', 1), ('currency', 'usd'), ('client_reference_id', '0'),
                             ('livemode', True)]:
            original = self.checkout[field]
            self.checkout[field] = value
            self.assertEqual(self.send_event(field).status_code, 409)
            self.assertEqual(self.status(), 'pending')
            self.checkout[field] = original

    def test_expired_checkout_releases_locker(self):
        self.checkout.update(status='expired', payment_status='unpaid', payment_intent=None)
        self.assertEqual(self.send_event('expired', 'checkout.session.expired').status_code, 200)
        self.assertEqual(self.status(), 'cancelled')
        from lockers import read_lockers
        with Session(bind=self.connection, join_transaction_mode='create_savepoint') as db:
            self.assertEqual(read_lockers(db, self.locker)[0]['status'], 'available')

    def test_unpaid_checkout_does_not_activate(self):
        self.checkout.update(payment_status='unpaid', payment_intent=None)
        self.assertEqual(self.send_event().status_code, 200)
        self.assertEqual(self.status(), 'pending')
