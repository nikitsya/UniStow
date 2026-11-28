import unittest
from fastapi import FastAPI
from fastapi.exceptions import ResponseValidationError
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, patch

from auth.sessions import check_browser_request
from database import get_db
from routes.api import router


class ResponseSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: None
        app.dependency_overrides[check_browser_request] = lambda: None
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        self.addAsyncCleanup(self.client.aclose)

    async def test_registration_filters_private_fields(self):
        user = {'id': 1, 'email': 'student@example.com', 'password_hash': 'private'}
        with patch('routes.api.create_user', new=AsyncMock(return_value=user)):
            response = await self.client.post('/api/register', json={
                'email': 'student@example.com', 'password': 'a-long-test-password',
            })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {'id': 1, 'email': 'student@example.com'})

    async def test_lockers_reject_incomplete_service_response(self):
        with patch('routes.api.read_lockers', new=AsyncMock(return_value=[{'id': 1}])):
            with self.assertRaises(ResponseValidationError):
                await self.client.get('/api/lockers')
