import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

import test_commands
from services.overdue_payments import apply_recovery, create_outstanding_checkout, reconcile_outstanding


@unittest.skipUnless(os.getenv('UNISTOW_TEST_POSTGRES') == '1', 'Requires isolated PostgreSQL schema')
class OutstandingPaymentTests(unittest.TestCase):
    tearDown = test_commands.CommandTests.tearDown
    report = test_commands.CommandTests.report

    def setUp(self):
        test_commands.CommandTests.setUp(self)
        self.connection.execute(text("""
            INSERT INTO overdue_charges(rental_id,charge_date,status,stripe_payment_intent_id,request_params)
            VALUES(:id,CURRENT_DATE,'failed','pi_declined',
                '{"customer":"cus_test","payment_method":"pm_test","metadata":{}}')
        """), {'id': self.rental_id})
        self.db = Session(bind=self.connection, join_transaction_mode='create_savepoint')
        self.addCleanup(self.db.close)
        self.client_stripe = Mock()
        self.intent = {'id': 'pi_declined', 'livemode': False, 'amount': 100, 'currency': 'eur',
                       'customer': 'cus_test', 'payment_method': None, 'metadata': {},
                       'status': 'requires_payment_method',
                       'last_payment_error': {'payment_method': {'id': 'pm_test'}}}
        self.client_stripe.v1.payment_intents.retrieve.side_effect = lambda _: self.wrap(self.intent)
        self.client_stripe.v1.payment_intents.cancel.side_effect = self.cancel
        self.client_stripe.v1.checkout.sessions.create.side_effect = self.create
        self.client_stripe.v1.checkout.sessions.retrieve.side_effect = lambda _: self.wrap(self.checkout)
        mock = patch('services.overdue_payments.stripe_client', return_value=self.client_stripe)
        mock.start()
        self.addCleanup(mock.stop)

    @staticmethod
    def wrap(value):
        return SimpleNamespace(to_dict=lambda: dict(value))

    def cancel(self, *args, **kwargs):
        self.intent['status'] = 'canceled'
        return self.wrap(self.intent)

    def create(self, params, options):
        self.checkout = {'id': 'cs_recovery', 'livemode': False, 'mode': 'payment', 'currency': 'eur',
                         'amount_total': params['line_items'][0]['price_data']['unit_amount'],
                         'metadata': params['metadata'], 'client_reference_id': params['client_reference_id'],
                         'status': 'open', 'payment_status': 'unpaid', 'url': 'https://checkout.stripe.com/test'}
        return self.wrap(self.checkout)

    def pay(self):
        self.checkout.update(status='complete', payment_status='paid', payment_intent='pi_recovery')
        self.intent = {'id': 'pi_recovery', 'livemode': False, 'status': 'succeeded',
                       'currency': 'eur', 'amount_received': 100, 'metadata': self.checkout['metadata']}

    def test_repeated_clicks_reuse_checkout_and_paid_events_are_idempotent(self):
        first = create_outstanding_checkout(self.db, self.rental_id, 1)
        self.assertEqual(create_outstanding_checkout(self.db, self.rental_id, 1), first)
        self.client_stripe.v1.checkout.sessions.create.assert_called_once()
        self.client_stripe.v1.payment_intents.cancel.assert_called_once()
        self.pay()
        apply_recovery(self.db, self.checkout)
        apply_recovery(self.db, self.checkout)
        self.assertEqual(self.db.scalar(text('SELECT status FROM overdue_charges')), 'paid')
        self.assertEqual(self.db.scalar(text('SELECT status FROM overdue_payments')), 'paid')
        self.assertEqual(self.db.scalar(text('SELECT stripe_payment_intent_id FROM overdue_charges')), 'pi_declined')
        with self.assertRaises(HTTPException):
            create_outstanding_checkout(self.db, self.rental_id, 1)

    def test_completed_rental_keeps_debt_visible_and_can_be_paid(self):
        self.connection.execute(text("UPDATE rentals SET status='completed',completed_at=clock_timestamp()"))
        page = self.client.get(f'/rentals/{self.rental_id}')
        self.assertIn('Pay outstanding €1.00', page.text)
        self.assertNotIn('id="finish-rental-button"', page.text)
        self.assertIn(f'/rentals/{self.rental_id}', self.client.get('/rentals').text)
        create_outstanding_checkout(self.db, self.rental_id, 1)
        self.pay()
        reconcile_outstanding(self.db, self.rental_id, 1)
        self.assertEqual(self.db.scalar(text('SELECT status FROM rentals')), 'completed')
        page = self.client.get(f'/rentals/{self.rental_id}')
        self.assertNotIn('Pay outstanding', page.text)

    def test_other_user_and_missing_browser_header_are_rejected(self):
        url = f'/api/rentals/{self.rental_id}/outstanding/checkout'
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.cookies.set('unistow_session', 'session-2')
        self.assertEqual(self.client.post(url, headers={'X-UniStow-Request': '1'}).status_code, 404)
        self.client_stripe.v1.checkout.sessions.create.assert_not_called()

    def test_pending_charges_are_not_recovered(self):
        self.connection.execute(text("UPDATE overdue_charges SET status='pending'"))
        with self.assertRaises(HTTPException):
            create_outstanding_checkout(self.db, self.rental_id, 1)
        self.client_stripe.v1.checkout.sessions.create.assert_not_called()

    def test_mismatched_amount_or_live_session_cannot_clear_debt(self):
        create_outstanding_checkout(self.db, self.rental_id, 1)
        self.pay()
        for change in ({'amount_total': 200}, {'livemode': True}, {'id': 'cs_wrong'}):
            with self.assertRaises(HTTPException):
                apply_recovery(self.db, self.checkout | change)
            self.db.rollback()
        self.assertEqual(self.db.scalar(text('SELECT status FROM overdue_charges')), 'failed')

    def test_processing_original_payment_cannot_be_replaced(self):
        self.intent.update(status='processing', payment_method='pm_test')
        with self.assertRaises(HTTPException):
            create_outstanding_checkout(self.db, self.rental_id, 1)
        self.client_stripe.v1.checkout.sessions.create.assert_not_called()

    def test_browser_return_does_not_clear_debt(self):
        create_outstanding_checkout(self.db, self.rental_id, 1)
        self.assertEqual(self.client.get(f'/rentals/{self.rental_id}?outstanding_returned=1').status_code, 200)
        self.assertEqual(self.db.scalar(text('SELECT status FROM overdue_charges')), 'failed')

    def test_unpaid_expiry_preserves_debt_and_allows_another_checkout(self):
        create_outstanding_checkout(self.db, self.rental_id, 1)
        self.checkout['status'] = 'expired'
        apply_recovery(self.db, self.checkout)
        self.assertEqual(self.db.scalar(text('SELECT status FROM overdue_payments')), 'expired')
        self.assertEqual(self.db.scalar(text('SELECT status FROM overdue_charges')), 'failed')
        old_create = self.create

        def new_create(params, options):
            old_create(params, options)
            self.checkout['id'] = 'cs_new'
            return self.wrap(self.checkout)

        self.client_stripe.v1.checkout.sessions.create.side_effect = new_create
        create_outstanding_checkout(self.db, self.rental_id, 1)
        self.assertEqual(self.client_stripe.v1.checkout.sessions.create.call_count, 2)

    def test_new_failed_day_is_not_added_to_an_existing_checkout(self):
        create_outstanding_checkout(self.db, self.rental_id, 1)
        self.connection.execute(text("""
            INSERT INTO overdue_charges(rental_id,charge_date,status)
            VALUES(:id,CURRENT_DATE+1,'failed')
        """), {'id': self.rental_id})
        page = self.client.get(f'/rentals/{self.rental_id}').text
        self.assertIn('Pay outstanding €1.00', page)
        self.pay()
        apply_recovery(self.db, self.checkout)
        self.assertEqual(self.db.scalar(text("SELECT count(*) FROM overdue_charges WHERE status='failed'")), 1)

    def test_unknown_checkout_creation_reuses_the_durable_request(self):
        import stripe
        saved = []

        def lost_response(params, options):
            saved.append(options['idempotency_key'])
            self.create(params, options)
            raise stripe.APIConnectionError('Lost response')

        self.client_stripe.v1.checkout.sessions.create.side_effect = lost_response
        with self.assertRaises(stripe.APIConnectionError):
            create_outstanding_checkout(self.db, self.rental_id, 1)
        self.client_stripe.v1.checkout.sessions.create.side_effect = self.create
        create_outstanding_checkout(self.db, self.rental_id, 1)
        self.assertEqual(self.client_stripe.v1.checkout.sessions.create.call_args.kwargs['options']['idempotency_key'], saved[0])
        self.assertEqual(self.db.scalar(text('SELECT count(*) FROM overdue_payments')), 1)

    def test_recovery_webhook_routes_to_outstanding_payment(self):
        from services.payment_confirmation import process_event
        create_outstanding_checkout(self.db, self.rental_id, 1)
        self.pay()
        event = {'id': 'evt_recovery', 'type': 'checkout.session.completed',
                 'data': {'object': {'id': self.checkout['id']}}}
        with patch('services.payment_confirmation.retrieve_checkout', return_value=self.checkout):
            process_event(self.db, event)
            process_event(self.db, event)
        self.assertEqual(self.db.scalar(text('SELECT status FROM overdue_charges')), 'paid')
