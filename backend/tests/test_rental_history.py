import os
import unittest

from sqlalchemy import text

import test_commands


@unittest.skipUnless(os.environ.get('UNISTOW_TEST_POSTGRES') == '1', 'Requires PostgreSQL')
class RentalHistoryTests(unittest.TestCase):
    setUp = test_commands.CommandTests.setUp
    tearDown = test_commands.CommandTests.tearDown
    report = test_commands.CommandTests.report

    def completed(self, minutes):
        return self.connection.execute(text("""
                                            INSERT INTO rentals (user_id, locker_id, status, prepaid_days,
                                                                 reserved_until, starts_at, paid_until, completed_at,
                                                                 total_price_cents, overdue_terms_version,
                                                                 overdue_terms_accepted_at, created_at)
                                            SELECT user_id,
                                                   locker_id,
                                                   'completed',
                                                   prepaid_days,
                                                   reserved_until,
                                                   starts_at,
                                                   paid_until,
                                                   starts_at + :minutes * INTERVAL '1 minute',
                                                   total_price_cents,
                                                   overdue_terms_version,
                                                   overdue_terms_accepted_at,
                                                   created_at
                                            FROM rentals
                                            WHERE id = :id
                                            RETURNING id
                                            """), {'minutes': minutes, 'id': self.rental_id}).scalar_one()

    def test_latest_five_by_completion_time_persist_on_refresh(self):
        ids = [self.completed(minutes) for minutes in (7, 2, 6, 1, 5, 3, 4)]
        expected = [ids[i] for i in (0, 2, 4, 6, 5)]
        for _ in range(2):
            response = self.client.get('/rentals')
            self.assertEqual(response.status_code, 200)
            history = response.text.split('id="completed-rentals-heading"')[1]
            positions = [history.index(f'Rental #{item} ·') for item in expected]
            self.assertEqual(positions, sorted(positions))
            for item in (ids[1], ids[3]):
                self.assertNotIn(f'Rental #{item} ·', history)
            self.assertIn(f'Rental #{self.rental_id} · Active', response.text)
            self.assertIn('/contact', history)

    def test_owner_only_and_completed_details_have_no_locker_controls(self):
        rental_id = self.completed(1)
        page = self.client.get(f'/rentals/{rental_id}')
        self.assertEqual(page.status_code, 200)
        self.assertNotIn('id="open-locker-button"', page.text)
        self.assertNotIn('id="finish-rental-button"', page.text)
        self.client.cookies.set('unistow_session', 'session-2')
        self.assertNotIn('Recently completed', self.client.get('/rentals').text)
        self.assertEqual(self.client.get(f'/rentals/{rental_id}').status_code, 404)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/rentals', follow_redirects=False).status_code, 303)
        self.client.cookies.set('unistow_session', 'session-1')
        self.assertIn(f'Rental #{rental_id} ·', self.client.get('/rentals').text)

    def test_history_remains_without_current_rentals(self):
        self.connection.execute(text("""
                                     UPDATE rentals
                                     SET status='completed',
                                         completed_at=clock_timestamp()
                                     WHERE id = :id
                                     """), {'id': self.rental_id})
        page = self.client.get('/rentals').text
        self.assertIn('No current rentals', page)
        self.assertIn(f'Rental #{self.rental_id} · Locker 4', page)
        self.assertIn('Completed ·', page)
