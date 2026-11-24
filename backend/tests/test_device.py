from unittest.mock import patch

from sqlalchemy import text

import test_registration


class DeviceTests(test_registration.RegistrationTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.engine.begin() as connection:
            await connection.execute(text("""
                                          CREATE TABLE lockers
                                          (
                                              number        INTEGER PRIMARY KEY,
                                              door_state    TEXT    DEFAULT 'unknown',
                                              last_seen_at  TIMESTAMP,
                                              is_enabled    BOOLEAN DEFAULT FALSE,
                                              device_ready  BOOLEAN DEFAULT FALSE,
                                              controller_id TEXT
                                          )
                                          """))
            # The SQLite fixture above intentionally differs from the PostgreSQL schema.
            # noinspection SqlInsertValues
            await connection.execute(text('INSERT INTO lockers (number) VALUES (4), (5)'))
        key = patch.dict('os.environ', {'UNISTOW_DEVICE_KEY': 'test-device-key'})
        key.start()
        self.addCleanup(key.stop)

    async def report(self, state='closed', key='test-device-key', **extra):
        return await self.client.post('/api/device/heartbeat',
                                      json={'door_state': state, **extra},
                                      headers={'Authorization': f'Bearer {key}'})

    async def test_updates_only_locker_four_without_enabling_rentals(self):
        for state in ['open', 'closed']:
            response = await self.report(state)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'locker_number': 4, 'door_state': state})
            async with self.engine.connect() as connection:
                rows = (await connection.execute(text('SELECT * FROM lockers ORDER BY number'))).mappings().all()
            self.assertEqual(rows[0]['door_state'], state)
            self.assertIsNotNone(rows[0]['last_seen_at'])
            self.assertFalse(rows[0]['is_enabled'])
            self.assertEqual(rows[1]['door_state'], 'unknown')
            self.assertIsNone(rows[1]['last_seen_at'])

    async def test_rejects_wrong_or_missing_key(self):
        self.assertEqual((await self.report(key='wrong')).status_code, 401)
        response = await self.client.post('/api/device/heartbeat', json={'door_state': 'open'})
        self.assertEqual(response.status_code, 401)
        async with self.engine.connect() as connection:
            self.assertEqual((await connection.execute(text(
                'SELECT count(*) FROM lockers WHERE last_seen_at IS NOT NULL'))).scalar_one(), 0)

    async def test_unconfigured_key_and_invalid_reports(self):
        self.assertEqual((await self.report('locked')).status_code, 422)
        self.assertEqual((await self.report(locker_number=5)).status_code, 422)
        with patch.dict('os.environ', {'UNISTOW_DEVICE_KEY': ''}):
            self.assertEqual((await self.report(key='')).status_code, 503)

    async def test_missing_locker_is_not_created(self):
        async with self.engine.begin() as connection:
            await connection.execute(text('DELETE FROM lockers WHERE number = 4'))
        self.assertEqual((await self.report()).status_code, 404)
