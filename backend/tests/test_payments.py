import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException

import test_login
from payments import checkout_parameters, stripe_client


class PaymentSettingsTests(unittest.TestCase):
    def test_only_test_keys_are_accepted(self):
        for key in ['', 'sk_live_not_a_real_key']:
            with patch.dict('os.environ', {'STRIPE_SECRET_KEY': key}):
                with self.assertRaises(HTTPException) as error:
                    stripe_client()
                self.assertEqual(error.exception.status_code, 503)

    def test_checkout_uses_saved_amount(self):
        now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
        rental = {'id': 7, 'number': 4, 'prepaid_days': 2,
                  'total_price_cents': 200, 'paid_until': now + timedelta(days=1)}
        params = checkout_parameters(rental, 'http://127.0.0.1:8000', now)
        self.assertEqual(params['line_items'][0]['price_data']['unit_amount'], 200)
        self.assertEqual(params['line_items'][0]['price_data']['currency'], 'eur')
        self.assertEqual(params['payment_method_types'], ['card'])
        self.assertEqual(params['metadata'], {'rental_id': '7'})
        self.assertEqual(params['expires_at'], int((now + timedelta(minutes=31)).timestamp()))
        rental['paid_until'] = now + timedelta(minutes=20)
        with self.assertRaises(HTTPException):
            checkout_parameters(rental, 'http://127.0.0.1:8000', now)


class CheckoutAccessTests(unittest.TestCase):
    setUp = test_login.LoginTests.setUp
    tearDown = test_login.LoginTests.tearDown
    register = test_login.LoginTests.register
    login = test_login.LoginTests.login

    def test_checkout_requires_session_and_browser_header(self):
        path = '/api/rentals/7/checkout'
        headers = {'X-PayBox-Request': '1'}
        self.assertEqual(self.client.post(path, headers=headers).status_code, 401)
        self.login()
        self.assertEqual(self.client.post(path).status_code, 403)
        with patch('routes.api.create_checkout', return_value={'url': 'https://checkout.stripe.com/test'}) as checkout:
            self.assertEqual(self.client.post(path, headers=headers).status_code, 200)
            self.assertEqual(checkout.call_args.args[1:], (7, 1))

    def test_return_url_does_not_confirm_payment(self):
        self.login()
        rental = {'id': 7, 'number': 4, 'prepaid_days': 2, 'total_price_cents': 200, 'status': 'pending',
                  'paid_until': datetime(2026, 10, 26, 18, tzinfo=timezone.utc), 'payment_status': None}
        with patch('routes.pages.owned_rental', return_value=rental):
            response = self.client.get('/rentals/7?returned=1')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Status: pending', response.text)
        self.assertIn('Payment: Not paid', response.text)

    def test_my_rental_shows_local_deadline_and_payment(self):
        self.login()
        for month, utc_hour, month_name in [(7, 17, 'July'), (12, 18, 'December')]:
            rental = {'id': 7, 'number': 4, 'prepaid_days': 2, 'total_price_cents': 200,
                      'status': 'active', 'payment_status': 'paid',
                      'paid_until': datetime(2026, month, 26, utc_hour, tzinfo=timezone.utc)}
            with self.subTest(month=month), patch('routes.pages.owned_rental', return_value=rental):
                response = self.client.get('/rentals/7')
                self.assertEqual(response.status_code, 200)
                self.assertIn('My rental · Locker 4', response.text)
                self.assertIn(f'26 {month_name} 2026 at 18:00', response.text)
                self.assertIn('Payment: Paid', response.text)
                self.assertNotIn('id="pay-button"', response.text)
