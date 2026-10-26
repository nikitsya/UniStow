import os
from pathlib import Path
import secrets
import unittest
from uuid import uuid4
from unittest.mock import patch
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import engine, get_db
from main import app
from rentals import reserve_locker, rental_end
from sessions import token_hash


@unittest.skipUnless(os.environ.get('PAYBOX_TEST_POSTGRES') == '1', 'Requires PostgreSQL; isolated schema rolled back')
class CommandTests(unittest.TestCase):
    def setUp(self):
        self.connection = engine.connect()
        self.transaction = self.connection.begin()
        schema = 'test_commands_' + secrets.token_hex(6)
        self.connection.exec_driver_sql(f'CREATE SCHEMA {schema}')
        self.connection.exec_driver_sql(f'SET LOCAL search_path TO {schema}')
        source = (Path(__file__).resolve().parents[1] / 'database/schema.sql').read_text()
        self.connection.exec_driver_sql(source.replace('BEGIN;', '').replace('COMMIT;', ''))
        self.connection.execute(text("INSERT INTO users(email,password_hash) VALUES ('owner@example.com','test'),('other@example.com','test')"))
        self.connection.execute(text("INSERT INTO lockers(number,size,price_per_day_cents,is_enabled) VALUES(4,'small',100,true),(5,'small',100,true)"))
        with Session(bind=self.connection, join_transaction_mode='create_savepoint') as db:
            self.rental = reserve_locker(db, 1, 1, 2)
        self.rental_id = self.rental['id']
        self.connection.execute(text("UPDATE rentals SET status='active' WHERE id=:id"), {'id': self.rental_id})
        self.connection.execute(text("INSERT INTO payments(rental_id,request_key,checkout_params,status) VALUES(:id,'fixture','{}','paid')"), {'id': self.rental_id})
        for user in (1, 2):
            self.connection.execute(text("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(:token,:user,CURRENT_TIMESTAMP+INTERVAL '1 hour')"), {'token': token_hash(f'session-{user}'), 'user': user})
        def test_db():
            with Session(bind=self.connection, join_transaction_mode='create_savepoint') as db:
                yield db
        app.dependency_overrides[get_db] = test_db
        self.client = TestClient(app)
        self.client.cookies.set('paybox_session', 'session-1')
        key = patch.dict('os.environ', {'PAYBOX_DEVICE_KEY': 'test-device-key'})
        key.start()
        self.addCleanup(key.stop)
        self.controller = str(uuid4())
        self.device_headers = {'Authorization': 'Bearer test-device-key'}
        self.report()

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.transaction.rollback()
        self.connection.close()

    def report(self, ready=True, door='closed', controller=None):
        return self.client.post('/api/device/heartbeat', headers=self.device_headers,
            json={'door_state': door, 'ready': ready, 'controller_id': controller or self.controller})

    def open(self):
        return self.client.post(f'/api/rentals/{self.rental_id}/open', headers={'X-PayBox-Request': '1'})

    def take(self, controller=None):
        return self.client.post('/api/device/commands/next', headers=self.device_headers,
                                json={'controller_id': controller or self.controller})

    def test_owner_paid_flow_dispatches_once(self):
        response = self.open()
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(self.open().status_code, 409)
        result = self.take().json()['command']
        self.assertEqual(result['id'], response.json()['command_id'])
        self.assertEqual(result['action'], 'open')
        self.assertLessEqual(result['valid_for_seconds'], 10)
        self.assertIsNone(self.take().json()['command'])
        self.report()
        self.assertIsNone(self.take().json()['command'])

    def test_authentication_ownership_and_browser_header(self):
        self.client.cookies.clear()
        self.assertEqual(self.open().status_code, 401)
        self.client.cookies.set('paybox_session', 'session-2')
        self.assertEqual(self.open().status_code, 404)
        self.client.cookies.set('paybox_session', 'session-1')
        self.assertEqual(self.client.post(f'/api/rentals/{self.rental_id}/open').status_code, 403)
        self.assertEqual(self.client.post('/api/device/commands/next', json={'controller_id': self.controller}).status_code, 401)

    def test_unpaid_offline_open_and_legacy_report_cannot_unlock(self):
        self.connection.execute(text("UPDATE payments SET status='pending'"))
        self.assertEqual(self.open().status_code, 409)
        self.connection.execute(text("UPDATE payments SET status='paid'"))
        self.report(door='open')
        self.assertEqual(self.open().status_code, 409)
        self.report(ready=False)
        self.assertEqual(self.open().status_code, 409)
        self.report()
        self.connection.execute(text("UPDATE lockers SET last_seen_at=CURRENT_TIMESTAMP-INTERVAL '1 minute'"))
        self.assertEqual(self.open().status_code, 409)
        self.client.post('/api/device/heartbeat', headers=self.device_headers, json={'door_state': 'closed'})
        self.assertEqual(self.open().status_code, 409)

    def test_expired_command_and_restarted_controller_do_not_open(self):
        self.assertEqual(self.open().status_code, 202)
        self.connection.execute(text("UPDATE locker_commands SET created_at=created_at-INTERVAL '1 minute', expires_at=expires_at-INTERVAL '1 minute'"))
        self.assertIsNone(self.take().json()['command'])
        self.assertEqual(self.open().status_code, 202)
        new_controller = str(uuid4())
        self.report(controller=new_controller)
        self.assertIsNone(self.take(new_controller).json()['command'])
        self.assertIsNone(self.take().json()['command'])

    def test_rental_and_payment_are_rechecked_on_dispatch(self):
        self.assertEqual(self.open().status_code, 202)
        self.connection.execute(text("UPDATE payments SET status='expired'"))
        self.assertIsNone(self.take().json()['command'])
        self.connection.execute(text("UPDATE payments SET status='paid'"))
        self.connection.execute(text("UPDATE rentals SET status='cancelled'"))
        self.assertIsNone(self.take().json()['command'])

    def test_expired_paid_period_cannot_open(self):
        start = self.connection.execute(text('SELECT starts_at FROM rentals')).scalar_one() - timedelta(days=7)
        self.connection.execute(text('''UPDATE rentals SET starts_at=:start, created_at=:start,
            overdue_terms_accepted_at=:start, reserved_until=:reserved, paid_until=:end'''),
            {'start': start, 'reserved': start + timedelta(minutes=15), 'end': rental_end(start, 2)})
        self.assertEqual(self.open().status_code, 409)

    def test_button_shown_only_for_ready_paid_owner(self):
        response = self.client.get(f'/rentals/{self.rental_id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="open-locker-button"', response.text)
        self.assertNotIn('disabled>Open locker', response.text)
        self.report(ready=False)
        self.assertIn('disabled>Open locker', self.client.get(f'/rentals/{self.rental_id}').text)
