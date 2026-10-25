import unittest

from sqlalchemy import text

import test_registration


class LoginTests(unittest.TestCase):
    register = test_registration.RegistrationTests.register
    tearDown = test_registration.RegistrationTests.tearDown

    def __init__(self, methodName: str = "runTest"):
        super().__init__(methodName)
        self.client = None
        self.engine = None

    def setUp(self):
        test_registration.RegistrationTests.setUp(self)
        with self.engine.begin() as connection:
            connection.execute(text("""
                                    CREATE TABLE sessions
                                    (
                                        token_hash TEXT PRIMARY KEY,
                                        user_id    INTEGER   NOT NULL,
                                        expires_at TIMESTAMP NOT NULL
                                    )
                                    """))
        self.register()

    def login(self, email="PERSON@example.com", password=None, headers=None):
        return self.client.post('/api/login', json={
            'email': email, 'password': self.password if password is None else password,
        }, headers={'X-PayBox-Request': '1'} if headers is None else headers)

    def test_session_and_logout(self):
        self.assertEqual(self.client.get('/api/me').status_code, 401)
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'id': 1, 'email': 'person@example.com'})
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('SameSite=lax', response.headers['set-cookie'])
        token = self.client.cookies['paybox_session']
        with self.engine.connect() as connection:
            stored = connection.execute(text('SELECT token_hash FROM sessions')).scalar_one()
        self.assertNotEqual(stored, token)
        self.assertEqual(self.client.get('/api/me').status_code, 200)
        self.assertEqual(self.client.get('/api/me').headers['cache-control'], 'no-store')
        self.assertEqual(self.client.post('/api/logout', headers={'X-PayBox-Request': '1'}).status_code, 204)
        self.client.cookies.set('paybox_session', token)
        self.assertEqual(self.client.get('/api/me').status_code, 401)

    def test_wrong_credentials(self):
        wrong = self.login(password='wrong')
        unknown = self.login(email='unknown@example.com')
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(wrong.json(), unknown.json())
        self.assertNotIn('set-cookie', wrong.headers)

    def test_expired_and_forged_sessions(self):
        self.login()
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE sessions SET expires_at = '2000-01-01'"))
        self.assertEqual(self.client.get('/api/me').status_code, 401)
        self.client.cookies.clear()
        self.client.cookies.set('paybox_session', 'invented-token')
        self.assertEqual(self.client.get('/api/me').status_code, 401)

    def test_login_rotates_session(self):
        self.login()
        old = self.client.cookies['paybox_session']
        self.login()
        self.assertNotEqual(old, self.client.cookies['paybox_session'])
        self.client.cookies.clear()
        self.client.cookies.set('paybox_session', old)
        self.assertEqual(self.client.get('/api/me').status_code, 401)

    def test_browser_header_required(self):
        self.assertEqual(self.login(headers={}).status_code, 403)
        self.login()
        self.assertEqual(self.client.post('/api/logout').status_code, 403)
        self.assertEqual(self.client.get('/api/me').status_code, 200)

    def test_login_page(self):
        response = self.client.get('/login')
        self.assertEqual(response.status_code, 200)
        self.assertIn('autocomplete="current-password"', response.text)
        self.assertEqual(self.client.get('/static/js/login.js').status_code, 200)
