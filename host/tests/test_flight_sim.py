from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from host.flight_sim import (
    FaultInjection,
    FlightDynamicsSimulator,
    SimCommand,
    SimConfig,
    SimulationError,
    default_commands,
    saturate,
    slew_towards,
)
from host.qualification import evaluate_records, load_jsonl


class FlightSimulationTests(unittest.TestCase):
    def test_saturation_and_slew_are_table_driven(self):
        for value, lower, upper, expected in ((2.0, -1.0, 1.0, 1.0), (-2.0, -1.0, 1.0, -1.0), (0.25, -1.0, 1.0, 0.25)):
            with self.subTest(value=value):
                self.assertEqual(saturate(value, lower, upper), expected)
        for current, target, rate, dt, expected in ((0.0, 1.0, 2.0, 0.1, 0.2), (1.0, 0.0, 2.0, 0.1, 0.8), (0.0, 1.0, 2.0, 1.0, 1.0)):
            with self.subTest(current=current, target=target):
                self.assertAlmostEqual(slew_towards(current, target, rate, dt), expected)

    def test_same_seed_and_input_produce_identical_qualification_jsonl(self):
        config = SimConfig(duration_seconds=0.5, dt_seconds=0.01)
        injection = FaultInjection(seed=7, observation_delay_seconds=0.03, observation_jitter_seconds=0.01)
        commands = [SimCommand(0.0, pitch_rad=0.1, altitude_m=0.3), SimCommand(0.25, roll_rad=0.1, altitude_m=0.3)]
        first = FlightDynamicsSimulator(config, injection, run_id="g1-fixture").run(commands)
        second = FlightDynamicsSimulator(config, injection, run_id="g1-fixture").run(commands)
        self.assertEqual(first.to_jsonl(), second.to_jsonl())
        self.assertTrue(all(record.get("simulation") is True for record in first.qualification_records))

    def test_delay_drop_reorder_producer_stop_and_expiry_are_visible(self):
        config = SimConfig(duration_seconds=0.6, dt_seconds=0.01, observation_max_age_seconds=0.05)
        injection = FaultInjection(
            seed=3,
            command_delay_seconds=0.3,
            observation_delay_seconds=0.2,
            command_drop_indices=frozenset({2}),
            observation_drop_indices=frozenset({2}),
            reorder_observations=True,
            producer_stop_at=0.25,
        )
        result = FlightDynamicsSimulator(config, injection, run_id="fault-fixture").run(
            [SimCommand(0.0, pitch_rad=0.2, altitude_m=0.4, ttl_seconds=0.1)]
        )
        self.assertTrue(result.faults)
        self.assertTrue(any("command_expired" in fault for fault in result.faults))
        self.assertTrue(any(record["kind"] == "sample" and not record["observation_valid"] for record in result.qualification_records))
        summary = evaluate_records(result.qualification_records)
        self.assertEqual(summary.schema_version, 1)
        self.assertGreater(summary.invalid_sample_count, 0)

    def test_jsonl_is_consumable_by_qualification_loader(self):
        result = FlightDynamicsSimulator(SimConfig(duration_seconds=0.2), run_id="load-fixture").run(default_commands())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "simulation.jsonl"
            result.write_jsonl(path)
            self.assertEqual(len(load_jsonl(path)), len(result.qualification_records))

    def test_invalid_commands_are_rejected(self):
        with self.assertRaises(SimulationError):
            FlightDynamicsSimulator().run([SimCommand(0.2), SimCommand(0.1)])


if __name__ == "__main__":
    unittest.main()
