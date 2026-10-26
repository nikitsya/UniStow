import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('run_locker',
                                              Path(__file__).resolve().parents[2] / 'hardware/run_locker.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class LockerCycleTests(unittest.TestCase):
    def ready(self):
        cycle = controller.LockerCycle()
        self.assertIsNone(cycle.tick(True, 0))
        self.assertEqual(cycle.tick(True, 1), 'close')
        self.assertEqual(cycle.tick(True, 3), 'secured')
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
        self.assertEqual(cycle.phase, 'confirm_close')
        self.assertEqual(cycle.tick(True, 27), 'secured')

    def test_startup_open_door_does_not_move_latch(self):
        cycle = controller.LockerCycle()
        for now in range(10):
            self.assertIsNone(cycle.tick(False, now))
        self.assertIsNone(cycle.tick(True, 10))
        self.assertEqual(cycle.tick(True, 11), 'close')

    def test_owner_can_release_latch_with_open_door(self):
        cycle = self.ready()
        self.assertEqual(cycle.tick(False, 4, True), 'open')
        self.assertEqual(cycle.phase, 'await_close')

    def test_early_closing_releases_and_waits_again(self):
        cycle = self.ready()
        cycle.tick(True, 4, True)
        cycle.tick(False, 5)
        cycle.tick(True, 6)
        self.assertEqual(cycle.tick(True, 7), 'close')
        self.assertEqual(cycle.tick(False, 7.5), 'open')
        self.assertEqual(cycle.phase, 'await_close')
        cycle.tick(True, 8)
        self.assertEqual(cycle.tick(True, 9), 'close')
        self.assertIsNone(cycle.tick(True, 10))
        self.assertEqual(cycle.tick(True, 11), 'secured')
        self.assertIsNone(cycle.tick(False, 12))

    def test_recovery_retries_are_bounded(self):
        cycle = self.ready()
        cycle.tick(True, 4, True)
        cycle.tick(False, 5)
        for attempt in range(4):
            now = 6 + attempt * 3
            cycle.tick(True, now)
            self.assertEqual(cycle.tick(True, now + 1), 'close')
            result = cycle.tick(False, now + 1.5)
            self.assertEqual(result, 'open' if attempt < 3 else None)
        self.assertEqual(cycle.phase, 'blocked')
        self.assertIsNone(cycle.tick(True, 30))
        self.assertEqual(cycle.tick(False, 31, True), 'open')


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
