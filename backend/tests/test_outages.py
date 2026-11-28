import unittest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from unittest.mock import patch

from main import app


class OutagePageTests(unittest.IsolatedAsyncioTestCase):
    async def test_rental_database_failure_offers_retry_and_support(self):
        with patch('routes.pages.current_user', side_effect=SQLAlchemyError('private detail')):
            async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                                   base_url="http://testserver", follow_redirects=True) as client:
                response = await client.get('/rentals/87')
        self.assertEqual(response.status_code, 503)
        self.assertIn('We can’t show your rental right now.', response.text)
        self.assertIn('href="/rentals/87">Try again', response.text)
        self.assertIn('/contact', response.text)
        self.assertNotIn('private detail', response.text)

    async def test_unexpected_rental_failure_has_a_readable_page(self):
        with patch('routes.pages.current_user', side_effect=RuntimeError('private detail')):
            async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                                   base_url="http://testserver", follow_redirects=True) as client:
                response = await client.get('/rentals/87')
        self.assertEqual(response.status_code, 503)
        self.assertIn('We can’t show your rental right now.', response.text)
        self.assertNotIn('private detail', response.text)


class RequestErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_page_has_html_and_navigation(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get('/missing-page')
        self.assertEqual(response.status_code, 404)
        self.assertIn('text/html', response.headers['content-type'])
        self.assertIn('Error 404', response.text)
        self.assertIn('Back to lockers', response.text)

    async def test_missing_api_route_keeps_json(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get('/api/missing-route')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {'detail': 'Not Found'})

    async def test_invalid_page_parameter_has_html_without_input(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get('/login', params={'locker_id': 'private-invalid-value'})
        self.assertEqual(response.status_code, 422)
        self.assertIn('text/html', response.headers['content-type'])
        self.assertIn('Please check the page address', response.text)
        self.assertNotIn('private-invalid-value', response.text)

    async def test_invalid_api_parameter_keeps_sanitised_json(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post('/api/login', json={'email': 'invalid', 'password': 'private-password'},
                                         headers={'X-UniStow-Request': '1'})
        self.assertEqual(response.status_code, 422)
        self.assertIsInstance(response.json()['detail'], list)
        self.assertNotIn('private-password', response.text)
        self.assertTrue(all('input' not in error for error in response.json()['detail']))
