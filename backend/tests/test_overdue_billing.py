from pathlib import Path

import asyncio
import os
import secrets
import stripe
import unittest
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from types import SimpleNamespace
from unittest.mock import patch

from database import connect_args, engine
from services.overdue_billing import WORKER_LOCK, due_dates, run_billing_once
from services.rentals import IRELAND, rental_end
from stripe_fakes import stripe_client as fake_stripe_client


class BillingDateTests(unittest.IsolatedAsyncioTestCase):
    async def test_deadline_is_inclusive(self):
        deadline = datetime(2026, 11, 9, 18, tzinfo=IRELAND)
        self.assertEqual(list(due_dates(deadline, deadline - timedelta(seconds=1))), [])
        self.assertEqual(list(due_dates(deadline, deadline)), [deadline.date()])

    async def test_daily_deadlines_follow_dublin_clock_changes(self):
        for month, day in [(3, 28), (10, 24)]:
            first = datetime(2026, month, day, 18, tzinfo=IRELAND)
            before = datetime(2026, month, day + 1, 17, 59, tzinfo=IRELAND).astimezone(timezone.utc)
            at = datetime(2026, month, day + 1, 18, tzinfo=IRELAND).astimezone(timezone.utc)
            self.assertEqual(len(list(due_dates(first, before))), 1)
            self.assertEqual(len(list(due_dates(first, at))), 2)


class OverdueBillingTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.addAsyncCleanup(engine.dispose)
        self.schema = 'test_billing_' + secrets.token_hex(6)
        async with engine.begin() as connection:
            await connection.exec_driver_sql(f'CREATE SCHEMA {self.schema}')
        self.db = create_async_engine(engine.url,
                                      connect_args=connect_args | {'options': f'-c search_path={self.schema}'})
        self.addAsyncCleanup(self.clean_database)
        async with self.db.begin() as connection:
            source = (Path(__file__).resolve().parents[2] / 'database/schema.sql').read_text()
            await connection.exec_driver_sql(source.replace('BEGIN;', '').replace('COMMIT;', ''))
            start = datetime.now(IRELAND).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=1)
            await connection.execute(text("INSERT INTO users(email,password_hash) VALUES('test@example.com','test')"))
            await connection.execute(text("INSERT INTO lockers(number,size,price_per_day_cents) VALUES(4,'small',100)"))
            await connection.execute(text("""
                                          INSERT INTO rentals(user_id, locker_id, status, prepaid_days, reserved_until,
                                                              starts_at, paid_until,
                                                              total_price_cents, overdue_terms_version,
                                                              overdue_terms_accepted_at, created_at)
                                          VALUES (1, 1, 'active', 1, :reserved, :start, :end, 100, '2026-11-09', :start,
                                                  :start)
                                          """),
                                     {'start': start, 'reserved': start + timedelta(minutes=15),
                                      'end': rental_end(start, 1)})
            await connection.execute(text("""
                                          INSERT INTO payments(rental_id, request_key, checkout_params, status,
                                                               stripe_customer_id,
                                                               stripe_payment_method_id, payment_method_saved_at)
                                          VALUES (1, 'checkout', '{}', 'paid', 'cus_test', 'pm_test', clock_timestamp())
                                          """))
        self.intents = {}
        self.keys = {}
        self.collected = 0
        self.client = fake_stripe_client()
        self.client.v1.payment_intents.create_async.side_effect = self.create_intent
        self.client.v1.payment_intents.retrieve_async.side_effect = lambda key: self.response(self.intents[key])
        self.client.v1.payment_intents.confirm_async.side_effect = self.confirm_intent
        self.patch = patch('services.overdue_billing.stripe_client', return_value=self.client)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def clean_database(self):
        await self.db.dispose()
        async with engine.begin() as connection:
            await connection.exec_driver_sql(f'DROP SCHEMA {self.schema} CASCADE')

    @staticmethod
    def response(intent):
        return SimpleNamespace(to_dict=lambda: dict(intent))

    def create_intent(self, params, options):
        key = options['idempotency_key']
        if key not in self.keys:
            self.keys[key] = 'pi_' + str(len(self.keys) + 1)
            self.intents[self.keys[key]] = params | {
                'id': self.keys[key], 'livemode': False, 'status': 'requires_confirmation',
            }
        self.assertFalse(params['confirm'])
        return self.response(self.intents[self.keys[key]])

    def confirm_intent(self, key, params, **_options):
        self.assertTrue(params['off_session'])
        if self.intents[key]['status'] != 'succeeded':
            self.collected += 1
        self.intents[key]['status'] = 'succeeded'
        return self.response(self.intents[key])

    async def run_pass(self):
        await run_billing_once(self.db)

    async def rows(self):
        async with self.db.connect() as connection:
            return (
                await connection.execute(text('SELECT * FROM overdue_charges ORDER BY charge_date'))).mappings().all()

    async def expected_days(self):
        async with self.db.connect() as connection:
            deadline = await connection.scalar(text('SELECT paid_until FROM rentals WHERE id=1'))
        return len(list(due_dates(deadline, datetime.now(timezone.utc))))


