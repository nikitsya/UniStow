import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import stripe
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.checkout import checkout_fits
from services.payment_confirmation import cancel_pending_reservation
from services.payments import create_checkout
from services.rentals import rental_end
from stripe_fakes import stripe_client
from test_commands import CommandTestCase


class CheckoutWindowTests(unittest.TestCase):
    def test_window_before_and_after_the_irish_deadline_in_winter_and_summer(self):
        for month, utc_hour in ((1, 17), (7, 16)):
            for minute, allowed in ((28, True), (29, False), (45, False)):
                now = datetime(2026, month, 10, utc_hour, minute, tzinfo=timezone.utc)
                with self.subTest(now=now):
                    self.assertEqual(checkout_fits(rental_end(now, 1), now), allowed)
                    self.assertTrue(checkout_fits(rental_end(now, 2), now))
            now = datetime(2026, month, 10, utc_hour + 1, tzinfo=timezone.utc)
            self.assertTrue(checkout_fits(rental_end(now, 1), now))


@unittest.skipUnless(os.getenv('UNISTOW_TEST_POSTGRES') == '1', 'Requires isolated PostgreSQL schema')
class CheckoutRecoveryTests(CommandTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.connection.execute(text('DELETE FROM payments WHERE rental_id=:id'), {'id': self.rental_id})
        await self.connection.execute(text("UPDATE rentals SET status='pending' WHERE id=:id"),
                                      {'id': self.rental_id})
        self.now = await self.connection.scalar(text('SELECT clock_timestamp()'))
        self.provider = stripe_client()
        self.provider.v1.checkout.sessions.create_async.side_effect = self.create_session
        self.provider.v1.checkout.sessions.retrieve_async.side_effect = lambda _: self.response()
        self.provider.v1.checkout.sessions.expire_async.side_effect = self.expire_session
        self.accepted = {}
        self.fail_before = 0
        self.fail_after = 0
        self.session = None

    def response(self):
        return SimpleNamespace(**self.session, to_dict=lambda: dict(self.session))

    async def create_session(self, params, options):
        if self.fail_before:
            self.fail_before -= 1
            raise stripe.APIConnectionError('Request did not reach Stripe')
        key = options['idempotency_key']
        if key in self.accepted:
            if self.accepted[key] != params:
                raise stripe.IdempotencyError('Parameters differ from the accepted request')
            return self.response()
        if params['expires_at'] - self.now.timestamp() < 1800:
            raise stripe.InvalidRequestError('Expiry must be at least 30 minutes away', 'expires_at', http_status=400)
        self.accepted[key] = dict(params)
        self.session = {
            'id': 'cs_recovered', 'livemode': False, 'mode': 'payment', 'currency': 'eur',
            'amount_total': 200, 'client_reference_id': str(self.rental_id),
            'metadata': {'rental_id': str(self.rental_id)}, 'status': 'open',
            'payment_status': 'unpaid', 'url': 'https://checkout.stripe.com/test',
        }
        if self.fail_after:
            self.fail_after -= 1
            raise stripe.APIConnectionError('Response was lost after Stripe created the session')
        return self.response()

    async def expire_session(self, _session_id):
        self.session.update(status='expired', payment_status='unpaid')
        return self.response()

    async def pay(self):
        with patch('services.payments.stripe_client', return_value=self.provider), \
                patch('services.checkout.checkout_now', side_effect=lambda _db: self.now):
            async with AsyncSession(bind=self.connection, join_transaction_mode='create_savepoint') as db:
                return await create_checkout(db, self.rental_id, 1)

    async def cancel(self):
        with patch('services.payment_confirmation.stripe_client', return_value=self.provider), \
                patch('services.checkout.checkout_now', return_value=self.now):
            async with AsyncSession(bind=self.connection, join_transaction_mode='create_savepoint') as db:
                await cancel_pending_reservation(db, self.rental_id, 1)

    async def fail_first_request(self, *, after_creation=False):
        if after_creation:
            self.fail_after = 1
        else:
            self.fail_before = 1
        with self.assertRaises(HTTPException) as error:
            await self.pay()
        self.assertEqual(error.exception.status_code, 503)

    async def test_delayed_retry_repairs_rejected_expiry_with_the_same_key(self):
        await self.fail_first_request()
        self.now += timedelta(minutes=2)
        result = await self.pay()
        self.assertEqual(result['url'], self.session['url'])
        calls = self.provider.v1.checkout.sessions.create_async.await_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[1].kwargs, calls[2].kwargs)
        self.assertEqual(calls[2].args[0]['expires_at'], int((self.now + timedelta(minutes=31)).timestamp()))
        self.assertEqual(calls[2].args[0] | {'expires_at': calls[0].args[0]['expires_at']}, calls[0].args[0])
        self.assertEqual(len(self.accepted), 1)
        self.assertEqual(await self.connection.scalar(text('SELECT checkout_session_id FROM payments')), 'cs_recovered')

    async def test_repeated_expiry_rejection_stops_after_one_repair(self):
        await self.fail_first_request()
        self.now += timedelta(minutes=2)

        async def reject_after_delay(_params, **_kwargs):
            self.now += timedelta(minutes=2)
            raise stripe.InvalidRequestError('Expiry is too soon', 'expires_at', http_status=400)

        self.provider.v1.checkout.sessions.create_async.side_effect = reject_after_delay
        with self.assertRaises(HTTPException) as error:
            await self.pay()
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.provider.v1.checkout.sessions.create_async.await_count, 3)
        self.assertFalse(self.accepted)
        self.assertEqual(await self.connection.scalar(text('SELECT status FROM payments')), 'pending')
        self.assertEqual(await self.connection.scalar(text('SELECT status FROM rentals')), 'pending')

    async def test_lost_success_is_replayed_without_changing_expiry_or_creating_again(self):
        await self.fail_first_request(after_creation=True)
        self.now += timedelta(minutes=2)
        await self.pay()
        calls = self.provider.v1.checkout.sessions.create_async.await_args_list
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(len(self.accepted), 1)

    async def test_lost_response_after_expiry_repair_keeps_the_repaired_request(self):
        await self.fail_first_request()
        self.now += timedelta(minutes=2)
        self.fail_after = 1
        with self.assertRaises(HTTPException):
            await self.pay()
        saved = await self.connection.scalar(text('SELECT checkout_params FROM payments'))
        self.now += timedelta(minutes=2)
        await self.pay()
        calls = self.provider.v1.checkout.sessions.create_async.await_args_list
        self.assertEqual(calls[2], calls[3])
        self.assertEqual(saved, calls[3].args[0])
        self.assertEqual(len(self.accepted), 1)

    async def test_cancel_recovers_a_lost_success_then_expires_that_session(self):
        await self.fail_first_request(after_creation=True)
        self.now += timedelta(minutes=2)
        await self.cancel()
        self.assertEqual(len(self.accepted), 1)
        self.provider.v1.checkout.sessions.expire_async.assert_awaited_once_with('cs_recovered')
        self.assertEqual(await self.connection.scalar(text('SELECT status FROM rentals')), 'cancelled')

    async def test_cancel_releases_a_rejected_request_that_never_created_a_session(self):
        await self.fail_first_request()
        self.now += timedelta(minutes=2)
        await self.cancel()
        self.assertFalse(self.accepted)
        self.assertEqual(await self.connection.scalar(text('SELECT status FROM rentals')), 'cancelled')
        self.assertEqual(await self.connection.scalar(text('SELECT status FROM payments')), 'expired')

    async def test_cancellation_timeout_keeps_the_reservation_and_original_request(self):
        await self.fail_first_request(after_creation=True)
        self.fail_before = 1
        with self.assertRaises(HTTPException) as error:
            await self.cancel()
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(await self.connection.scalar(text('SELECT status FROM rentals')), 'pending')
        self.provider.v1.checkout.sessions.expire_async.assert_not_called()
        await self.cancel()
        self.assertEqual(await self.connection.scalar(text('SELECT status FROM rentals')), 'cancelled')

    async def test_rejected_checkout_after_cutoff_releases_the_unpaid_reservation(self):
        await self.fail_first_request()
        self.now = self.rental['paid_until'] - timedelta(minutes=20)
        # Keep this deadline test inside Stripe's retry retention window.
        await self.connection.execute(text('UPDATE payments SET created_at=:created WHERE rental_id=:id'),
                                      {'created': self.now - timedelta(minutes=20), 'id': self.rental_id})
        with self.assertRaises(HTTPException) as error:
            await self.pay()
        self.assertEqual(error.exception.detail, {
            'code': 'checkout_expired',
            'message': 'This unpaid reservation has expired. Please choose a locker and rental period again.',
        })
        self.assertFalse(self.accepted)
        self.assertEqual(await self.connection.scalar(text('SELECT status FROM rentals')), 'cancelled')

    async def test_unrelated_stripe_errors_do_not_repair_or_release_the_request(self):
        await self.fail_first_request()
        self.now += timedelta(minutes=2)
        original = await self.connection.scalar(text('SELECT checkout_params FROM payments'))
        for error in (stripe.InvalidRequestError('Invalid return URL', 'success_url', http_status=400),
                      stripe.IdempotencyError('Request still in use')):
            self.provider.v1.checkout.sessions.create_async.side_effect = error
            with self.assertRaises(HTTPException):
                await self.pay()
            self.assertEqual(await self.connection.scalar(text('SELECT status FROM rentals')), 'pending')
            self.assertEqual(await self.connection.scalar(text('SELECT checkout_params FROM payments')), original)

    async def test_request_older_than_retry_window_requires_review_without_a_new_session(self):
        await self.fail_first_request()
        self.now += timedelta(hours=23, seconds=1)
        with self.assertRaises(HTTPException) as error:
            await self.pay()
        self.assertEqual(error.exception.status_code, 409)
        self.provider.v1.checkout.sessions.create_async.assert_awaited_once()

    async def test_paid_lost_response_is_not_cancelled_when_recovered(self):
        await self.fail_first_request(after_creation=True)
        self.now += timedelta(minutes=2)
        self.session.update(status='complete', payment_status='paid', payment_intent='pi_paid')
        self.provider.v1.checkout.sessions.expire_async.side_effect = stripe.InvalidRequestError('Already paid', None)
        with patch('services.payment_confirmation.retrieve_checkout', return_value=self.session):
            with self.assertRaises(HTTPException):
                await self.cancel()
        self.assertEqual(await self.connection.scalar(text('SELECT status FROM payments')), 'paid')
        self.assertEqual(await self.connection.scalar(text('SELECT status FROM rentals')), 'active')

    async def test_api_rejects_a_short_period_without_creating_a_reservation(self):
        with patch('services.rentals.rental_end', return_value=self.now + timedelta(minutes=20)):
            response = await self.client.post('/api/rentals', headers={'X-UniStow-Request': '1'},
                                              json={'locker_id': 2, 'days': 1, 'overdue_consent': True})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail']['code'], 'rental_period_too_short')
        self.assertEqual(await self.connection.scalar(text('SELECT count(*) FROM rentals')), 1)

    async def test_booking_page_explains_the_cutoff_and_restores_one_day_after_1800(self):
        for hour, minute, minimum in ((17, 28, 1), (17, 29, 2), (17, 45, 2), (18, 0, 1)):
            with self.subTest(hour=hour, minute=minute), patch('routes.pages.datetime') as clock:
                clock.now.return_value = datetime(2026, 11, 28, hour, minute, tzinfo=timezone.utc)
                page = await self.client.get('/lockers/2/rent')
            self.assertEqual(page.status_code, 200)
            self.assertIn(f'min="{minimum}"', page.text)
            self.assertIn(f'value="{minimum}"', page.text)
            self.assertEqual('One-day rentals are unavailable' in page.text, minimum == 2)

    async def test_unstarted_checkout_at_cutoff_is_disabled_but_can_be_cancelled(self):
        with patch('routes.pages.datetime') as clock:
            clock.now.return_value = self.rental['paid_until'] - timedelta(minutes=20)
            page = await self.client.get(f'/rentals/{self.rental_id}')
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-checkout-blocked="true"', page.text)
        self.assertIn('There is not enough time to pay', page.text)
        self.assertIn('id="cancel-reservation"', page.text)
        await self.cancel()
        self.assertEqual(await self.connection.scalar(text('SELECT status FROM rentals')), 'cancelled')
