import hashlib
import hmac
import json
import os
import secrets
import stripe
import time
import unittest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch

from database import engine, get_db
from main import app
from services.payment_confirmation import cancel_pending_reservation
from services.rentals import reserve_locker
from stripe_fakes import stripe_client as fake_stripe_client

SECRET = 'whsec_test_only_not_a_real_secret'


def signed(payload, timestamp=None):
    timestamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(SECRET.encode(), f'{timestamp}.'.encode() + payload, hashlib.sha256).hexdigest()
    return f't={timestamp},v1={digest}'


class WebhookSignatureTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_and_stale_signatures(self):
        payload = b'{"object":"event","livemode":false,"type":"ignored","id":"evt_test"}'
        with patch.dict(os.environ, {'STRIPE_WEBHOOK_SECRET': SECRET}):
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver',
                                   follow_redirects=True) as client:
                for signature in ['', 'invalid', signed(payload, int(time.time()) - 600)]:
                    self.assertEqual((await client.post('/api/stripe/webhook', content=payload,
                                                        headers={'stripe-signature': signature})).status_code, 400)
                self.assertEqual((await client.post('/api/stripe/webhook', content=payload,
                                                    headers={'stripe-signature': signed(payload)})).status_code, 200)
                live = payload.replace(b'false', b'true')
                self.assertEqual((await client.post('/api/stripe/webhook', content=live,
                                                    headers={'stripe-signature': signed(live)})).status_code, 400)


@unittest.skipUnless(os.environ.get('UNISTOW_TEST_POSTGRES') == '1',
                     'Requires local PostgreSQL; all fixture rows are rolled back')
class WebhookDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.addAsyncCleanup(engine.dispose)
        self.connection = await engine.connect()
        self.transaction = await self.connection.begin()
        token = secrets.token_hex(10)
        number = (await self.connection.execute(text('SELECT coalesce(max(number),0)+100 FROM lockers'))).scalar_one()
        self.locker = (await self.connection.execute(text(
            "INSERT INTO lockers(number,size,price_per_day_cents,is_enabled,last_seen_at) VALUES(:number,'small',100,true,CURRENT_TIMESTAMP) RETURNING id"),
            {'number': number})).scalar_one()
        user = (await self.connection.execute(
            text("INSERT INTO users(email,password_hash) VALUES(:email,'test-fixture') RETURNING id"),
            {'email': f'{token}@example.com'})).scalar_one()
        async with AsyncSession(bind=self.connection, join_transaction_mode='create_savepoint') as db:
            self.rental = await reserve_locker(db, user, self.locker, 2)
        self.session_id = 'cs_test_' + token
        await self.connection.execute(text(
            "INSERT INTO payments(rental_id,request_key,checkout_params,checkout_session_id) VALUES(:id,:key,'{}',:session)"),
            {'id': self.rental['id'], 'key': token, 'session': self.session_id})
        self.checkout: dict[str, str | bool | int | dict[str, str] | None] = {
            'id': self.session_id, 'livemode': False, 'mode': 'payment',
            'amount_total': 200, 'currency': 'eur', 'client_reference_id': str(self.rental['id']),
            'metadata': {'rental_id': str(self.rental['id'])}, 'status': 'complete',
            'payment_status': 'paid', 'payment_intent': 'pi_test_' + token}

        async def test_db():
            async with AsyncSession(bind=self.connection, join_transaction_mode='create_savepoint') as request_db:
                yield request_db

        app.dependency_overrides[get_db] = test_db
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.clear()
        await self.transaction.rollback()
        await self.connection.close()

    async def test_checkout_retry_reuses_saved_request(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        from fastapi import HTTPException
        from services.payments import create_checkout

        await self.connection.execute(text('DELETE FROM payments WHERE rental_id=:id'),
                                      {'id': self.rental['id']})
        checkout = SimpleNamespace(id=self.session_id, livemode=False, status='open',
                                   url='https://checkout.stripe.com/test')
        client = Mock()
        client.v1.checkout.sessions.create_async = AsyncMock(
            side_effect=[stripe.APIConnectionError('test timeout'), checkout])
        client.v1.checkout.sessions.retrieve_async = AsyncMock(return_value=checkout)
        with patch('services.payments.stripe_client', return_value=client):
            for attempt in range(3):
                async with AsyncSession(bind=self.connection, join_transaction_mode='create_savepoint') as db:
                    if attempt == 0:
                        with self.assertRaises(HTTPException) as error:
                            await create_checkout(db, self.rental['id'], self.rental['user_id'])
                        self.assertEqual(error.exception.status_code, 503)
                    else:
                        result = await create_checkout(db, self.rental['id'], self.rental['user_id'])
                        self.assertEqual(result, {'url': checkout.url})
        calls = client.v1.checkout.sessions.create_async.await_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertTrue(calls[0].kwargs['options']['idempotency_key'])
        self.assertEqual(calls[0].args[0]['metadata'], {'rental_id': str(self.rental['id'])})
        client.v1.checkout.sessions.retrieve_async.assert_awaited_once_with(self.session_id)

    async def send_event(self, suffix='paid', event_type='checkout.session.completed'):
        payload = json.dumps({'object': 'event', 'id': self.session_id + suffix,
                              'livemode': False, 'type': event_type,
                              'data': {'object': {'id': self.session_id}}}).encode()
        with patch.dict(os.environ, {'STRIPE_WEBHOOK_SECRET': SECRET}):
            with patch('services.payment_confirmation.retrieve_checkout', return_value=self.checkout):
                return await self.client.post('/api/stripe/webhook', content=payload,
                                              headers={'stripe-signature': signed(payload)})

    async def status(self):
        return (await self.connection.execute(text('SELECT status FROM rentals WHERE id=:id'),
                                              {'id': self.rental['id']})).scalar_one()

    async def test_cancel_before_checkout_releases_locker(self):
        await self.connection.execute(text('DELETE FROM payments WHERE rental_id=:id'), {'id': self.rental['id']})
        async with AsyncSession(bind=self.connection, join_transaction_mode='create_savepoint') as db:
            await cancel_pending_reservation(db, self.rental['id'], self.rental['user_id'])
            await cancel_pending_reservation(db, self.rental['id'], self.rental['user_id'])
            replacement = await reserve_locker(db, self.rental['user_id'], self.locker, 2)
        self.assertNotEqual(replacement['id'], self.rental['id'])
        self.assertEqual(await self.status(), 'cancelled')

    async def test_cancel_expires_existing_checkout(self):
        self.checkout.update(status='expired', payment_status='unpaid')
        with patch('services.payment_confirmation.stripe_client', return_value=fake_stripe_client()) as stripe_client:
            async with AsyncSession(bind=self.connection, join_transaction_mode='create_savepoint') as db:
                expire = stripe_client.return_value.v1.checkout.sessions.expire_async
                expire.return_value.to_dict.return_value = self.checkout
                await cancel_pending_reservation(db, self.rental['id'], self.rental['user_id'])
                expire.assert_called_once_with(self.session_id)
        self.assertEqual(await self.status(), 'cancelled')

    async def test_payment_duplicate_and_reordered_event(self):
        self.assertEqual((await self.send_event()).status_code, 200)
        self.assertEqual(await self.status(), 'active')
        self.assertEqual((await self.send_event()).status_code, 200)
        self.assertEqual(
            (await self.connection.execute(
                text('SELECT count(*) FROM payment_events WHERE checkout_session_id=:session'),
                {'session': self.session_id})).scalar_one(), 1)
        self.assertEqual((await self.send_event('old', 'checkout.session.expired')).status_code, 200)
        self.assertEqual(await self.status(), 'active')
        self.assertEqual((await self.connection.execute(text('SELECT status FROM payments WHERE rental_id=:id'),
                                                        {'id': self.rental['id']})).scalar_one(), 'paid')

    async def test_wrong_amount_currency_reference_and_live_mode(self):
        for field, value in [('amount_total', 1), ('currency', 'usd'), ('client_reference_id', '0'),
                             ('livemode', True)]:
            original = self.checkout[field]
            self.checkout[field] = value
            self.assertEqual((await self.send_event(field)).status_code, 409)
            self.assertEqual(await self.status(), 'pending')
            self.checkout[field] = original

    async def test_expired_checkout_releases_locker(self):
        self.checkout.update(status='expired', payment_status='unpaid', payment_intent=None)
        self.assertEqual((await self.send_event('expired', 'checkout.session.expired')).status_code, 200)
        self.assertEqual(await self.status(), 'cancelled')
        from services.lockers import read_lockers
        async with AsyncSession(bind=self.connection, join_transaction_mode='create_savepoint') as db:
            self.assertEqual((await read_lockers(db, self.locker))[0]['status'], 'available')

    async def test_unpaid_checkout_does_not_activate(self):
        self.checkout.update(payment_status='unpaid', payment_intent=None)
        self.assertEqual((await self.send_event()).status_code, 200)
        self.assertEqual(await self.status(), 'pending')

    async def prepare_saved_method(self):
        await self.connection.execute(text("""
                                           UPDATE rentals
                                           SET overdue_terms_version='2026-11-09'
                                           WHERE id = :id
                                           """), {'id': self.rental['id']})
        await self.connection.execute(text("""
                                           UPDATE payments
                                           SET checkout_params='{"payment_intent_data":{"setup_future_usage":"off_session"}}'
                                           WHERE rental_id = :id
                                           """), {'id': self.rental['id']})
        self.checkout['customer'] = 'cus_test'
        return {
            'id': self.checkout['payment_intent'], 'livemode': False,
            'status': 'succeeded', 'amount': 200, 'currency': 'eur',
            'setup_future_usage': 'off_session', 'customer': 'cus_test',
            'payment_method': {'id': 'pm_test', 'type': 'card', 'livemode': False, 'customer': 'cus_test'},
        }

    async def test_saves_verified_method_and_preserves_timestamp_on_retry(self):
        intent = await self.prepare_saved_method()
        with patch('services.payment_confirmation.stripe_client', return_value=fake_stripe_client()) as client:
            client.return_value.v1.payment_intents.retrieve_async.return_value.to_dict.return_value = intent
            self.assertEqual((await self.send_event()).status_code, 200)
            saved = (await self.connection.execute(text(
                "SELECT stripe_customer_id,stripe_payment_method_id,payment_method_saved_at FROM payments WHERE rental_id=:id"
            ), {'id': self.rental['id']})).one()
            self.assertEqual(saved[:2], ('cus_test', 'pm_test'))
            self.assertIsNotNone(saved[2])
            self.assertEqual((await self.send_event()).status_code, 200)
            self.assertEqual((await self.send_event('again')).status_code, 200)
            current = (await self.connection.execute(text(
                "SELECT payment_method_saved_at FROM payments WHERE rental_id=:id"
            ), {'id': self.rental['id']})).scalar_one()
            self.assertEqual(saved[2], current)
            self.assertEqual(await self.status(), 'active')

    async def test_rejects_wrong_customer_without_recording_method(self):
        intent = await self.prepare_saved_method()
        payment_method = intent['payment_method']
        assert isinstance(payment_method, dict)
        payment_method['customer'] = 'cus_someone_else'
        with patch('services.payment_confirmation.stripe_client', return_value=fake_stripe_client()) as client:
            client.return_value.v1.payment_intents.retrieve_async.return_value.to_dict.return_value = intent
            self.assertEqual((await self.send_event()).status_code, 409)
        self.assertEqual(await self.status(), 'pending')
        self.assertIsNone((await self.connection.execute(text(
            "SELECT stripe_payment_method_id FROM payments WHERE rental_id=:id"
        ), {'id': self.rental['id']})).scalar_one())

    async def test_method_lookup_timeout_can_be_retried(self):
        intent = await self.prepare_saved_method()
        with patch('services.payment_confirmation.stripe_client', return_value=fake_stripe_client()) as client:
            retrieve = client.return_value.v1.payment_intents.retrieve_async
            retrieve.side_effect = stripe.APIConnectionError('test timeout')
            self.assertEqual((await self.send_event()).status_code, 503)
            self.assertEqual(await self.status(), 'pending')
            retrieve.side_effect = None
            retrieve.return_value.to_dict.return_value = intent
            self.assertEqual((await self.send_event()).status_code, 200)
            self.assertEqual(await self.status(), 'active')

    async def test_old_consent_keeps_single_payment_behaviour(self):
        with patch('services.payment_confirmation.stripe_client', return_value=fake_stripe_client()) as client:
            self.assertEqual((await self.send_event()).status_code, 200)
            client.assert_not_called()
        self.assertIsNone((await self.connection.execute(text(
            "SELECT stripe_payment_method_id FROM payments WHERE rental_id=:id"
        ), {'id': self.rental['id']})).scalar_one())
