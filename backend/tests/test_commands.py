import os
import secrets
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

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
        self.connection.execute(text(
            "INSERT INTO users(email,password_hash) VALUES ('owner@example.com','test'),('other@example.com','test')"))
        self.connection.execute(text(
            "INSERT INTO lockers(number,size,price_per_day_cents,is_enabled) VALUES(4,'small',100,true),(5,'small',100,true)"))
        with Session(bind=self.connection, join_transaction_mode='create_savepoint') as db:
            self.rental = reserve_locker(db, 1, 1, 2)
        self.rental_id = self.rental['id']
        self.connection.execute(text("UPDATE rentals SET status='active' WHERE id=:id"), {'id': self.rental_id})
        self.connection.execute(text(
            "INSERT INTO payments(rental_id,request_key,checkout_params,status) VALUES(:id,'fixture','{}','paid')"),
            {'id': self.rental_id})
        for user in (1, 2):
            self.connection.execute(text(
                "INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(:token,:user,CURRENT_TIMESTAMP+INTERVAL '1 hour')"),
                {'token': token_hash(f'session-{user}'), 'user': user})

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
                                json={'door_state': door, 'ready': ready,
                                      'controller_id': controller or self.controller})

    def test_home_continues_only_the_owners_current_rental(self):
        for status in ('pending', 'active', 'overdue'):
            self.connection.execute(text('UPDATE rentals SET status=:status'), {'status': status})
            self.client.cookies.set('paybox_session', 'session-1')
            page = self.client.get('/')
            self.assertEqual(page.status_code, 200)
            self.assertIn('Continue rental for Locker 4', page.text)
            self.assertIn(f'/rentals/{self.rental_id}', page.text)
            self.client.cookies.set('paybox_session', 'session-2')
            self.assertNotIn('Continue rental', self.client.get('/').text)
        self.client.cookies.set('paybox_session', 'session-1')
        for status in ('completed', 'cancelled'):
            self.connection.execute(text("UPDATE rentals SET status=:status, completed_at=CASE WHEN :status='completed' THEN clock_timestamp() ELSE NULL END"), {'status': status})
            self.assertNotIn('Continue rental', self.client.get('/').text)
        self.connection.execute(text('DELETE FROM payments'))
        self.connection.execute(text("UPDATE rentals SET status='pending', created_at=CURRENT_TIMESTAMP-INTERVAL '20 minutes', overdue_terms_accepted_at=CURRENT_TIMESTAMP-INTERVAL '20 minutes', reserved_until=CURRENT_TIMESTAMP-INTERVAL '1 minute'"))
        self.assertNotIn('Continue rental', self.client.get('/').text)
        self.client.cookies.clear()
        self.assertNotIn('Continue rental', self.client.get('/').text)

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

    def test_open_door_allows_release_but_not_finish(self):
        self.report(door='open')
        page = self.client.get(f'/rentals/{self.rental_id}')
        self.assertIn('Open locker', page.text)
        self.assertRegex(page.text, r'<button(?=[^>]*id="open-locker-button")(?![^>]*\bdisabled\b)[^>]*>')
        self.assertRegex(page.text, r'<button(?=[^>]*id="finish-rental-button")(?=[^>]*\bdisabled\b)[^>]*>')
        self.assertEqual(self.finish().status_code, 409)
        self.assertEqual(self.open().status_code, 202)
        self.assertEqual(self.take().json()['command']['action'], 'open')

    def test_authentication_ownership_and_browser_header(self):
        self.client.cookies.clear()
        self.assertEqual(self.open().status_code, 401)
        self.client.cookies.set('paybox_session', 'session-2')
        self.assertEqual(self.open().status_code, 404)
        self.client.cookies.set('paybox_session', 'session-1')
        self.assertEqual(self.client.post(f'/api/rentals/{self.rental_id}/open').status_code, 403)
        self.assertEqual(
            self.client.post('/api/device/commands/next', json={'controller_id': self.controller}).status_code, 401)

    def test_unpaid_offline_open_and_legacy_report_cannot_unlock(self):
        self.connection.execute(text("UPDATE payments SET status='pending'"))
        self.assertEqual(self.open().status_code, 409)
        self.connection.execute(text("UPDATE payments SET status='paid'"))
        self.report(ready=False)
        self.assertEqual(self.open().status_code, 409)
        self.report()
        self.connection.execute(text("UPDATE lockers SET last_seen_at=CURRENT_TIMESTAMP-INTERVAL '1 minute'"))
        self.assertEqual(self.open().status_code, 409)
        self.client.post('/api/device/heartbeat', headers=self.device_headers, json={'door_state': 'closed'})
        self.assertEqual(self.open().status_code, 409)

    def test_expired_command_and_restarted_controller_do_not_open(self):
        self.assertEqual(self.open().status_code, 202)
        self.connection.execute(text(
            "UPDATE locker_commands SET created_at=created_at-INTERVAL '1 minute', expires_at=expires_at-INTERVAL '1 minute'"))
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
        self.connection.execute(text('''UPDATE rentals
                                        SET starts_at=:start,
                                            created_at=:start,
                                            overdue_terms_accepted_at=:start,
                                            reserved_until=:reserved,
                                            paid_until=:end'''),
                                {'start': start, 'reserved': start + timedelta(minutes=15),
                                 'end': rental_end(start, 2)})
        self.assertEqual(self.open().status_code, 409)

    def test_button_shown_only_for_ready_paid_owner(self):
        response = self.client.get(f'/rentals/{self.rental_id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="open-locker-button"', response.text)
        self.assertRegex(response.text, r'<button(?=[^>]*id="open-locker-button")(?![^>]*\bdisabled\b)[^>]*>')
        self.report(ready=False)
        self.assertRegex(self.client.get(f'/rentals/{self.rental_id}').text,
                         r'<button(?=[^>]*id="open-locker-button")(?=[^>]*\bdisabled\b)[^>]*>')

    def finish(self, confirmed=True):
        headers = {'X-PayBox-Request': '1'}
        if confirmed:
            headers['X-PayBox-Collect-All'] = 'yes'
        return self.client.post(f'/api/rentals/{self.rental_id}/finish', headers=headers)

    def acknowledge(self, command_id, controller=None):
        return self.client.post(f'/api/device/commands/{command_id}/completed',
                                headers=self.device_headers, json={'controller_id': controller or self.controller})

    def test_finish_requires_confirmation_owner_and_paid_access(self):
        self.assertEqual(self.finish(False).status_code, 422)
        self.client.cookies.clear()
        self.assertEqual(self.finish().status_code, 401)
        self.client.cookies.set('paybox_session', 'session-2')
        self.assertEqual(self.finish().status_code, 404)
        self.client.cookies.set('paybox_session', 'session-1')
        self.connection.execute(text("UPDATE payments SET status='pending'"))
        self.assertEqual(self.finish().status_code, 409)

    def test_collection_needs_dispatch_and_matching_controller(self):
        response = self.finish()
        self.assertEqual(response.status_code, 202, response.text)
        command_id = response.json()['command_id']
        self.assertEqual(self.acknowledge(command_id).status_code, 409)
        # Old controllers must never execute a collection as an ordinary opening.
        self.assertIsNone(self.take().json()['command'])
        result = self.client.post('/api/device/commands/next', headers=self.device_headers,
                                  json={'controller_id': self.controller, 'supports_finish': True}).json()['command']
        self.assertEqual(result['action'], 'finish')
        self.report(ready=False, door='open')
        self.report(ready=False, door='closed')
        self.assertEqual(self.connection.execute(text('SELECT status FROM rentals')).scalar_one(), 'active')
        self.assertEqual(self.acknowledge(command_id, str(uuid4())).status_code, 409)
        self.assertEqual(self.client.post(f'/api/device/commands/{command_id}/completed',
                                          json={'controller_id': self.controller}).status_code, 401)
        self.assertEqual(self.acknowledge(command_id).status_code, 200)
        completed = self.connection.execute(text('SELECT completed_at FROM rentals')).scalar_one()
        self.assertIsNotNone(completed)
        self.assertEqual(self.acknowledge(command_id).status_code, 200)
        self.assertEqual(self.connection.execute(text('SELECT completed_at FROM rentals')).scalar_one(), completed)
        with Session(bind=self.connection, join_transaction_mode='create_savepoint') as db:
            from lockers import read_lockers
            self.assertEqual(read_lockers(db, 1)[0]['status'], 'available')
        self.assertEqual(self.open().status_code, 409)
        page = self.client.get(f'/rentals/{self.rental_id}').text
        self.assertIn('Rental completed.', page)
        self.assertNotIn('id="finish-rental-button"', page)

    def test_ordinary_open_cannot_complete_rental(self):
        command_id = self.open().json()['command_id']
        self.take()
        self.assertEqual(self.acknowledge(command_id).status_code, 409)
        self.assertEqual(self.connection.execute(text('SELECT status FROM rentals')).scalar_one(), 'active')
