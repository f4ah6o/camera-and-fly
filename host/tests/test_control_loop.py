from __future__ import annotations

import threading
import time
import unittest

from host.control_loop import ControlIntent, ControlLoop, ControlScheduler, SchedulerState


class FakeTransport:
    def __init__(self):
        self.sets = []
        self.disarms = 0
        self.disarm_thread_ids = []
        self.fail_disarm = False

    def set_control(self, *args, **kwargs):
        self.sets.append((args, kwargs))
        return len(self.sets)

    def best_effort_disarm(self):
        self.disarms += 1
        self.disarm_thread_ids.append(threading.get_ident())
        if self.fail_disarm:
            raise RuntimeError("fake disarm failure")


class BlockingSetTransport(FakeTransport):
    def __init__(self):
        super().__init__()
        self.set_entered = threading.Event()
        self.release_set = threading.Event()

    def set_control(self, *args, **kwargs):
        self.set_entered.set()
        if not self.release_set.wait(2.0):
            raise RuntimeError("test failed to release SET")
        return super().set_control(*args, **kwargs)


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

    def test_201ms_scheduler_stall_faults_before_next_set(self):
        transport = FakeTransport()
        scheduler = ControlScheduler(transport, local_watchdog_seconds=0.2)
        scheduler.publish(self.intent(sequence=1, now=0.0, ttl=1.0, throttle=0.2))
        self.assertTrue(scheduler.tick(now=0.0).sent)
        scheduler.publish(self.intent(sequence=2, now=0.201, ttl=1.0, throttle=0.2))
        result = scheduler.tick(now=0.201)
        self.assertEqual(result.state, SchedulerState.FAULT)
        self.assertEqual(result.fault_reason, "local_watchdog_before_send")
        self.assertEqual(len(transport.sets), 1)
        self.assertEqual(transport.disarms, 1)

    def test_stopped_producer_cannot_keep_nonzero_intent_alive(self):
        transport = FakeTransport()
        scheduler = ControlScheduler(transport)
        scheduler.publish(self.intent(sequence=1, now=0.0, ttl=0.16, throttle=0.2))
        self.assertTrue(scheduler.tick(now=0.00).sent)
        self.assertTrue(scheduler.tick(now=0.05).sent)
        self.assertTrue(scheduler.tick(now=0.10).sent)
        self.assertTrue(scheduler.tick(now=0.15).sent)
        result = scheduler.tick(now=0.16)
        self.assertEqual(result.state, SchedulerState.FAULT)
        self.assertEqual(result.fault_reason, "intent_expired:1")
        self.assertEqual(len(transport.sets), 4)
        self.assertEqual(transport.disarms, 1)

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

    def test_normal_loop_stop_disarms_once_from_owner_and_is_bounded(self):
        transport = BlockingSetTransport()
        scheduler = ControlScheduler(transport)
        scheduler.publish(self.intent(now=time.monotonic(), ttl=1.0, throttle=0.2))
        loop = ControlLoop(scheduler)
        caller_thread_id = threading.get_ident()

        loop.start()
        self.assertTrue(transport.set_entered.wait(1.0))

        self.assertFalse(loop.stop(timeout=0.01))
        self.assertEqual(scheduler.state, SchedulerState.READY)
        self.assertEqual(transport.disarms, 0)

        transport.release_set.set()
        self.assertTrue(loop.stop(timeout=1.0))
        self.assertEqual(scheduler.state, SchedulerState.STOPPED)
        self.assertEqual(len(transport.sets), 1)
        self.assertEqual(transport.disarms, 1)
        self.assertEqual(len(transport.disarm_thread_ids), 1)
        self.assertNotEqual(transport.disarm_thread_ids[0], caller_thread_id)

        self.assertTrue(loop.stop(timeout=0.0))
        self.assertEqual(transport.disarms, 1)
        self.assertEqual(len(transport.sets), 1)

    def test_stop_waits_for_disarm_attempt_even_when_transport_raises(self):
        transport = FakeTransport()
        transport.fail_disarm = True
        scheduler = ControlScheduler(transport)
        scheduler.publish(self.intent(now=0.0, ttl=1.0, throttle=0.2))
        self.assertTrue(scheduler.tick(now=0.0).sent)

        scheduler.stop()

        self.assertEqual(scheduler.state, SchedulerState.STOPPED)
        self.assertEqual(transport.disarms, 1)

    def test_fault_then_stop_does_not_double_disarm(self):
        transport = FakeTransport()
        scheduler = ControlScheduler(transport)

        self.assertEqual(scheduler.tick(now=0.0).state, SchedulerState.FAULT)
        self.assertEqual(transport.disarms, 1)

        scheduler.stop()

        self.assertEqual(scheduler.state, SchedulerState.STOPPED)
        self.assertEqual(transport.disarms, 1)


if __name__ == "__main__":
    unittest.main()
