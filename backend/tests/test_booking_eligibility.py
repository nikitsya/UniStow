import asyncio
import os
import time
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import stripe
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import test_commands
import test_overdue_billing
from services.booking_eligibility import booking_eligibility, lock_account
from services.commands import authorised_locker
from services.overdue_billing import save_status
from services.payment_confirmation import apply_checkout
from services.payments import create_checkout
from services.rentals import reserve_locker
from stripe_fakes import stripe_client as fake_stripe_client


@unittest.skipUnless(os.getenv('UNISTOW_TEST_POSTGRES') == '1', 'Requires isolated PostgreSQL schema')
class BookingEligibilityTests(test_commands.CommandTestCase):
    headers = {'X-UniStow-Request': '1'}

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.db = AsyncSession(bind=self.connection, join_transaction_mode='create_savepoint')
        self.addAsyncCleanup(self.db.close)

    async def charge(self, status='failed', rental_id=None, day=0):
        return await self.connection.scalar(text("""
                                                 INSERT INTO overdue_charges(rental_id, charge_date, status, stripe_payment_intent_id, paid_at)
                                                 VALUES (:rental, CURRENT_DATE + :day, :status, :intent,
                                                         CASE WHEN :status = 'paid' THEN clock_timestamp() END)
                                                 RETURNING id
                                                 """),
                                            {'rental': rental_id or self.rental_id, 'day': day, 'status': status,
                                             'intent': f'pi_test_{rental_id}_{day}'})

    async def completed_rental(self):
        return await self.connection.scalar(text("""
                                                 INSERT INTO rentals(user_id, locker_id, status, prepaid_days,
                                                                     reserved_until,
                                                                     starts_at, paid_until, total_price_cents,
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

    async def reserve(self):
        return await self.client.post('/api/rentals', headers=self.headers,
                                      json={'locker_id': 2, 'days': 2, 'overdue_consent': True,
                                            'payment_method_consent': True})

    async def test_one_failed_euro_blocks_direct_booking_and_prepared_reservation_checkout(self):
        pending = await reserve_locker(self.db, 1, 2, 2)
        await self.charge()
        response = await self.reserve()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail']['code'], 'account_booking_restricted')
        self.assertEqual(response.json()['detail']['unpaid_cents'], 100)
        with patch('services.payments.stripe_client', return_value=fake_stripe_client()) as stripe_mock:
            response = await self.client.post(f"/api/rentals/{pending['id']}/checkout", headers=self.headers)
            self.assertEqual(response.status_code, 409)
            stripe_mock.return_value.v1.checkout.sessions.create_async.assert_not_called()
        self.assertEqual(await self.connection.scalar(text('SELECT count(*) FROM rentals')), 2)
        self.assertEqual(await self.connection.scalar(text('SELECT count(*) FROM payments')), 1)
        page = await self.client.get(f"/rentals/{pending['id']}")
        self.assertRegex(page.text, r'<button(?=[^>]*id="pay-button")(?=[^>]*\bdisabled\b)[^>]*>')
        self.assertEqual(
            (await self.client.post(f"/api/rentals/{pending['id']}/cancel", headers=self.headers)).status_code,
            200)

    async def test_pending_and_review_payments_pause_booking_without_claiming_debt(self):
        await self.charge('pending')
        await self.charge('review', day=1)
        response = await self.reserve()
        self.assertEqual(response.status_code, 409)
        detail = response.json()['detail']
        self.assertEqual((detail['unpaid_cents'], detail['processing_cents']), (0, 200))
        self.assertIn('awaiting confirmation', detail['message'])
        page = (await self.client.get('/rentals')).text
        self.assertIn('Payment awaiting confirmation', page)
        self.assertNotIn('Balance to settle', page)
        self.assertIn('account-balance--pending', page)
        self.assertNotIn('account-balance--unpaid', page)

    async def test_completed_debt_disables_new_lockers_and_precedes_rental_history(self):
        await self.connection.execute(
            text("UPDATE rentals SET status='completed',completed_at=clock_timestamp(),locker_id=2 WHERE id=:id"),
            {'id': self.rental_id})
        await self.charge()
        home = (await self.client.get('/')).text
        self.assertNotIn('Continue rental', home)
        self.assertNotIn('home-locker is-available', home)
        self.assertNotIn('/lockers/1/rent', home)
        self.assertEqual(home.count('data-status="unavailable"'), 2)
        self.assertRegex(home, r'data-filter="available"[^>]*>Available\s*<span>0</span>')
        page = (await self.client.get('/rentals')).text
        self.assertIn('account-balance--unpaid', page)
        self.assertIn(f'Rental #{self.rental_id} · Locker 5', page)
        self.assertLess(page.index('id="account-balance"'), page.index('id="completed-rentals-heading"'))
        self.assertNotIn('Choose an available locker to get started.', page)
        await self.connection.execute(text("UPDATE overdue_charges SET status='waived' WHERE rental_id=:id"),
                                      {'id': self.rental_id})
        home = (await self.client.get('/')).text
        self.assertIn('home-locker is-available', home)
        self.assertIn('/lockers/1/rent', home)
        self.assertNotIn('account-balance--unpaid', home)

    async def test_paid_waived_and_initial_unpaid_reservations_are_not_debt(self):
        await self.charge('paid')
        await self.charge('waived', day=1)
        self.assertEqual((await self.reserve()).status_code, 201)
        self.assertTrue((await booking_eligibility(self.db, 1))['can_book'])
        self.assertEqual((await self.reserve()).status_code, 201)

    async def test_cancelled_reservation_and_other_accounts_do_not_restrict_booking(self):
        await self.charge()
        await self.connection.execute(text("UPDATE rentals SET status='cancelled' WHERE id=:id"),
                                      {'id': self.rental_id})
        self.assertTrue((await booking_eligibility(self.db, 1))['can_book'])
        await self.connection.execute(text("UPDATE rentals SET status='active' WHERE id=:id"), {'id': self.rental_id})
        self.client.cookies.set('unistow_session', 'session-2')
        self.assertEqual((await self.reserve()).status_code, 201)
        self.assertNotIn('Balance to settle', (await self.client.get('/rentals')).text)

    async def test_all_balances_remain_visible_beyond_five_completed_rentals(self):
        oldest = await self.completed_rental()
        await self.charge(rental_id=oldest)
        latest = await self.completed_rental()
        for _ in range(5):
            latest = await self.completed_rental()
        await self.charge(rental_id=latest)
        self.assertEqual((await self.reserve()).status_code, 409)
        for path in ('/', '/rentals', '/lockers/2/rent'):
            page = await self.client.get(path)
            self.assertEqual(page.status_code, 200)
            self.assertIn('€2.00', page.text)
            for rental in (oldest, latest):
                self.assertIn(f'Rental #{rental}', page.text)
                self.assertIn(f'/rentals/{rental}', page.text)
            self.assertIn('/contact', page.text)
        self.assertNotIn('id="rental-form"', (await self.client.get('/lockers/2/rent')).text)
        self.assertNotIn('/lockers/2/rent', (await self.client.get('/')).text)
        self.assertIn('Pay outstanding €1.00', (await self.client.get(f'/rentals/{oldest}')).text)

    async def test_debt_does_not_revoke_paid_access_collection_or_support(self):
        other_rental = await self.completed_rental()
        await self.charge(rental_id=other_rental)
        await authorised_locker(self.db, self.rental_id, 1)
        await self.db.commit()
        self.assertEqual((await self.client.get('/contact')).status_code, 200)
        self.assertEqual((await self.client.get('/api/me')).status_code, 200)
        self.assertIn('Continue rental for Locker 4', (await self.client.get('/')).text)
        self.assertEqual((await self.finish()).status_code, 202)

    async def test_checkout_authorised_before_debt_appears_is_honoured(self):
        await self.connection.execute(
            text("UPDATE rentals SET status='completed',completed_at=clock_timestamp() WHERE id=:id"),
            {'id': self.rental_id})
        pending = await reserve_locker(self.db, 1, 1, 2)
        client = fake_stripe_client()
        checkout = {'id': 'cs_authorised', 'livemode': False, 'status': 'open',
                    'url': 'https://checkout.stripe.com/test', 'mode': 'payment',
                    'payment_status': 'unpaid', 'currency': 'eur', 'amount_total': 200,
                    'client_reference_id': str(pending['id']), 'metadata': {'rental_id': str(pending['id'])}}

        async def issue_checkout(*_args, **_kwargs):
            # The account lock and durable checkout decision precede the Stripe call.
            await self.charge()
            return SimpleNamespace(**checkout)

        client.v1.checkout.sessions.create_async.side_effect = issue_checkout
        with patch('services.payments.stripe_client', return_value=client):
            self.assertEqual((await create_checkout(self.db, pending['id'], 1))['url'], checkout['url'])
            with self.assertRaises(HTTPException) as blocked:
                await create_checkout(self.db, pending['id'], 1)
            self.assertEqual(blocked.exception.status_code, 409)
            await self.db.rollback()
        checkout.update(status='complete', payment_status='paid', payment_intent='pi_authorised')
        await apply_checkout(self.db, checkout)
        await apply_checkout(self.db, checkout)
        await authorised_locker(self.db, pending['id'], 1)
        self.assertFalse((await booking_eligibility(self.db, 1))['can_book'])
        self.assertEqual(await self.db.scalar(text('SELECT status FROM rentals WHERE id=:id'),
                                              {'id': pending['id']}), 'active')


@unittest.skipUnless(os.getenv('UNISTOW_TEST_POSTGRES') == '1', 'Requires independent PostgreSQL transactions')
class BookingConcurrencyTests(test_overdue_billing.OverdueBillingTestCase):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.db.begin() as connection:
            await connection.execute(text("""
                                          INSERT INTO lockers(number, size, price_per_day_cents, is_enabled, last_seen_at)
                                          VALUES (5, 'small', 100, true, clock_timestamp())
                                          """))
            await connection.execute(text("""
                                          INSERT INTO overdue_charges(rental_id, charge_date, status, stripe_payment_intent_id)
                                          VALUES (1, CURRENT_DATE, 'pending', 'pi_unconfirmed')
                                          """))

    async def wait_for_lock(self, pid):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            async with self.db.connect() as connection:
                if await connection.scalar(text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),
                                           {'pid': pid}) == 'Lock':
                    return
            await asyncio.sleep(0.01)
        self.fail('The concurrent booking did not wait for the account lock.')

    async def assert_waits_for_balance(self, operation):
        for status in ('failed', 'paid'):
            with self.subTest(status=status):
                async with AsyncSession(self.db) as settlement:
                    await lock_account(settlement, 1)
                    ready = asyncio.Event()
                    pid = []

                    async def book():
                        async with AsyncSession(self.db) as booking:
                            pid.append(await booking.scalar(text('SELECT pg_backend_pid()')))
                            ready.set()
                            try:
                                await operation(booking)
                                return 'allowed'
                            except HTTPException as error:
                                return error.status_code

                    task = asyncio.create_task(book())
                    try:
                        await asyncio.wait_for(ready.wait(), timeout=3)
                        await self.wait_for_lock(pid[0])
                        self.assertFalse(task.done())
                        await settlement.execute(text("""
                                                      UPDATE overdue_charges
                                                      SET status='pending',
                                                          paid_at=NULL
                                                      WHERE id = 1
                                                      """))
                        await save_status(settlement, 1, status)
                    finally:
                        await settlement.rollback()
                    self.assertEqual(await asyncio.wait_for(task, timeout=3),
                                     409 if status == 'failed' else 'allowed')

    async def test_reservation_waits_for_settlement_or_new_debt_before_deciding(self):
        await self.assert_waits_for_balance(lambda db: reserve_locker(db, 1, 2, 2))

    async def test_checkout_waits_for_settlement_or_new_debt_before_deciding(self):
        async with self.db.begin() as connection:
            await connection.execute(text("UPDATE overdue_charges SET status='waived' WHERE id=1"))
        async with AsyncSession(self.db) as db:
            rental = await reserve_locker(db, 1, 2, 2)
        async with self.db.begin() as connection:
            await connection.execute(text("UPDATE overdue_charges SET status='pending' WHERE id=1"))
        client = fake_stripe_client()
        client.v1.checkout.sessions.create_async.return_value = SimpleNamespace(
            id='cs_concurrent', livemode=False, status='open', url='https://checkout.stripe.com/test')
        with patch('services.payments.stripe_client', return_value=client):
            await self.assert_waits_for_balance(lambda booking_db: create_checkout(booking_db, rental['id'], 1))
        client.v1.checkout.sessions.create_async.assert_called_once()

    async def test_concurrent_retries_share_the_repaired_checkout(self):
        async with self.db.begin() as connection:
            await connection.execute(text("UPDATE overdue_charges SET status='waived' WHERE id=1"))
        async with AsyncSession(self.db) as setup_db:
            rental = await reserve_locker(setup_db, 1, 2, 2)
            now = await setup_db.scalar(text('SELECT clock_timestamp()'))
        client = fake_stripe_client()
        client.v1.checkout.sessions.create_async.side_effect = stripe.APIConnectionError('Not delivered')
        with patch('services.payments.stripe_client', return_value=client):
            async with AsyncSession(self.db) as initial_db:
                with self.assertRaises(HTTPException):
                    await create_checkout(initial_db, rental['id'], 1)
            now += timedelta(minutes=2)
            creating = asyncio.Event()
            release = asyncio.Event()
            second_started = asyncio.Event()
            second_pid = []
            session = SimpleNamespace(id='cs_one_session', status='open', livemode=False,
                                      url='https://checkout.stripe.com/test')

            async def create(params, **_kwargs):
                if params['expires_at'] - now.timestamp() < 1800:
                    raise stripe.InvalidRequestError('Expiry is too soon', 'expires_at', http_status=400)
                creating.set()
                await asyncio.wait_for(release.wait(), timeout=5)
                return session

            async def pay(is_second=False):
                async with AsyncSession(self.db) as request_db:
                    if is_second:
                        second_pid.append(await request_db.scalar(text('SELECT pg_backend_pid()')))
                        second_started.set()
                    return await create_checkout(request_db, rental['id'], 1)

            client.v1.checkout.sessions.create_async.side_effect = create
            client.v1.checkout.sessions.retrieve_async.return_value = session
            with patch('services.checkout.checkout_now', return_value=now):
                first_task = asyncio.create_task(pay())
                second_task = None
                try:
                    await asyncio.wait_for(creating.wait(), timeout=3)
                    second_task = asyncio.create_task(pay(is_second=True))
                    await asyncio.wait_for(second_started.wait(), timeout=3)
                    await self.wait_for_lock(second_pid[0])
                    self.assertFalse(second_task.done())
                finally:
                    release.set()
                    results = await asyncio.wait_for(
                        asyncio.gather(*[task for task in (first_task, second_task) if task]), timeout=5)
            self.assertEqual(results, [{'url': session.url}, {'url': session.url}])
        calls = client.v1.checkout.sessions.create_async.await_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(len({call.kwargs['options']['idempotency_key'] for call in calls}), 1)
        client.v1.checkout.sessions.retrieve_async.assert_awaited_once_with(session.id)
