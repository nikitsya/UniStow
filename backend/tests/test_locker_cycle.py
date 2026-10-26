import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('run_locker', Path(__file__).resolve().parents[2] / 'hardware/run_locker.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class LockerCycleTests(unittest.TestCase):
    def ready(self):
        cycle = controller.LockerCycle()
        self.assertIsNone(cycle.tick(True, 0))
        self.assertEqual(cycle.tick(True, 1), 'close')
        return cycle

    def test_waits_for_open_then_continuously_closed_door(self):
        cycle = self.ready()
        self.assertEqual(cycle.tick(True, 2, True), 'open')
        self.assertIsNone(cycle.tick(True, 20))
        self.assertIsNone(cycle.tick(True, 21, True))
        self.assertIsNone(cycle.tick(False, 22))
        self.assertIsNone(cycle.tick(True, 23))
        self.assertIsNone(cycle.tick(False, 23.9))
        self.assertIsNone(cycle.tick(True, 24))
        self.assertIsNone(cycle.tick(True, 24.9))
        self.assertEqual(cycle.tick(True, 25), 'close')
        self.assertEqual(cycle.phase, 'idle')

    def test_startup_open_door_does_not_move_latch(self):
        cycle = controller.LockerCycle()
        for now in range(10):
            self.assertIsNone(cycle.tick(False, now, True))
        self.assertIsNone(cycle.tick(True, 10))
        self.assertEqual(cycle.tick(True, 11), 'close')

    def test_cannot_unlock_while_door_is_open(self):
        cycle = self.ready()
        self.assertIsNone(cycle.tick(False, 2, True))
        self.assertEqual(cycle.phase, 'await_close')


class CollectionDeliveryTests(unittest.TestCase):
    def test_failed_acknowledgement_is_retried_without_reopening(self):
        from unittest.mock import patch
        from urllib.error import URLError
        from threading import Event, Lock
        from queue import Queue
        import json

        collections = Queue()
        collections.put('collection-command')
        finishing = Event()
        finishing.set()
        commands = Queue()
        calls = []

        class Stop:
            rounds = 0

            def is_set(self):
                return self.rounds >= 2

            def wait(self, seconds):
                self.rounds += 1

        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return b'{}'

        class Opener:
            def open(self, request, timeout):
                calls.append((request.full_url, json.loads(request.data)))
                if len(calls) == 1:
                    raise URLError('Connection lost')
                return Reply()

        with patch.object(controller, 'build_opener', return_value=Opener()), patch('builtins.print'):
            controller.communicate('http://example.test', 'key',
                                   {'door_state': 'closed', 'ready': False}, Lock(), commands,
                                   collections, finishing, Stop())
        self.assertEqual(calls[0], calls[1])
        self.assertTrue(calls[0][0].endswith('/collection-command/completed'))
        self.assertFalse(finishing.is_set())
        self.assertTrue(commands.empty())
