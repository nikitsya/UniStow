import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from pydantic import ValidationError

import test_login
from rentals import rental_end
from schemas import RentalRequest


class RentalDatesTests(unittest.TestCase):
    def test_calendar_days_and_cutoff(self):
        for days in range(1, 5):
            for hour, expected_day in [(17, 10 + days - 1), (18, 10 + days)]:
                end = rental_end(datetime(2026, 1, 10, hour, tzinfo=timezone.utc), days)
                self.assertEqual((end.day, end.hour), (expected_day, 18))

    def test_daylight_saving_changes(self):
        spring = rental_end(datetime(2026, 3, 28, 12, tzinfo=timezone.utc), 2)
        autumn = rental_end(datetime(2026, 10, 24, 12, tzinfo=timezone.utc), 2)
        self.assertEqual(spring.astimezone(timezone.utc).hour, 17)
        self.assertEqual(autumn.astimezone(timezone.utc).hour, 18)

    def test_days_and_consent_are_required(self):
        for changes in [{'days': 0}, {'days': 5}, {'days': 1.5}, {'days': True},
                        {'overdue_consent': False}, {'overdue_consent': 'true'}]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                RentalRequest(**({'locker_id': 4, 'days': 1, 'overdue_consent': True} | changes))


class RentalAccessTests(unittest.TestCase):
    setUp = test_login.LoginTests.setUp
    tearDown = test_login.LoginTests.tearDown
    register = test_login.LoginTests.register
    login = test_login.LoginTests.login

    def test_server_uses_signed_in_user(self):
        body = {'locker_id': 4, 'days': 2, 'overdue_consent': True, 'user_id': 999}
        headers = {'X-PayBox-Request': '1'}
        self.assertEqual(self.client.post('/api/rentals', json=body, headers=headers).status_code, 401)
        self.login()
        self.assertEqual(self.client.post('/api/rentals', json=body).status_code, 403)
        with patch('routes.api.reserve_locker', return_value={'id': 1, 'status': 'pending'}) as reserve:
            self.assertEqual(self.client.post('/api/rentals', json=body, headers=headers).status_code, 201)
            self.assertEqual(reserve.call_args.args[1:], (1, 4, 2))
            body['overdue_consent'] = False
            self.assertEqual(self.client.post('/api/rentals', json=body, headers=headers).status_code, 422)
            self.assertEqual(reserve.call_count, 1)