@unittest.skipUnless(os.getenv('UNISTOW_TEST_POSTGRES') == '1', 'Requires isolated PostgreSQL schema')
class OverdueBillingTests(OverdueBillingTestCase):
    async def test_decline_with_cleared_payment_method_is_reconciled(self):
        def decline(key, _params, **_options):
            intent = self.intents[key]
            intent['status'] = 'requires_payment_method'
            intent['last_payment_error'] = {
                'code': 'card_declined',
                'payment_method': {'id': intent['payment_method']},
            }
            intent['payment_method'] = None
            raise stripe.CardError('Declined', 'payment_method', 'card_declined')

        self.client.v1.payment_intents.confirm_async.side_effect = decline
        await self.run_pass()
        self.assertTrue(all(row['status'] == 'pending' for row in await self.rows()))
        await self.run_pass()
        self.assertTrue(all(row['status'] == 'failed' for row in await self.rows()))
        calls = self.client.v1.payment_intents.confirm_async.call_count
        await self.run_pass()
        self.assertEqual(self.client.v1.payment_intents.confirm_async.call_count, calls)
        self.assertEqual(len(self.intents), await self.expected_days())
        self.assertEqual(self.collected, 0)

    async def test_daily_amount_and_repeat_runs(self):
        await self.run_pass()
        first = await self.rows()
        await self.run_pass()
        self.assertEqual(len(first), await self.expected_days())
        self.assertEqual(len(await self.rows()), len(first))
        self.assertEqual(self.collected, len(first))
        self.assertTrue(all(r['status'] == 'paid' and r['amount_cents'] == 100 and r['paid_at'] for r in first))

    async def test_lost_creation_response_reuses_request(self):
        def lost(params, options):
            self.create_intent(params, options)
            raise stripe.APIConnectionError('lost response')

        self.client.v1.payment_intents.create_async.side_effect = lost
        await self.run_pass()
        self.assertEqual(self.collected, 0)
        self.client.v1.payment_intents.create_async.side_effect = self.create_intent
        await self.run_pass()
        self.assertEqual(len(self.intents), await self.expected_days())
        self.assertEqual(self.collected, await self.expected_days())

    async def test_lost_confirmation_response_is_reconciled(self):
        def lost(key, params, options):
            self.confirm_intent(key, params, options=options)
            raise stripe.APIConnectionError('lost response')

        self.client.v1.payment_intents.confirm_async.side_effect = lost
        await self.run_pass()
        await self.run_pass()
        self.assertEqual(self.collected, await self.expected_days())
        self.assertTrue(all(r['status'] == 'paid' for r in await self.rows()))
        self.assertEqual(self.client.v1.payment_intents.confirm_async.call_count, await self.expected_days())

    async def test_unknown_creation_after_retry_window_needs_review(self):
        self.client.v1.payment_intents.create_async.side_effect = stripe.APIConnectionError('lost')
        await self.run_pass()
        async with self.db.begin() as connection:
            await connection.execute(
                text("UPDATE overdue_charges SET first_requested_at=clock_timestamp()-INTERVAL '25 hours' "
                     "WHERE rental_id = 1"))
        calls = self.client.v1.payment_intents.create_async.call_count
        await self.run_pass()
        self.assertEqual(self.client.v1.payment_intents.create_async.call_count, calls)
        self.assertTrue(all(r['status'] == 'review' for r in await self.rows()))

    async def test_completion_between_creation_and_confirmation_stops_charge(self):
        async def complete(params, options):
            result = self.create_intent(params, options)
            async with self.db.begin() as connection:
                await connection.execute(
                    text("UPDATE rentals SET status='completed',completed_at=clock_timestamp() WHERE id=1"))
            return result

        self.client.v1.payment_intents.create_async.side_effect = complete
        await self.run_pass()
        self.assertEqual(self.collected, 0)
        self.assertTrue(all(r['status'] == 'waived' for r in await self.rows()))

    async def test_completed_rental_accrues_nothing(self):
        async with self.db.begin() as connection:
            await connection.execute(
                text("UPDATE rentals SET status='completed',completed_at=clock_timestamp() WHERE id=1"))
        await self.run_pass()
        self.assertEqual(await self.rows(), [])

    async def test_old_consent_accrues_nothing(self):
        async with self.db.begin() as connection:
            await connection.execute(text("UPDATE rentals SET overdue_terms_version='2026-10-25' WHERE id=1"))
        await self.run_pass()
        self.assertEqual(await self.rows(), [])

    async def test_second_worker_skips_held_lock(self):
        async with self.db.connect() as connection:
            await connection.execute(text('SELECT pg_advisory_lock(:key)'), {'key': WORKER_LOCK})
            await connection.commit()
            try:
                await self.run_pass()
                self.assertEqual(await self.rows(), [])
                self.client.v1.payment_intents.create_async.assert_not_called()
            finally:
                await connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': WORKER_LOCK})
                await connection.commit()

    async def test_declined_payment_is_not_confirmed_again(self):
        def decline(key, _params, **_options):
            self.intents[key]['status'] = 'requires_payment_method'
            raise stripe.CardError('Declined', 'payment_method', 'card_declined')

        self.client.v1.payment_intents.confirm_async.side_effect = decline
        await self.run_pass()
        await self.run_pass()
        self.assertTrue(all(r['status'] == 'failed' for r in await self.rows()))
        self.assertEqual(self.client.v1.payment_intents.confirm_async.call_count, await self.expected_days())

    async def test_live_or_mismatched_intent_is_rejected(self):
        def invalid(params, options):
            result = self.create_intent(params, options).to_dict()
            result['livemode'] = True
            return self.response(result)

        self.client.v1.payment_intents.create_async.side_effect = invalid
        await self.run_pass()
        self.assertEqual(self.collected, 0)
        self.assertTrue(all(r['status'] == 'review' for r in await self.rows()))

    async def test_catches_up_missed_days_without_duplicates(self):
        start = datetime.now(IRELAND).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=4)
        async with self.db.begin() as connection:
            await connection.execute(text("""
                                          UPDATE rentals
                                          SET starts_at=:start,
                                              created_at=:start,
                                              overdue_terms_accepted_at=:start,
                                              reserved_until=:reserved,
                                              paid_until=:end
                                          WHERE id = 1
                                          """),
                                     {'start': start, 'reserved': start + timedelta(minutes=15),
                                      'end': rental_end(start, 1)})
        await self.run_pass()
        await self.run_pass()
        self.assertGreaterEqual(len(await self.rows()), 4)
        self.assertEqual(self.collected, await self.expected_days())
        self.assertEqual(len({r['charge_date'] for r in await self.rows()}), await self.expected_days())

    async def test_completion_uses_same_lock_during_confirmation(self):
        from sqlalchemy.exc import OperationalError

        async def confirm(key, params, options):
            async with self.db.connect() as connection:
                await connection.execute(text("SET LOCAL lock_timeout='100ms'"))
                with self.assertRaises(OperationalError):
                    await connection.execute(text("SELECT id FROM lockers WHERE id=1 FOR UPDATE"))
                await connection.rollback()
            return self.confirm_intent(key, params, options=options)

        self.client.v1.payment_intents.confirm_async.side_effect = confirm
        await self.run_pass()
        self.assertEqual(self.collected, await self.expected_days())

    async def test_processing_payment_waits_for_verified_success(self):
        def processing(key, _params, **_options):
            self.intents[key]['status'] = 'processing'
            return self.response(self.intents[key])

        self.client.v1.payment_intents.confirm_async.side_effect = processing
        await self.run_pass()
        await self.run_pass()
        self.assertTrue(all(r['status'] == 'pending' and r['paid_at'] is None for r in await self.rows()))
        for intent in self.intents.values():
            intent['status'] = 'succeeded'
        await self.run_pass()
        self.assertTrue(all(r['status'] == 'paid' for r in await self.rows()))
        self.assertEqual(self.client.v1.payment_intents.confirm_async.call_count, await self.expected_days())

    async def test_paid_response_after_completion_is_reconciled(self):
        def lost(key, params, options):
            self.confirm_intent(key, params, options=options)
            raise stripe.APIConnectionError('lost response')

        self.client.v1.payment_intents.confirm_async.side_effect = lost
        await self.run_pass()
        async with self.db.begin() as connection:
            await connection.execute(
                text("UPDATE rentals SET status='completed',completed_at=clock_timestamp() WHERE id=1"))
        await self.run_pass()
        self.assertTrue(all(r['status'] == 'paid' for r in await self.rows()))
        self.assertEqual(self.client.v1.payment_intents.confirm_async.call_count, await self.expected_days())

    async def test_cancelled_worker_releases_advisory_lock(self):
        entered = asyncio.Event()

        async def waiting_charge(*_args):
            entered.set()
            await asyncio.Event().wait()

        with patch('services.overdue_billing.process_charge', side_effect=waiting_charge):
            task = asyncio.create_task(run_billing_once(self.db))
            try:
                await asyncio.wait_for(entered.wait(), timeout=3)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        async with self.db.connect() as connection:
            acquired = await connection.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': WORKER_LOCK})
            try:
                self.assertTrue(acquired)
            finally:
                if acquired:
                    await connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': WORKER_LOCK})
                await connection.commit()
