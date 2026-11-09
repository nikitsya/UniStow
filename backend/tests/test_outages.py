import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from main import app


class OutagePageTests(unittest.TestCase):
    def test_rental_database_failure_offers_retry_and_support(self):
        with patch('routes.pages.current_user', side_effect=SQLAlchemyError('private detail')):
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get('/rentals/87')
        self.assertEqual(response.status_code, 503)
        self.assertIn('We can’t show your rental right now.', response.text)
        self.assertIn('href="/rentals/87">Try again', response.text)
        self.assertIn('/contact', response.text)
        self.assertNotIn('private detail', response.text)

    def test_unexpected_rental_failure_has_a_readable_page(self):
        with patch('routes.pages.current_user', side_effect=RuntimeError('private detail')):
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get('/rentals/87')
        self.assertEqual(response.status_code, 503)
        self.assertIn('We can’t show your rental right now.', response.text)
        self.assertNotIn('private detail', response.text)
