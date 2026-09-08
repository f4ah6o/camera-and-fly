from __future__ import annotations

import threading
import time
import unittest

from host.camera_stream import (
    DecodedFrame,
    DecoderDisconnected,
    DecoderWorker,
    FFmpegDecoderBackend,
    LatestDecodedFrameSlot,
    StreamEvent,
    probe_events,
    synthetic_events,
)


def frame(frame_id: int, received: float, complete: float | None = None, generation: int = 1) -> DecodedFrame:
    complete = received if complete is None else complete
    return DecodedFrame(frame_id, bytes([frame_id]), 1, 1, "gray", received, complete, decoder_generation=generation)


class CameraStreamTests(unittest.TestCase):
    def test_ffmpeg_descriptor_records_missing_runtime_without_guessing(self):
        descriptor = FFmpegDecoderBackend.descriptor_for("definitely-missing-ffmpeg")
        self.assertEqual(descriptor.name, "ffmpeg-cli")
        self.assertEqual(descriptor.version, "unavailable")
        self.assertEqual(descriptor.install, "brew install ffmpeg")
        self.assertIn("ffmpeg.org", descriptor.source)

    def test_latest_slot_overwrites_burst_without_catch_up(self):
        slot = LatestDecodedFrameSlot(monotonic_clock=lambda: 0.0)
        slot.publish(frame(1, 0.0))
        slot.publish(frame(2, 0.01))
        slot.publish(frame(3, 0.01))
        snapshot = slot.snapshot(now=0.01, max_age=0.5)
        self.assertTrue(snapshot.valid)
        self.assertEqual(snapshot.frame.frame_id, 3)
        self.assertEqual(snapshot.published, 3)
        self.assertEqual(snapshot.overwritten, 2)

    def test_disconnect_invalidates_recent_frame_and_reconnect_requires_new_frame(self):
        slot = LatestDecodedFrameSlot(monotonic_clock=lambda: 0.0)
        slot.publish(frame(1, 0.0))
        slot.mark_disconnected("socket closed")
        self.assertFalse(slot.snapshot(now=0.01, max_age=1.0).valid)
        slot.begin_generation(2)
        self.assertFalse(slot.snapshot(now=0.01, max_age=1.0).valid)
        slot.publish(frame(1, 0.02, generation=2))
        snapshot = slot.snapshot(now=0.02, max_age=1.0)
        self.assertTrue(snapshot.valid)
        self.assertEqual(snapshot.generation, 2)

    def test_probe_fixture_is_deterministic_and_records_timestamp_separation(self):
        events = synthetic_events(count=5, burst=2, period=0.1)
        first = probe_events(events)
        second = probe_events(events)
        self.assertEqual(first, second)
        self.assertEqual(first.frames_received, 5)
        self.assertEqual(first.slot_overwrites, 4)
        self.assertFalse(first.source_pts_available)
        self.assertEqual(first.decision, "measurement-only")

        events = [
            StreamEvent(0.0, "frame", frame(1, 0.0)),
            StreamEvent(0.1, "disconnect", error="eof"),
            StreamEvent(0.2, "reconnect"),
            StreamEvent(0.3, "frame", frame(1, 0.3, generation=2)),
        ]
        report = probe_events(events)
        self.assertEqual(report.disconnects, 1)
        self.assertEqual(report.reconnects, 1)
        self.assertEqual(report.frames_received, 2)

    def test_worker_reconnects_and_consumer_does_not_call_backend(self):
        ready = threading.Event()
        calls: list[int] = []

        class Backend:
            name = "fixture"

            def __init__(self, generation: int) -> None:
                self.generation = generation

            def decode(self, stop: threading.Event, generation: int):
                calls.append(generation)
                if generation == 1:
                    yield frame(1, time.monotonic(), generation=generation)
                    raise DecoderDisconnected("fixture disconnect")
                yield frame(2, time.monotonic(), generation=generation)
                ready.set()
                stop.wait(0.01)

            def close(self) -> None:
                pass

        worker = DecoderWorker(lambda generation: Backend(generation), retry_initial_seconds=0.001, retry_max_seconds=0.002)
        worker.start()
        self.assertTrue(ready.wait(1.0))
        snapshot = worker.snapshot(max_age=1.0)
        worker.stop(timeout=1.0)
        self.assertTrue(snapshot.valid)
        self.assertEqual(snapshot.frame.decoder_generation, 2)
        self.assertGreaterEqual(worker.disconnects, 1)
        self.assertEqual(calls[:2], [1, 2])


if __name__ == "__main__":
    unittest.main()
