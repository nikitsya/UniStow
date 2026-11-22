import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

import test_commands
import test_overdue_billing
from services.booking_eligibility import booking_eligibility, lock_account
from services.commands import authorised_locker
from services.overdue_billing import save_status
from services.payment_confirmation import apply_checkout
from services.payments import create_checkout
from services.rentals import reserve_locker


@unittest.skipUnless(os.getenv('UNISTOW_TEST_POSTGRES') == '1', 'Requires isolated PostgreSQL schema')
class BookingEligibilityTests(unittest.TestCase):
    tearDown = test_commands.CommandTests.tearDown
    report = test_commands.CommandTests.report
    finish = test_commands.CommandTests.finish
    headers = {'X-UniStow-Request': '1'}

    def setUp(self):
        test_commands.CommandTests.setUp(self)
        self.db = Session(bind=self.connection, join_transaction_mode='create_savepoint')
        self.addCleanup(self.db.close)

    def charge(self, status='failed', rental_id=None, day=0):
        return self.connection.scalar(text("""
                                           INSERT INTO overdue_charges(rental_id, charge_date, status, stripe_payment_intent_id, paid_at)
                                           VALUES (:rental, CURRENT_DATE + :day, :status, :intent,
                                                   CASE WHEN :status = 'paid' THEN clock_timestamp() END)
                                           RETURNING id
                                           """), {'rental': rental_id or self.rental_id, 'day': day, 'status': status,
                                                  'intent': f'pi_test_{rental_id}_{day}'})

    def completed_rental(self):
        return self.connection.scalar(text("""
                                           INSERT INTO rentals(user_id, locker_id, status, prepaid_days, reserved_until,
                                                               starts_at, paid_until, total_price_cents,
                                                               overdue_terms_version,
                                                               overdue_terms_accepted_at, created_at, completed_at)
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

    def reserve(self):
        return self.client.post('/api/rentals', headers=self.headers,
                                json={'locker_id': 2, 'days': 2, 'overdue_consent': True,
                                      'payment_method_consent': True})

    def test_one_failed_euro_blocks_direct_booking_and_prepared_reservation_checkout(self):
        pending = reserve_locker(self.db, 1, 2, 2)
        self.charge()
        response = self.reserve()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail']['code'], 'account_booking_restricted')
        self.assertEqual(response.json()['detail']['unpaid_cents'], 100)
        with patch('services.payments.stripe_client') as stripe:
            response = self.client.post(f"/api/rentals/{pending['id']}/checkout", headers=self.headers)
            self.assertEqual(response.status_code, 409)
            stripe.return_value.v1.checkout.sessions.create.assert_not_called()
        self.assertEqual(self.connection.scalar(text('SELECT count(*) FROM rentals')), 2)
        self.assertEqual(self.connection.scalar(text('SELECT count(*) FROM payments')), 1)
        page = self.client.get(f"/rentals/{pending['id']}")
        self.assertRegex(page.text, r'<button(?=[^>]*id="pay-button")(?=[^>]*\bdisabled\b)[^>]*>')
        self.assertEqual(self.client.post(f"/api/rentals/{pending['id']}/cancel", headers=self.headers).status_code,
                         200)

    def test_pending_and_review_payments_pause_booking_without_claiming_debt(self):
        self.charge('pending')
        self.charge('review', day=1)
        response = self.reserve()
        self.assertEqual(response.status_code, 409)
        detail = response.json()['detail']
        self.assertEqual((detail['unpaid_cents'], detail['processing_cents']), (0, 200))
        self.assertIn('awaiting confirmation', detail['message'])
        page = self.client.get('/rentals').text
        self.assertIn('Payment awaiting confirmation', page)
        self.assertNotIn('Balance to settle', page)
        self.assertIn('account-balance--pending', page)
        self.assertNotIn('account-balance--unpaid', page)

    def test_completed_debt_disables_new_lockers_and_precedes_rental_history(self):
        self.connection.execute(
            text("UPDATE rentals SET status='completed',completed_at=clock_timestamp(),locker_id=2"))
        self.charge()
        home = self.client.get('/').text
        self.assertNotIn('Continue rental', home)
        self.assertNotIn('home-locker is-available', home)
        self.assertNotIn('/lockers/1/rent', home)
        self.assertEqual(home.count('data-status="unavailable"'), 2)
        self.assertRegex(home, r'data-filter="available"[^>]*>Available\s*<span>0</span>')
        page = self.client.get('/rentals').text
        self.assertIn('account-balance--unpaid', page)
        self.assertIn(f'Rental #{self.rental_id} · Locker 5', page)
        self.assertLess(page.index('id="account-balance"'), page.index('id="completed-rentals-heading"'))
        self.assertNotIn('Choose an available locker to get started.', page)
        self.connection.execute(text("UPDATE overdue_charges SET status='waived'"))
        home = self.client.get('/').text
        self.assertIn('home-locker is-available', home)
        self.assertIn('/lockers/1/rent', home)
        self.assertNotIn('account-balance--unpaid', home)

    def test_paid_waived_and_initial_unpaid_reservations_are_not_debt(self):
        self.charge('paid')
        self.charge('waived', day=1)
        self.assertEqual(self.reserve().status_code, 201)
        self.assertTrue(booking_eligibility(self.db, 1)['can_book'])
        self.assertEqual(self.reserve().status_code, 201)

    def test_cancelled_reservation_and_other_accounts_do_not_restrict_booking(self):
        self.charge()
        self.connection.execute(text("UPDATE rentals SET status='cancelled' WHERE id=:id"), {'id': self.rental_id})
        self.assertTrue(booking_eligibility(self.db, 1)['can_book'])
        self.connection.execute(text("UPDATE rentals SET status='active' WHERE id=:id"), {'id': self.rental_id})
        self.client.cookies.set('unistow_session', 'session-2')
        self.assertEqual(self.reserve().status_code, 201)
        self.assertNotIn('Balance to settle', self.client.get('/rentals').text)

    def test_all_balances_remain_visible_beyond_five_completed_rentals(self):
        oldest = self.completed_rental()
        self.charge(rental_id=oldest)
        for _ in range(6):
            latest = self.completed_rental()
        self.charge(rental_id=latest)
        self.assertEqual(self.reserve().status_code, 409)
        for path in ('/', '/rentals', '/lockers/2/rent'):
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200)
            self.assertIn('€2.00', page.text)
            for rental in (oldest, latest):
                self.assertIn(f'Rental #{rental}', page.text)
                self.assertIn(f'/rentals/{rental}', page.text)
            self.assertIn('/contact', page.text)
        self.assertNotIn('id="rental-form"', self.client.get('/lockers/2/rent').text)
        self.assertNotIn('/lockers/2/rent', self.client.get('/').text)
        self.assertIn('Pay outstanding €1.00', self.client.get(f'/rentals/{oldest}').text)

    def test_debt_does_not_revoke_paid_access_collection_or_support(self):
        other_rental = self.completed_rental()
        self.charge(rental_id=other_rental)
        authorised_locker(self.db, self.rental_id, 1)
        self.db.commit()
        self.assertEqual(self.client.get('/contact').status_code, 200)
        self.assertEqual(self.client.get('/api/me').status_code, 200)
        self.assertIn('Continue rental for Locker 4', self.client.get('/').text)
        self.assertEqual(self.finish().status_code, 202)

    def test_checkout_authorised_before_debt_appears_is_honoured(self):
        self.connection.execute(text("UPDATE rentals SET status='completed',completed_at=clock_timestamp()"))
        pending = reserve_locker(self.db, 1, 1, 2)
        client = Mock()
        checkout = {'id': 'cs_authorised', 'livemode': False, 'status': 'open',
                    'url': 'https://checkout.stripe.com/test', 'mode': 'payment',
                    'payment_status': 'unpaid', 'currency': 'eur', 'amount_total': 200,
                    'client_reference_id': str(pending['id']), 'metadata': {'rental_id': str(pending['id'])}}

        def issue_checkout(*args, **kwargs):
            # The account lock and durable checkout decision precede the Stripe call.
            self.charge()
            return SimpleNamespace(**checkout)

        client.v1.checkout.sessions.create.side_effect = issue_checkout
        with patch('services.payments.stripe_client', return_value=client):
            self.assertEqual(create_checkout(self.db, pending['id'], 1)['url'], checkout['url'])
            with self.assertRaises(HTTPException) as blocked:
                create_checkout(self.db, pending['id'], 1)
            self.assertEqual(blocked.exception.status_code, 409)
            self.db.rollback()
        checkout.update(status='complete', payment_status='paid', payment_intent='pi_authorised')
        apply_checkout(self.db, checkout)
        apply_checkout(self.db, checkout)
        authorised_locker(self.db, pending['id'], 1)
        self.assertFalse(booking_eligibility(self.db, 1)['can_book'])
        self.assertEqual(self.db.scalar(text('SELECT status FROM rentals WHERE id=:id'),
                                        {'id': pending['id']}), 'active')

    def test_migration_classifies_only_uncertain_failures_and_can_be_repeated(self):
        self.charge(day=0)
        review = self.charge(day=1)
        self.connection.execute(text("UPDATE overdue_charges SET failure_reason=:reason WHERE id=:id"),
                                {'id': review, 'reason': 'Payment verification needs operator review.'})
        source = (Path(__file__).resolve().parents[2] /
                  'database/migrations/005_booking_balance_review.sql').read_text()
        for _ in range(2):
            self.connection.exec_driver_sql(source.replace('BEGIN;', '').replace('COMMIT;', ''))
        self.assertEqual(self.connection.execute(text(
            'SELECT status FROM overdue_charges ORDER BY charge_date')).scalars().all(), ['failed', 'review'])


@unittest.skipUnless(os.getenv('UNISTOW_TEST_POSTGRES') == '1', 'Requires independent PostgreSQL transactions')
class BookingConcurrencyTests(unittest.TestCase):
    clean_database = test_overdue_billing.OverdueBillingTests.clean_database
    response = test_overdue_billing.OverdueBillingTests.response
    create_intent = test_overdue_billing.OverdueBillingTests.create_intent
    confirm_intent = test_overdue_billing.OverdueBillingTests.confirm_intent

    def setUp(self):
        test_overdue_billing.OverdueBillingTests.setUp(self)
        with self.db.begin() as connection:
            connection.execute(text("""
                                    INSERT INTO lockers(number, size, price_per_day_cents, is_enabled, last_seen_at)
                                    VALUES (5, 'small', 100, true, clock_timestamp())
                                    """))
            connection.execute(text("""
                                    INSERT INTO overdue_charges(rental_id, charge_date, status, stripe_payment_intent_id)
                                    VALUES (1, CURRENT_DATE, 'pending', 'pi_unconfirmed')
                                    """))

    def wait_for_lock(self, pid):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with self.db.connect() as connection:
                if connection.scalar(text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),
                                     {'pid': pid}) == 'Lock':
                    return
            time.sleep(0.01)
        self.fail('The concurrent booking did not wait for the account lock.')

    def assert_waits_for_balance(self, operation):
        for status in ('failed', 'paid'):
            with self.subTest(status=status), Session(self.db) as settlement:
                lock_account(settlement, 1)
                ready = Event()
                pid = []

                def book():
                    with Session(self.db) as booking:
                        pid.append(booking.scalar(text('SELECT pg_backend_pid()')))
                        ready.set()
                        try:
                            operation(booking)
                            return 'allowed'
                        except HTTPException as error:
                            return error.status_code

                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(book)
                    try:
                        self.assertTrue(ready.wait(3))
                        self.wait_for_lock(pid[0])
                        self.assertFalse(future.done())
                        settlement.execute(text("""
                                                UPDATE overdue_charges
                                                SET status='pending',
                                                    paid_at=NULL
                                                """))
                        save_status(settlement, 1, status)
                    finally:
                        settlement.rollback()
                    self.assertEqual(future.result(timeout=3), 409 if status == 'failed' else 'allowed')

    def test_reservation_waits_for_settlement_or_new_debt_before_deciding(self):
        self.assert_waits_for_balance(lambda db: reserve_locker(db, 1, 2, 2))

    def test_checkout_waits_for_settlement_or_new_debt_before_deciding(self):
        with self.db.begin() as connection:
            connection.execute(text("UPDATE overdue_charges SET status='waived'"))
        with Session(self.db) as db:
            rental = reserve_locker(db, 1, 2, 2)
        with self.db.begin() as connection:
            connection.execute(text("UPDATE overdue_charges SET status='pending'"))
        client = Mock()
        client.v1.checkout.sessions.create.return_value = SimpleNamespace(
            id='cs_concurrent', livemode=False, status='open', url='https://checkout.stripe.com/test')
        with patch('services.payments.stripe_client', return_value=client):
            self.assert_waits_for_balance(lambda db: create_checkout(db, rental['id'], 1))
        client.v1.checkout.sessions.create.assert_called_once()
