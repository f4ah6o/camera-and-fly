from __future__ import annotations

import unittest

from host.integration import DryRunSession, FakeSafeTransport, IntegrationState
from host.replay import ReplayEvent


STATUS = (
    "CF1 STATUS claimed=1 armed=0 connected=1 mode=3 voltage=4.1 roll=0 pitch=0 yaw=0 "
    "altitude=0 range=0 altitude_m=0 altitude_valid=0 altitude_age_ms=4294967295 "
    "altitude_source=tof_imu range_mm=0 range_valid=0 range_age_ms=4294967295 "
    "range_source=tof_bottom imu_valid=0 imu_age_ms=4294967295 imu_source=bmi270 "
    "capabilities=telemetry_validity_v1 safe_test=1"
)


class IntegrationTests(unittest.TestCase):
    def test_replay_is_zero_control_and_camera_gap_does_not_stop_ticks(self):
        transport = FakeSafeTransport()
        session = DryRunSession(transport, tick_period=0.05, camera_max_age=0.2)
        events = [
            ReplayEvent(0.0, "status", {"line": STATUS}),
            ReplayEvent(0.0, "frame", {"frame_id": 1}),
            ReplayEvent(2.0, "frame", {"frame_id": 2}),
        ]
        summary = session.run(events)
        self.assertEqual(summary.arm_count, 0)
        self.assertGreater(len(session.commands), 10)
        self.assertTrue(all(command[:4] == (0.0, 0.0, 0.0, 0.0) for command in session.commands))
        self.assertGreater(summary.camera_gaps, 0)
        self.assertEqual(summary.fault_reasons, [])

    def test_invalid_status_latches_and_does_not_resume(self):
        transport = FakeSafeTransport()
        session = DryRunSession(transport)
        session.run(
            [
                ReplayEvent(0.0, "status", {"line": STATUS.replace("safe_test=1", "safe_test=0")}),
                ReplayEvent(0.1, "frame", {"frame_id": 1}),
            ]
        )
        self.assertEqual(session.state, IntegrationState.FAULT)
        self.assertIn("unsafe_status", session.fault_reasons)

    def test_serial_error_latches(self):
        session = DryRunSession(FakeSafeTransport())
        session.run([ReplayEvent(0.0, "serial_error", {"reason": "read_failed"})])
        self.assertEqual(session.state, IntegrationState.FAULT)
        self.assertIn("read_failed", session.fault_reasons)


if __name__ == "__main__":
    unittest.main()
