from __future__ import annotations

import unittest

from host.control_loop import ControlIntent, ControlScheduler, SchedulerState


class FakeTransport:
    def __init__(self):
        self.sets = []
        self.disarms = 0

    def set_control(self, *args, **kwargs):
        self.sets.append((args, kwargs))
        return len(self.sets)

    def best_effort_disarm(self):
        self.disarms += 1


class ControlLoopTests(unittest.TestCase):
    def intent(self, sequence=1, now=0.0, ttl=0.2, throttle=0.0):
        return ControlIntent(sequence, now, now + ttl, 0, 0, 0, throttle)

    def test_expiry_is_checked_before_set(self):
        transport = FakeTransport()
        scheduler = ControlScheduler(transport)
        scheduler.publish(self.intent())
        self.assertTrue(scheduler.tick(now=0.199).sent)
        scheduler.publish(self.intent(sequence=2, now=0.199, ttl=0.2, throttle=0.1))
        result = scheduler.tick(now=0.400)
        self.assertEqual(result.state, SchedulerState.FAULT)
        self.assertEqual(transport.disarms, 1)
        self.assertEqual(len(transport.sets), 1)

    def test_reversed_intent_latches(self):
        transport = FakeTransport()
        scheduler = ControlScheduler(transport)
        scheduler.publish(self.intent(sequence=2))
        scheduler.tick(now=0.01)
        scheduler.publish(self.intent(sequence=1, now=0.01))
        result = scheduler.tick(now=0.02)
        self.assertEqual(result.fault_reason, "intent_sequence_reversed")

    def test_missing_intent_disarms_without_serial_write(self):
        transport = FakeTransport()
        scheduler = ControlScheduler(transport)
        result = scheduler.tick(now=0.0)
        self.assertEqual(result.state, SchedulerState.FAULT)
        self.assertEqual(transport.sets, [])
        self.assertEqual(transport.disarms, 1)


if __name__ == "__main__":
    unittest.main()
