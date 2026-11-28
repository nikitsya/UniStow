import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import test_commands
from services.booking_eligibility import booking_eligibility
from services.overdue_payments import apply_recovery, create_outstanding_checkout, reconcile_outstanding
from stripe_fakes import stripe_client as fake_stripe_client


@unittest.skipUnless(os.getenv('UNISTOW_TEST_POSTGRES') == '1', 'Requires isolated PostgreSQL schema')
class OutstandingPaymentTests(test_commands.CommandTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.connection.execute(text("""
                                           INSERT INTO overdue_charges(rental_id, charge_date, status,
                                                                       stripe_payment_intent_id, request_params)
                                           VALUES (:id, CURRENT_DATE, 'failed', 'pi_declined',
                                                   '{"customer":"cus_test","payment_method":"pm_test","metadata":{}}')
                                           """), {'id': self.rental_id})
        self.db = AsyncSession(bind=self.connection, join_transaction_mode='create_savepoint')
        self.addAsyncCleanup(self.db.close)
        self.client_stripe = fake_stripe_client()
        self.intent = {'id': 'pi_declined', 'livemode': False, 'amount': 100, 'currency': 'eur',
                       'customer': 'cus_test', 'payment_method': None, 'metadata': {},
                       'status': 'requires_payment_method',
                       'last_payment_error': {'payment_method': {'id': 'pm_test'}}}
        self.client_stripe.v1.payment_intents.retrieve_async.side_effect = lambda _: self.wrap(self.intent)
        self.client_stripe.v1.payment_intents.cancel_async.side_effect = self.cancel
        self.client_stripe.v1.checkout.sessions.create_async.side_effect = self.create
        self.client_stripe.v1.checkout.sessions.retrieve_async.side_effect = lambda _: self.wrap(self.checkout)
        mock = patch('services.overdue_payments.stripe_client', return_value=self.client_stripe)
        mock.start()
        self.addCleanup(mock.stop)

    @staticmethod
    def wrap(value):
        return SimpleNamespace(**value, to_dict=lambda: dict(value))

    def cancel(self, *_args, **_kwargs):
        self.intent['status'] = 'canceled'
        return self.wrap(self.intent)

    def create(self, params, **_options):
        self.checkout = {'id': 'cs_recovery', 'livemode': False, 'mode': 'payment', 'currency': 'eur',
                         'amount_total': params['line_items'][0]['price_data']['unit_amount'],
                         'metadata': params['metadata'], 'client_reference_id': params['client_reference_id'],
                         'status': 'open', 'payment_status': 'unpaid', 'url': 'https://checkout.stripe.com/test'}
        return self.wrap(self.checkout)

    def pay(self):
        self.checkout.update(status='complete', payment_status='paid', payment_intent='pi_recovery')
        self.intent = {'id': 'pi_recovery', 'livemode': False, 'status': 'succeeded',
                       'currency': 'eur', 'amount_received': 100, 'metadata': self.checkout['metadata']}

    async def test_repeated_clicks_reuse_checkout_and_paid_events_are_idempotent(self):
        self.assertEqual((await booking_eligibility(self.db, 1))['unpaid_cents'], 100)
        first = await create_outstanding_checkout(self.db, self.rental_id, 1)
        eligibility = await booking_eligibility(self.db, 1)
        self.assertEqual((eligibility['unpaid_cents'], eligibility['processing_cents']), (0, 100))
        self.assertFalse(eligibility['can_book'])
        self.assertEqual(await create_outstanding_checkout(self.db, self.rental_id, 1), first)
        self.client_stripe.v1.checkout.sessions.create_async.assert_called_once()
        self.client_stripe.v1.payment_intents.cancel_async.assert_called_once()
        self.pay()
        await apply_recovery(self.db, self.checkout)
        await apply_recovery(self.db, self.checkout)
        await apply_recovery(self.db, self.checkout | {'status': 'expired', 'payment_status': 'unpaid'})
        self.assertTrue((await booking_eligibility(self.db, 1))['can_book'])
        for _ in range(2):
            self.client.cookies.clear()
            self.client.cookies.set('unistow_session', 'session-1')
            self.assertNotIn('Balance to settle', (await self.client.get('/rentals')).text)
            self.assertIn('id="rental-form"', (await self.client.get('/lockers/2/rent')).text)
        self.assertEqual(await self.db.scalar(text('SELECT status FROM overdue_charges')), 'paid')
        self.assertEqual(await self.db.scalar(text('SELECT status FROM overdue_payments')), 'paid')
        self.assertEqual(await self.db.scalar(text('SELECT stripe_payment_intent_id FROM overdue_charges')),
                         'pi_declined')
        with self.assertRaises(HTTPException):
            await create_outstanding_checkout(self.db, self.rental_id, 1)

    async def test_completed_rental_keeps_debt_visible_and_can_be_paid(self):
        await self.connection.execute(
            text("UPDATE rentals SET status='completed',completed_at=clock_timestamp() WHERE id=:id"),
            {'id': self.rental_id})
        page = await self.client.get(f'/rentals/{self.rental_id}')
        self.assertIn('Pay outstanding €1.00', page.text)
        self.assertNotIn('id="finish-rental-button"', page.text)
        self.assertIn(f'/rentals/{self.rental_id}', (await self.client.get('/rentals')).text)
        await create_outstanding_checkout(self.db, self.rental_id, 1)
        self.pay()
        await reconcile_outstanding(self.db, self.rental_id, 1)
        self.assertEqual(await self.db.scalar(text('SELECT status FROM rentals')), 'completed')
        page = await self.client.get(f'/rentals/{self.rental_id}')
        self.assertNotIn('Pay outstanding', page.text)

    async def test_other_user_and_missing_browser_header_are_rejected(self):
        url = f'/api/rentals/{self.rental_id}/outstanding/checkout'
        self.assertEqual((await self.client.post(url)).status_code, 403)
        self.client.cookies.set('unistow_session', 'session-2')
        self.assertEqual((await self.client.post(url, headers={'X-UniStow-Request': '1'})).status_code, 404)
        self.client_stripe.v1.checkout.sessions.create_async.assert_not_called()

    async def test_pending_charges_are_not_recovered(self):
        await self.connection.execute(
            text("UPDATE overdue_charges SET status='pending' WHERE rental_id=:id"),
            {'id': self.rental_id})
        with self.assertRaises(HTTPException):
            await create_outstanding_checkout(self.db, self.rental_id, 1)
        self.client_stripe.v1.checkout.sessions.create_async.assert_not_called()

    async def test_mismatched_amount_or_live_session_cannot_clear_debt(self):
        await create_outstanding_checkout(self.db, self.rental_id, 1)
        self.pay()
        for change in ({'amount_total': 200}, {'livemode': True}, {'id': 'cs_wrong'}):
            with self.assertRaises(HTTPException):
                await apply_recovery(self.db, self.checkout | change)
            await self.db.rollback()
        self.assertEqual(await self.db.scalar(text('SELECT status FROM overdue_charges')), 'failed')

    async def test_processing_original_payment_cannot_be_replaced(self):
        self.intent.update(status='processing', payment_method='pm_test')
        with self.assertRaises(HTTPException):
            await create_outstanding_checkout(self.db, self.rental_id, 1)
        self.client_stripe.v1.checkout.sessions.create_async.assert_not_called()

    async def test_browser_return_does_not_clear_debt(self):
        await create_outstanding_checkout(self.db, self.rental_id, 1)
        self.assertEqual((await self.client.get(f'/rentals/{self.rental_id}?outstanding_returned=1')).status_code, 200)
        self.assertEqual(await self.db.scalar(text('SELECT status FROM overdue_charges')), 'failed')
        self.assertFalse((await booking_eligibility(self.db, 1))['can_book'])

    async def test_unpaid_expiry_preserves_debt_and_allows_another_checkout(self):
        await create_outstanding_checkout(self.db, self.rental_id, 1)
        self.checkout['status'] = 'expired'
        await apply_recovery(self.db, self.checkout)
        self.assertEqual(await self.db.scalar(text('SELECT status FROM overdue_payments')), 'expired')
        self.assertEqual(await self.db.scalar(text('SELECT status FROM overdue_charges')), 'failed')
        old_create = self.create

        def new_create(params, options):
            old_create(params, options=options)
            self.checkout['id'] = 'cs_new'
            return self.wrap(self.checkout)

        self.client_stripe.v1.checkout.sessions.create_async.side_effect = new_create
        await create_outstanding_checkout(self.db, self.rental_id, 1)
        self.assertEqual(self.client_stripe.v1.checkout.sessions.create_async.call_count, 2)

    async def test_new_failed_day_is_not_added_to_an_existing_checkout(self):
        await create_outstanding_checkout(self.db, self.rental_id, 1)
        await self.connection.execute(text("""
                                           INSERT INTO overdue_charges(rental_id, charge_date, status)
                                           VALUES (:id, CURRENT_DATE + 1, 'failed')
                                           """), {'id': self.rental_id})
        page = (await self.client.get(f'/rentals/{self.rental_id}')).text
        self.assertIn('Continue payment €1.00', page)
        self.pay()
        await apply_recovery(self.db, self.checkout)
        self.assertEqual(await self.db.scalar(text("SELECT count(*) FROM overdue_charges WHERE status='failed'")), 1)
        self.assertEqual((await booking_eligibility(self.db, 1))['unpaid_cents'], 100)
        self.assertFalse((await booking_eligibility(self.db, 1))['can_book'])

    async def test_unknown_checkout_creation_reuses_the_durable_request(self):
        import stripe
        saved = []

        def lost_response(params, options):
            saved.append(options['idempotency_key'])
            self.create(params, options=options)
            raise stripe.APIConnectionError('Lost response')

        self.client_stripe.v1.checkout.sessions.create_async.side_effect = lost_response
        with self.assertRaises(stripe.APIConnectionError):
            await create_outstanding_checkout(self.db, self.rental_id, 1)
        self.client_stripe.v1.checkout.sessions.create_async.side_effect = self.create
        await create_outstanding_checkout(self.db, self.rental_id, 1)
        self.assertEqual(
            self.client_stripe.v1.checkout.sessions.create_async.call_args.kwargs['options']['idempotency_key'],
            saved[0])
        self.assertEqual(await self.db.scalar(text('SELECT count(*) FROM overdue_payments')), 1)

    async def test_delayed_retry_repairs_expiry_without_changing_the_debt_or_key(self):
        import stripe

        self.client_stripe.v1.checkout.sessions.create_async.side_effect = stripe.APIConnectionError('Not delivered')
        with self.assertRaises(stripe.APIConnectionError):
            await create_outstanding_checkout(self.db, self.rental_id, 1)
        saved = (await self.db.execute(text('SELECT * FROM overdue_payments'))).mappings().one()
        now = datetime.fromtimestamp(saved['checkout_params']['expires_at'], timezone.utc) - timedelta(minutes=29)

        def delayed_create(params, **kwargs):
            if params['expires_at'] - now.timestamp() < 1800:
                raise stripe.InvalidRequestError('Expiry is too soon', 'expires_at', http_status=400)
            return self.create(params, **kwargs)

        self.client_stripe.v1.checkout.sessions.create_async.side_effect = delayed_create
        with patch('services.checkout.checkout_now', return_value=now):
            await create_outstanding_checkout(self.db, self.rental_id, 1)
        calls = self.client_stripe.v1.checkout.sessions.create_async.await_args_list
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[1].kwargs, calls[2].kwargs)
        self.assertGreater(calls[2].args[0]['expires_at'], calls[1].args[0]['expires_at'])
        self.assertEqual(await self.db.scalar(text('SELECT count(*) FROM overdue_payments')), 1)
        self.assertEqual(await self.db.scalar(text('SELECT charge_ids FROM overdue_payments')), saved['charge_ids'])
        self.client_stripe.v1.payment_intents.cancel_async.assert_awaited_once()
        self.pay()
        await apply_recovery(self.db, self.checkout)
        self.assertTrue((await booking_eligibility(self.db, 1))['can_book'])

    async def test_settlement_leaves_another_rentals_balance_restricted(self):
        other = await self.connection.scalar(text("""
                                                  INSERT INTO rentals(user_id, locker_id, status, prepaid_days,
                                                                      reserved_until, starts_at,
                                                                      paid_until, total_price_cents,
                                                                      overdue_terms_version,
                                                                      overdue_terms_accepted_at, created_at,
                                                                      completed_at)
                                                  SELECT user_id,
                                                         locker_id,
                                                         'completed',
                                                         prepaid_days,
                                                         reserved_until,
                                                         starts_at,
                                                         paid_until,
                                                         total_price_cents,
                                                         overdue_terms_version,
                                                         overdue_terms_accepted_at,
                                                         created_at,
                                                         clock_timestamp()
                                                  FROM rentals
                                                  WHERE id = :id
                                                  RETURNING id
                                                  """), {'id': self.rental_id})
        await self.connection.execute(text("""
                                           INSERT INTO overdue_charges(rental_id, charge_date, status)
                                           VALUES (:id, CURRENT_DATE, 'failed')
                                           """), {'id': other})
        await create_outstanding_checkout(self.db, self.rental_id, 1)
        self.pay()
        await apply_recovery(self.db, self.checkout)
        eligibility = await booking_eligibility(self.db, 1)
        self.assertFalse(eligibility['can_book'])
        self.assertEqual(eligibility['unpaid_cents'], 100)
        self.assertEqual([r['id'] for r in eligibility['rentals']], [other])

    async def test_recovery_webhook_routes_to_outstanding_payment(self):
        from services.payment_confirmation import process_event
        await create_outstanding_checkout(self.db, self.rental_id, 1)
        self.pay()
        event = {'id': 'evt_recovery', 'type': 'checkout.session.completed',
                 'data': {'object': {'id': self.checkout['id']}}}
        with patch('services.payment_confirmation.retrieve_checkout', return_value=self.checkout):
            await process_event(self.db, event)
            await process_event(self.db, event)
        self.assertEqual(await self.db.scalar(text('SELECT status FROM overdue_charges')), 'paid')
