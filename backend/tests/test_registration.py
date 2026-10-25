import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database import get_db
from main import app
from users import password_hasher


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        with self.engine.begin() as connection:
            connection.execute(text("""
                                    CREATE TABLE users
                                    (
                                        id            INTEGER PRIMARY KEY,
                                        email         TEXT NOT NULL UNIQUE,
                                        password_hash TEXT NOT NULL
                                    )
                                    """))

        def test_db():
            with Session(self.engine) as db:
                yield db

        app.dependency_overrides[get_db] = test_db
        self.client = TestClient(app)
        self.password = "A long test password!"

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    def register(self, email="Person@example.com", password=None):
        return self.client.post("/api/register", json={
            "email": email,
            "password": self.password if password is None else password,
        })

    def test_registration_stores_hash_and_returns_public_fields(self):
        response = self.register()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {"id": 1, "email": "person@example.com"})
        with self.engine.connect() as connection:
            stored = connection.execute(text("SELECT password_hash FROM users")).scalar_one()
        self.assertNotEqual(stored, self.password)
        self.assertTrue(password_hasher.verify(self.password, stored))

    def test_duplicate_email_does_not_replace_password(self):
        self.register()
        response = self.register("PERSON@example.com", "A different password!")
        self.assertEqual(response.status_code, 409)
        with self.engine.connect() as connection:
            rows = connection.execute(text("SELECT password_hash FROM users")).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertTrue(password_hasher.verify(self.password, rows[0]))

    def test_invalid_input_is_rejected_without_echoing_password(self):
        for email, password in [
            ("invalid", self.password), ("person@example.com", "short-secret"),
            ("person@example.com", "x" * 129),
        ]:
            with self.subTest(email=email, length=len(password)):
                response = self.register(email, password)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn(password, response.text)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT count(*) FROM users")).scalar_one(), 0)


if __name__ == "__main__":
    unittest.main()
