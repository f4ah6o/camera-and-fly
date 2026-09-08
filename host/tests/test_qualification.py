from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from host.qualification import (
    QualificationCriteria,
    QualificationError,
    evaluate_records,
    load_criteria,
    load_jsonl,
)


def sample(
    run_id: str,
    *,
    target=(0.0, 0.0, 0.4),
    observed=(0.0, 0.0, 0.4),
    observation_valid=True,
    telemetry_valid=True,
    saturated=False,
    observation_age=0.05,
    intent_age=0.02,
    command_period=0.05,
    fault=None,
):
    return {
        "schema_version": 1,
        "run_id": run_id,
        "kind": "sample",
        "target_position_m": {"x": target[0], "y": target[1], "z": target[2]},
        "observed_position_m": {"x": observed[0], "y": observed[1], "z": observed[2]},
        "observation_valid": observation_valid,
        "telemetry_valid": telemetry_valid,
        "observation_age_seconds": observation_age,
        "intent_age_seconds": intent_age,
        "command_period_seconds": command_period,
        "saturated": saturated,
        "fault": fault,
    }


def outcome(run_id: str, value: str):
    return {"schema_version": 1, "run_id": run_id, "kind": "outcome", "outcome": value}


class QualificationTests(unittest.TestCase):
    def test_metrics_keep_aborted_and_failed_runs_in_denominator(self):
        records = [
            sample("r1", observed=(0.1, 0.0, 0.45)),
            outcome("r1", "completed"),
            sample("r2", observed=(0.2, 0.0, 0.5), saturated=True, fault="operator_abort"),
            outcome("r2", "aborted"),
            sample("r3", observed=(0.3, 0.0, 0.6)),
            outcome("r3", "failed"),
        ]
        summary = evaluate_records(records)
        self.assertEqual(summary.runs_total, 3)
        self.assertEqual(summary.runs_completed, 1)
        self.assertEqual(summary.runs_aborted, 1)
        self.assertEqual(summary.runs_failed, 1)
        self.assertAlmostEqual(summary.success_rate, 1 / 3)
        self.assertAlmostEqual(summary.horizontal_error_max_m, 0.3)
        self.assertAlmostEqual(summary.altitude_error_max_m, 0.2)
        self.assertAlmostEqual(summary.saturation_fraction, 1 / 3)
        self.assertEqual(summary.fault_sample_count, 1)
        self.assertIsNone(summary.qualified)

    def test_explicit_criteria_can_qualify_five_consecutive_runs(self):
        records = []
        for index in range(5):
            run_id = f"r{index}"
            records.extend(
                [
                    sample(run_id, observed=(0.08, 0.06, 0.45)),
                    sample(run_id, observed=(0.10, 0.00, 0.46)),
                    outcome(run_id, "completed"),
                ]
            )
        criteria = QualificationCriteria(
            horizontal_error_p95_m=0.20,
            horizontal_error_max_m=0.40,
            altitude_error_p95_m=0.10,
            required_consecutive_completions=5,
            minimum_success_rate=1.0,
        )
        summary = evaluate_records(records, criteria=criteria)
        self.assertTrue(summary.qualified)
        self.assertEqual(summary.longest_consecutive_completions, 5)
        self.assertTrue(all(summary.criteria_checks.values()))

    def test_invalid_sample_and_missing_outcome_fail_explicit_criteria(self):
        records = [
            sample("r1", observation_valid=False),
            outcome("r1", "completed"),
            sample("r2"),
        ]
        criteria = QualificationCriteria(
            horizontal_error_p95_m=1.0,
            horizontal_error_max_m=1.0,
            altitude_error_p95_m=1.0,
            required_consecutive_completions=1,
        )
        summary = evaluate_records(records, criteria=criteria)
        self.assertFalse(summary.qualified)
        self.assertEqual(summary.invalid_sample_count, 1)
        self.assertEqual(summary.runs_missing_outcome, 1)
        self.assertFalse(summary.criteria_checks["all_samples_valid"])
        self.assertFalse(summary.criteria_checks["all_runs_have_outcomes"])

    def test_schema_rejects_nonfinite_and_truthy_integer_boolean(self):
        with self.assertRaises(QualificationError):
            evaluate_records([sample("r1", observation_age=float("nan"))])
        invalid_bool = sample("r1")
        invalid_bool["telemetry_valid"] = 1
        with self.assertRaises(QualificationError):
            evaluate_records([invalid_bool])

    def test_jsonl_and_criteria_loaders_are_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "qualification.jsonl"
            log.write_text(json.dumps(sample("r1")) + "\n" + json.dumps(outcome("r1", "completed")) + "\n")
            self.assertEqual(len(load_jsonl(log)), 2)

            criteria_path = root / "criteria.json"
            criteria_path.write_text(
                json.dumps(
                    {
                        "horizontal_error_p95_m": 0.2,
                        "horizontal_error_max_m": 0.4,
                        "altitude_error_p95_m": 0.1,
                        "required_consecutive_completions": 5,
                    }
                )
            )
            self.assertEqual(load_criteria(criteria_path).required_consecutive_completions, 5)

            criteria_path.write_text(json.dumps({"horizontal_error_p95_m": 0.2, "unexpected": True}))
            with self.assertRaises(QualificationError):
                load_criteria(criteria_path)


if __name__ == "__main__":
    unittest.main()
