from __future__ import annotations

import unittest

from host.vision import ObservationSequenceGate, PoseObservation, VisionGateConfig, evaluate_observation


def observation(**changes):
    values = dict(
        sequence=1,
        received_monotonic=10.0,
        source_timestamp_monotonic=9.95,
        x_m=0.1,
        y_m=-0.1,
        z_m=-0.3,
        yaw_rad=0.2,
        marker_count=2,
        reprojection_error_px=1.0,
        confidence=0.9,
    )
    values.update(changes)
    return PoseObservation(**values)


class VisionContractTests(unittest.TestCase):
    def test_valid_observation_uses_frd_world_and_positive_height(self):
        item = observation()
        decision = evaluate_observation(item, now_monotonic=10.1)
        self.assertTrue(decision.valid)
        self.assertAlmostEqual(item.height_above_world_origin_m, 0.3)

    def test_unknown_capture_time_is_fail_closed(self):
        item = observation(source_timestamp_monotonic=None)
        decision = evaluate_observation(item, now_monotonic=10.1)
        self.assertFalse(decision.valid)
        self.assertIn("source_timestamp_unknown", decision.reasons)

    def test_stale_outlier_and_low_quality_are_rejected(self):
        item = observation(
            received_monotonic=9.0,
            source_timestamp_monotonic=8.9,
            x_m=3.0,
            z_m=0.1,
            marker_count=0,
            reprojection_error_px=8.0,
            confidence=0.1,
        )
        decision = evaluate_observation(item, now_monotonic=10.0)
        self.assertFalse(decision.valid)
        self.assertIn("receive_stale", decision.reasons)
        self.assertIn("source_stale", decision.reasons)
        self.assertIn("outside_xy_bounds", decision.reasons)
        self.assertIn("outside_height_bounds", decision.reasons)
        self.assertIn("marker_count_low", decision.reasons)
        self.assertIn("reprojection_error_high", decision.reasons)
        self.assertIn("confidence_low", decision.reasons)

    def test_sequence_gate_rejects_duplicate_and_reorder(self):
        gate = ObservationSequenceGate().accept(observation(sequence=4))
        with self.assertRaises(ValueError):
            gate.accept(observation(sequence=4))
        with self.assertRaises(ValueError):
            gate.accept(observation(sequence=3))
        self.assertEqual(gate.accept(observation(sequence=5)).last_sequence, 5)

    def test_gate_config_rejects_invalid_bounds(self):
        with self.assertRaises(ValueError):
            VisionGateConfig(min_height_m=1.0, max_height_m=1.0)


if __name__ == "__main__":
    unittest.main()
