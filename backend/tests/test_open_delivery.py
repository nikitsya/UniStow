import sys
import unittest
from pathlib import Path
from time import time
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from services.door_events import CHANNEL, COMMAND_CHANNEL, DoorEvents

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'hardware'))
from probe_ping import handle_open, open_test_ack


class OpenDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.payload = {'type': 'test_open', 'id': str(uuid4()), 'expires_at': 110}
        self.event = {'i': 'unistow-mac-reader', 'c': COMMAND_CHANNEL, 'd': self.payload}

    def test_accepts_each_live_test_once(self):
        seen = {}
        self.assertEqual(open_test_ack(self.event, seen, 100),
                         {'type': 'test_open_ack', 'id': self.payload['id']})
        self.assertIsNone(open_test_ack(self.event, seen, 101))
        self.assertIsNone(open_test_ack(self.event, {}, 110))

    def test_rejects_other_publishers_channels_and_physical_commands(self):
        for event in (dict(self.event, i='other'), dict(self.event, c=CHANNEL),
                      dict(self.event, d=dict(self.payload, type='open'))):
            self.assertIsNone(open_test_ack(event, {}, 100))

    def test_only_a_fresh_open_for_this_boot_reaches_the_servo_queue_once(self):
        controller = str(uuid4())
        data = dict(self.payload, type='open', controller_id=controller)
        queue = Mock()
        queue.put.side_effect = lambda item: item[3].set()
        seen = {}
        with patch('probe_ping.time', return_value=100):
            for event in (self.event, dict(self.event, d=dict(data, controller_id=str(uuid4()))),
                          dict(self.event, d=dict(data, expires_at=100)), dict(self.event, d=data, i='other')):
                self.assertIsNone(handle_open(event, seen, controller, queue))
            queue.put.assert_not_called()
            event = dict(self.event, d=data)
            self.assertEqual(handle_open(event, seen, controller, queue), {'type': 'open_ack', 'id': data['id']})
            self.assertIsNone(handle_open(event, seen, controller, queue))
            queue.put.assert_called_once()

    def test_matches_controller_acknowledgement(self):
        events = DoorEvents()
        client = Mock()
        publish = client.publish.return_value.channel.return_value

        def deliver(callback):
            payload = publish.message.call_args.args[0]
            ack = open_test_ack(dict(self.event, d=payload), {}, time())
            received = events.pending_tests[payload['id']]
            for publisher, command_id in [('other', payload['id']), ('unistow-locker-4', 'other')]:
                events.message(client, SimpleNamespace(channel=CHANNEL, publisher=publisher,
                                                       message=dict(ack, id=command_id)))
                self.assertFalse(received.is_set())
            events.message(client, SimpleNamespace(channel=CHANNEL, publisher='unistow-locker-4', message=ack))

        publish.message.return_value.should_store.return_value.pn_async.side_effect = deliver
        self.assertEqual(events.test_open(client, time() + 9)['status'], 'test_acknowledged')
        self.assertFalse(events.pending_tests)

    def test_door_reading_survives_small_clock_difference_and_expires(self):
        events = DoorEvents()
        event = SimpleNamespace(channel=CHANNEL, publisher='unistow-locker-4',
                                message={'door_state': 'closed'}, timetoken=1_000_000_000)
        with patch('services.door_events.time', return_value=99.95):
            events.message(None, event)
            self.assertEqual(events.latest(None), 'closed')
        with patch('services.door_events.time', return_value=115):
            self.assertIsNone(events.latest(None))
