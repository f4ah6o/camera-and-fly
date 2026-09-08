from __future__ import annotations

import unittest

from host.atomcam import AtomCamFrame
from host.camera_probe import jpeg_dimensions, run_probe


JPEG_640X480 = (
    b"\xff\xd8"
    b"\xff\xc0\x00\x07\x08\x01\xe0\x02\x80"
    b"\xff\xd9"
)


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def wall(self) -> float:
        return 1_700_000_000.0 + self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class _Source:
    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.frame_id = 0

    def snapshot(self) -> AtomCamFrame:
        started = self.clock.monotonic()
        self.clock.value += 0.05
        self.frame_id += 1
        return AtomCamFrame(
            captured_at=self.clock.wall(),
            jpeg=JPEG_640X480,
            content_type="image/jpeg",
            frame_id=self.frame_id,
            request_started_monotonic=started,
            received_monotonic=self.clock.monotonic(),
        )


class CameraProbeTests(unittest.TestCase):
    def test_jpeg_dimensions_without_pixel_decoder(self):
        self.assertEqual(jpeg_dimensions(JPEG_640X480), (640, 480))
        self.assertIsNone(jpeg_dimensions(b"not-jpeg"))

    def test_report_records_resolution_condition_and_decision(self):
        clock = _Clock()
        report = run_probe(
            _Source(clock),
            duration=0.25,
            fps=10.0,
            condition="static-fixture",
            lighting="indoor-unmeasured",
            decision="measurement-only",
            monotonic_clock=clock.monotonic,
            wall_clock=clock.wall,
            sleep=clock.sleep,
        )
        self.assertEqual(report.schema_version, 2)
        self.assertEqual(report.resolutions, ["640x480"])
        self.assertEqual(report.condition, "static-fixture")
        self.assertEqual(report.lighting, "indoor-unmeasured")
        self.assertEqual(report.decision, "measurement-only")
        self.assertEqual(report.received_frames, 3)
        self.assertEqual(report.failed_requests, 0)
        self.assertEqual(report.loss_fraction, 0.0)
        self.assertFalse(report.capture_timestamp_available)


if __name__ == "__main__":
    unittest.main()
