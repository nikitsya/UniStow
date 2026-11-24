import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import text

import test_login
from main import app
from services.payments import checkout_parameters, stripe_client


class PaymentSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_test_keys_are_accepted(self):
        for key in ['', 'sk_live_not_a_real_key']:
            with patch.dict('os.environ', {'STRIPE_SECRET_KEY': key}):
                with self.assertRaises(HTTPException) as error:
                    stripe_client()
                self.assertEqual(error.exception.status_code, 503)

    async def test_checkout_uses_saved_amount(self):
        now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
        rental: dict[str, int | datetime | str] = {
            'id': 7, 'number': 4, 'prepaid_days': 2,
            'total_price_cents': 200, 'paid_until': now + timedelta(days=1),
        }
        params = checkout_parameters(rental, 'http://127.0.0.1:8000', now)
        self.assertEqual(params['branding_settings']['display_name'], 'UniStow')
        self.assertEqual(params['line_items'][0]['price_data']['unit_amount'], 200)
        self.assertEqual(params['line_items'][0]['price_data']['currency'], 'eur')
        self.assertEqual(params['payment_method_types'], ['card'])
        self.assertEqual(params['metadata'], {'rental_id': '7'})
        self.assertEqual(params['expires_at'], int((now + timedelta(minutes=31)).timestamp()))
        self.assertNotIn('payment_intent_data', params)
        rental['overdue_terms_version'] = '2026-11-09'
        params = checkout_parameters(rental, 'http://127.0.0.1:8000', now)
        self.assertEqual(params['customer_creation'], 'always')
        self.assertEqual(params['payment_intent_data']['setup_future_usage'], 'off_session')
        rental['paid_until'] = now + timedelta(minutes=20)
        with self.assertRaises(HTTPException):
            checkout_parameters(rental, 'http://127.0.0.1:8000', now)


class CheckoutAccessTests(test_login.LoginTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.engine.begin() as connection:
            await connection.execute(text("""
                                          CREATE TABLE overdue_charges
                                          (
                                              rental_id    INTEGER NOT NULL,
                                              status       TEXT    NOT NULL,
                                              amount_cents INTEGER NOT NULL
                                          )
                                          """))

            await connection.execute(text("""
                                          CREATE TABLE overdue_payments
                                          (
                                              rental_id    INTEGER NOT NULL,
                                              status       TEXT    NOT NULL,
                                              amount_cents INTEGER NOT NULL
                                          )
                                          """))

    async def test_checkout_requires_session_and_browser_header(self):
        path = '/api/rentals/7/checkout'
        headers = {'X-UniStow-Request': '1'}
        self.assertEqual((await self.client.post(path, headers=headers)).status_code, 401)
        await self.login()
        self.assertEqual((await self.client.post(path)).status_code, 403)
        with patch('routes.api.create_checkout', return_value={'url': 'https://checkout.stripe.com/test'}) as checkout:
            self.assertEqual((await self.client.post(path, headers=headers)).status_code, 200)
            self.assertEqual(checkout.call_args.args[1:], (7, 1))

    async def test_return_url_does_not_confirm_payment(self):
        await self.login()
        rental = {'id': 7, 'number': 4, 'prepaid_days': 2, 'total_price_cents': 200, 'status': 'pending',
                  'paid_until': datetime(2026, 10, 26, 18, tzinfo=timezone.utc), 'payment_status': None}
        with patch('routes.pages.owned_rental', return_value=rental):
            response = await self.client.get('/rentals/7?returned=1')
        self.assertEqual(response.status_code, 200)
        self.assertRegex(response.text, r'class="rental-state state-pending">Pending</span>')
        self.assertRegex(response.text, r'<dt>Payment</dt>\s*<dd>\s*Not\s+paid\s*</dd>')

    async def test_delivery_button_accepts_click_without_recent_telemetry(self):
        await self.login()
        now = datetime.now(timezone.utc)
        rental = {'id': 7, 'number': 4, 'prepaid_days': 1, 'total_price_cents': 100,
                  'status': 'active', 'paid_until': now + timedelta(hours=1), 'payment_status': 'paid'}
        events = Mock()
        events.latest.return_value = None
        events.test_open.return_value = {'status': 'test_acknowledged', 'command_id': 'test-command'}
        with patch.object(app.state, 'open_test_client', object(), create=True):
            with patch.object(app.state, 'door_events', events, create=True):
                with patch('routes.pages.owned_rental', return_value=rental):
                    with patch('routes.api.authorised_locker', return_value=({}, rental, now)) as authorise:
                        page = await self.client.get('/rentals/7')
                        button = page.text.split('id="open-locker-button"', 1)[1].split('>', 1)[0]
                        self.assertNotIn('disabled', button)
                        headers = {'X-UniStow-Request': '1'}
                        self.assertEqual((await self.client.post('/api/rentals/7/open', headers=headers)).status_code,
                                         409)
                        response = await self.client.post('/api/rentals/7/test-open', headers=headers)
                        self.assertEqual(response.json()['status'], 'test_acknowledged')
                        authorise.assert_called_once()
                        events.test_open.assert_called_once()

    async def test_my_rental_shows_local_deadline_and_payment(self):
        await self.login()
        for month, utc_hour, month_name in [(7, 17, 'July'), (12, 18, 'December')]:
            rental = {'id': 7, 'number': 4, 'prepaid_days': 2, 'total_price_cents': 200,
                      'status': 'active', 'payment_status': 'paid',
                      'paid_until': datetime(2026, month, 26, utc_hour, tzinfo=timezone.utc)}
            with self.subTest(month=month):
                with patch('routes.pages.owned_rental', return_value=rental):
                    response = await self.client.get('/rentals/7')
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('My rental · Locker 4', response.text)
                    self.assertIn(f'26 {month_name} 2026 at 18:00', response.text)
                    self.assertRegex(response.text, r'<dt>Payment</dt>\s*<dd>\s*Paid\s*</dd>')
                    self.assertNotIn('id="pay-button"', response.text)
