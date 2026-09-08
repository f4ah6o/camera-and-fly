from __future__ import annotations

import threading
import time
import unittest

from host.atomcam import AtomCamError, AtomCamFrame
from host.camera_worker import CameraWorker, LatestFrameSlot


class CameraWorkerTests(unittest.TestCase):
    def test_latest_slot_reports_age_and_error_without_queueing(self):
        slot = LatestFrameSlot(monotonic_clock=lambda: 1.0)
        frame = AtomCamFrame(
            captured_at=1.0,
            jpeg=b"\xff\xd8x\xff\xd9",
            content_type="image/jpeg",
            frame_id=7,
            received_monotonic=0.5,
        )
        slot.publish(frame)
        current = slot.snapshot(now=1.0, max_age=0.6)
        self.assertTrue(current.valid)
        self.assertEqual(current.frame.frame_id, 7)
        self.assertEqual(current.age_seconds, 0.5)

        slot.publish_error("temporary camera failure")
        stale = slot.snapshot(now=1.2, max_age=0.6)
        self.assertFalse(stale.valid)
        self.assertEqual(stale.error, "temporary camera failure")
        with self.assertRaises(ValueError):
            slot.snapshot(now=float("inf"))

    def test_worker_retries_with_bounded_backoff_and_stops(self):
        frame = AtomCamFrame(
            captured_at=time.time(),
            jpeg=b"\xff\xd8x\xff\xd9",
            content_type="image/jpeg",
            frame_id=1,
            received_monotonic=time.monotonic(),
        )
        source = _FakeSource(frame)
        worker = CameraWorker(
            source,
            retry_initial_seconds=0.05,
            retry_max_seconds=0.1,
        )
        worker.start()
        self.assertTrue(source.error_seen.wait(1.0))
        worker.stop(timeout=1.0)
        self.assertGreaterEqual(worker.requests, 2)
        self.assertGreaterEqual(worker.errors, 1)
        self.assertEqual(worker.snapshot(max_age=60.0).frame.frame_id, 1)


class _FakeSource:
    def __init__(self, frame: AtomCamFrame) -> None:
        self.frame = frame
        self.calls = 0
        self.error_seen = threading.Event()

    def snapshot(self) -> AtomCamFrame:
        self.calls += 1
        if self.calls == 1:
            return self.frame
        self.error_seen.set()
        raise AtomCamError("temporary failure")


if __name__ == "__main__":
    unittest.main()
