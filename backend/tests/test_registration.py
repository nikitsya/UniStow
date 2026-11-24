import unittest

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from auth.users import create_user, password_hasher
from database import get_db
from main import app


class RegistrationTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite://", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with self.engine.begin() as connection:
            await connection.execute(text("""
                                    CREATE TABLE users
                                    (
                                        id            INTEGER PRIMARY KEY,
                                        email         TEXT NOT NULL UNIQUE,
                                        password_hash TEXT NOT NULL,
                                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                                    )
                                    """))

        async def test_db():
            async with AsyncSession(self.engine) as db:
                yield db

        app.dependency_overrides[get_db] = test_db
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)
        self.password = "A long test password!"

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.clear()
        await self.engine.dispose()

    async def register(self, email="Person@example.com", password=None, headers=None):
        return await self.client.post("/api/register", json={
            "email": email,
            "password": self.password if password is None else password,
        }, headers={"X-UniStow-Request": "1"} if headers is None else headers)


class RegistrationTests(RegistrationTestCase):
    async def test_registration_stores_hash_and_returns_public_fields(self):
        response = await self.register()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {"id": 1, "email": "person@example.com"})
        async with self.engine.connect() as connection:
            stored = (await connection.execute(text("SELECT password_hash FROM users"))).scalar_one()
        self.assertNotEqual(stored, self.password)
        self.assertTrue(password_hasher.verify(self.password, stored))

    async def test_browser_header_required(self):
        for headers in ({}, {"X-UniStow-Request": "invalid"}):
            with self.subTest(headers=headers):
                self.assertEqual((await self.register(headers=headers)).status_code, 403)
        async with self.engine.connect() as connection:
            self.assertEqual((await connection.execute(text("SELECT count(*) FROM users"))).scalar_one(), 0)

    async def test_duplicate_email_does_not_replace_password(self):
        await self.register()
        response = await self.register("PERSON@example.com", "A different password!")
        self.assertEqual(response.status_code, 409)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(text("SELECT password_hash FROM users"))).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertTrue(password_hasher.verify(self.password, rows[0]))

    async def test_duplicate_registration_leaves_session_usable(self):
        async with AsyncSession(self.engine) as db:
            first = await create_user(db, "first@example.com", self.password)
            duplicate = await create_user(db, "first@example.com", "A different password!")
            second = await create_user(db, "second@example.com", self.password)
        self.assertIsNone(duplicate)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["email"], "second@example.com")

    async def test_invalid_input_is_rejected_without_echoing_password(self):
        for email, password in [
            ("invalid", self.password), ("person@example.com", "short-secret"),
            ("person@example.com", "x" * 129),
        ]:
            with self.subTest(email=email, length=len(password)):
                response = await self.register(email, password)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn(password, response.text)
        async with self.engine.connect() as connection:
            self.assertEqual((await connection.execute(text("SELECT count(*) FROM users"))).scalar_one(), 0)


if __name__ == "__main__":
    unittest.main()
