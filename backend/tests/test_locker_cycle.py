import importlib.util
from pathlib import Path
import unittest

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
