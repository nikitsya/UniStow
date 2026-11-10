import json
import sys
import unittest
from pathlib import Path
from time import time
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException
from services.door_events import COMMANDS, EVENTS, DoorEvents

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'hardware'))
from locker_mqtt import acknowledge


def packet(data, topic=EVENTS, retain=False):
    return SimpleNamespace(topic=topic, retain=retain, payload=json.dumps(data).encode())


class OpenDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.controller = str(uuid4())
        self.command = {'type': 'open', 'id': str(uuid4()), 'controller_id': self.controller,
                        'expires_at': time() + 9}
        self.queue = Mock()
        self.queue.put.side_effect = lambda item: item[3].set()

    def test_physical_command_must_be_fresh_unique_and_addressed_to_this_boot(self):
        seen = {}
        for invalid in [dict(self.command, expires_at=time()), dict(self.command, controller_id=str(uuid4())),
                        dict(self.command, id='invalid'), dict(self.command, expires_at=time() + 11)]:
            self.assertIsNone(acknowledge(invalid, seen, 'physical', self.controller, self.queue))
        self.assertIsNone(acknowledge(self.command, seen, 'test', self.controller, self.queue))
        self.queue.put.assert_not_called()
        self.assertEqual(acknowledge(self.command, seen, 'physical', self.controller, self.queue),
                         {'id': self.command['id'], 'type': 'open_ack'})
        self.assertIsNone(acknowledge(self.command, seen, 'physical', self.controller, self.queue))
        self.queue.put.assert_called_once()

    def test_matches_only_the_expected_live_acknowledgement(self):
        events, client = DoorEvents(), Mock()

        def deliver(topic, payload, qos, retain):
            self.assertEqual((topic, qos, retain), (COMMANDS, 0, False))
            ack = acknowledge(json.loads(payload), {}, 'test', None, self.queue)
            received = events.pending[('test_open_ack', ack['id'])]
            for event in [packet(ack, retain=True), packet(ack, topic=COMMANDS),
                          packet(dict(ack, type='open_ack')), packet(dict(ack, id=str(uuid4())))]:
                events.message(client, None, event)
                self.assertFalse(received.is_set())
            events.message(client, None, packet(ack))
            return SimpleNamespace(rc=0)

        client.publish.side_effect = deliver
        self.assertEqual(events.test_open(client, time() + 9)['status'], 'test_acknowledged')
        self.queue.put.assert_not_called()
        self.assertFalse(events.pending)

    def test_failed_publish_cleans_up_without_redelivery(self):
        events, client = DoorEvents(), Mock()
        client.publish.return_value.rc = 4
        with self.assertRaises(HTTPException) as caught:
            events.test_open(client, time() + 9)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertFalse(events.pending)
        client.publish.assert_called_once()

    def test_stale_retained_and_out_of_order_readings_cannot_enable_opening(self):
        events = DoorEvents()
        reading = dict(door_state='closed', ready=True, controller_id=self.controller, observed_at=100)
        with patch('services.door_events.time', return_value=99.95):
            events.message(None, None, packet(reading, retain=True))
            self.assertIsNone(events.ready_controller())
            events.message(None, None, packet(reading))
            events.message(None, None, packet(dict(reading, ready=False, observed_at=99)))
            self.assertEqual(events.latest(None), 'closed')
            self.assertEqual(events.ready_controller(), self.controller)
        with patch('services.door_events.time', return_value=115):
            self.assertIsNone(events.ready_controller())
            self.assertIsNone(events.latest(None))
