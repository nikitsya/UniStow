from unittest.mock import patch

from sqlalchemy import text

import test_registration


class LoginTestCase(test_registration.RegistrationTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        eligibility = patch('routes.pages.booking_eligibility', return_value={
            'can_book': True, 'unpaid_cents': 0, 'processing_cents': 0, 'rentals': [],
        })
        eligibility.start()
        self.addCleanup(eligibility.stop)
        async with self.engine.begin() as connection:
            await connection.execute(text("""
                                          CREATE TABLE sessions
                                          (
                                              token_hash TEXT PRIMARY KEY,
                                              user_id    INTEGER   NOT NULL,
                                              expires_at TIMESTAMP NOT NULL
                                          )
                                          """))
        await self.register()

    async def login(self, email="PERSON@example.com", password=None, headers=None):
        return await self.client.post('/api/login', json={
            'email': email, 'password': self.password if password is None else password,
        }, headers={'X-UniStow-Request': '1'} if headers is None else headers)


class LoginTests(LoginTestCase):
    async def test_session_and_logout(self):
        self.assertEqual((await self.client.get('/api/me')).status_code, 401)
        response = await self.login()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'id': 1, 'email': 'person@example.com'})
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('SameSite=lax', response.headers['set-cookie'])
        token = self.client.cookies['unistow_session']
        async with self.engine.connect() as connection:
            stored = (await connection.execute(text('SELECT token_hash FROM sessions'))).scalar_one()
        self.assertNotEqual(stored, token)
        self.assertEqual((await self.client.get('/api/me')).status_code, 200)
        self.assertEqual((await self.client.get('/api/me')).headers['cache-control'], 'no-store')
        self.assertEqual((await self.client.post('/api/logout', headers={'X-UniStow-Request': '1'})).status_code, 204)
        self.client.cookies.set('unistow_session', token)
        self.assertEqual((await self.client.get('/api/me')).status_code, 401)

    async def test_wrong_credentials(self):
        wrong = await self.login(password='wrong')
        unknown = await self.login(email='unknown@example.com')
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(wrong.json(), unknown.json())
        self.assertNotIn('set-cookie', wrong.headers)

    async def test_expired_and_forged_sessions(self):
        await self.login()
        async with self.engine.begin() as connection:
            await connection.execute(text("UPDATE sessions SET expires_at = '2000-01-01' WHERE user_id = 1"))
        self.assertEqual((await self.client.get('/api/me')).status_code, 401)
        self.client.cookies.clear()
        self.client.cookies.set('unistow_session', 'invented-token')
        self.assertEqual((await self.client.get('/api/me')).status_code, 401)

    async def test_login_rotates_session(self):
        await self.login()
        old = self.client.cookies['unistow_session']
        await self.login()
        self.assertNotEqual(old, self.client.cookies['unistow_session'])
        self.client.cookies.clear()
        self.client.cookies.set('unistow_session', old)
        self.assertEqual((await self.client.get('/api/me')).status_code, 401)

    async def test_browser_header_required(self):
        self.assertEqual((await self.login(headers={})).status_code, 403)
        await self.login()
        self.assertEqual((await self.client.post('/api/logout')).status_code, 403)
        self.assertEqual((await self.client.get('/api/me')).status_code, 200)

    async def test_login_page(self):
        response = await self.client.get('/login')
        self.assertEqual(response.status_code, 200)
        self.assertIn('autocomplete="current-password"', response.text)
        self.assertEqual((await self.client.get('/static/js/account/login.js')).status_code, 200)

    async def test_rental_requires_valid_session(self):
        path = '/lockers/4/rent'
        response = await self.client.get(path, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], '/login?locker_id=4')
        self.assertIn('data-destination="/lockers/4/rent"', (await self.client.get(response.headers['location'])).text)
        await self.login()
        locker = {'id': 4, 'number': 4, 'size': 'Small', 'name': 'Locker 4', 'status': 'available',
                  'price_per_day_cents': 100}
        with patch('routes.pages.read_lockers', return_value=[locker]):
            self.assertEqual((await self.client.get(path)).status_code, 200)
        async with self.engine.begin() as connection:
            await connection.execute(text("UPDATE sessions SET expires_at = '2000-01-01' WHERE user_id = 1"))
        self.assertEqual((await self.client.get(path, follow_redirects=False)).status_code, 303)

    async def test_my_rentals_requires_valid_session(self):
        with patch('routes.pages.current_rentals', return_value=[]) as rentals:
            with patch('routes.pages.completed_rentals', return_value=[]):
                response = await self.client.get('/rentals', follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers['location'], '/login?my_rentals=true')
                rentals.assert_not_called()
                self.assertIn('data-destination="/rentals"',
                              (await self.client.get(response.headers['location'])).text)
                await self.login()
                self.assertEqual((await self.client.get('/rentals')).status_code, 200)
                rentals.assert_called_once()
                rentals.reset_mock()
                async with self.engine.begin() as connection:
                    await connection.execute(text("UPDATE sessions SET expires_at = '2000-01-01' WHERE user_id = 1"))
                self.assertEqual((await self.client.get('/rentals', follow_redirects=False)).status_code, 303)
                rentals.assert_not_called()

    async def test_login_destination_stays_local(self):
        self.assertIn('data-destination="/"', (await self.client.get('/login')).text)
        self.assertEqual((await self.client.get('/login', params={'locker_id': 'https://example.com'})).status_code,
                         422)
