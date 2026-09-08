#!/usr/bin/env python3
"""Deterministic flight-qualification log evaluator.

This module evaluates already-recorded qualification logs.  It does not open
camera, serial, radio, or flight devices and it never supplies default flight
limits.  A pass/fail decision is produced only when explicit criteria are
provided by the caller.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import statistics
from typing import Iterable, Mapping, Sequence


class QualificationError(ValueError):
    """Raised when a qualification record or criteria set is invalid."""


@dataclass(frozen=True)
class QualificationCriteria:
    horizontal_error_p95_m: float
    horizontal_error_max_m: float
    altitude_error_p95_m: float
    required_consecutive_completions: int
    minimum_success_rate: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "horizontal_error_p95_m",
            "horizontal_error_max_m",
            "altitude_error_p95_m",
            "minimum_success_rate",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise QualificationError(f"{name} must be finite")
        if self.horizontal_error_p95_m < 0 or self.horizontal_error_max_m < 0 or self.altitude_error_p95_m < 0:
            raise QualificationError("error limits must be non-negative")
        if not 0.0 <= self.minimum_success_rate <= 1.0:
            raise QualificationError("minimum_success_rate must be between 0 and 1")
        if type(self.required_consecutive_completions) is not int or self.required_consecutive_completions < 1:
            raise QualificationError("required_consecutive_completions must be a positive integer")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "QualificationCriteria":
        allowed = {
            "horizontal_error_p95_m",
            "horizontal_error_max_m",
            "altitude_error_p95_m",
            "required_consecutive_completions",
            "minimum_success_rate",
        }
        unknown = set(value) - allowed
        if unknown:
            raise QualificationError(f"unknown criteria fields: {sorted(unknown)}")
        required = allowed - {"minimum_success_rate"}
        missing = required - set(value)
        if missing:
            raise QualificationError(f"missing criteria fields: {sorted(missing)}")
        return cls(**dict(value))  # type: ignore[arg-type]


@dataclass(frozen=True)
class QualificationSummary:
    schema_version: int
    runs_total: int
    runs_completed: int
    runs_aborted: int
    runs_failed: int
    runs_missing_outcome: int
    success_rate: float | None
    longest_consecutive_completions: int
    samples_total: int
    valid_position_samples: int
    invalid_sample_count: int
    horizontal_error_p95_m: float | None
    horizontal_error_max_m: float | None
    altitude_error_p95_m: float | None
    altitude_error_max_m: float | None
    observation_age_max_seconds: float | None
    intent_age_max_seconds: float | None
    command_period_max_seconds: float | None
    saturation_fraction: float | None
    fault_sample_count: int
    criteria: dict[str, object] | None
    criteria_checks: dict[str, bool] | None
    qualified: bool | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _finite_number(value: object, *, field: str, non_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QualificationError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise QualificationError(f"{field} must be finite")
    if non_negative and result < 0:
        raise QualificationError(f"{field} must be non-negative")
    return result


def _strict_bool(value: object, *, field: str) -> bool:
    if type(value) is not bool:
        raise QualificationError(f"{field} must be a boolean")
    return value


def _position(record: Mapping[str, object], field: str) -> tuple[float, float, float]:
    raw = record.get(field)
    if not isinstance(raw, Mapping):
        raise QualificationError(f"{field} must be an object")
    if set(raw) != {"x", "y", "z"}:
        raise QualificationError(f"{field} must contain exactly x, y, z")
    return (
        _finite_number(raw["x"], field=f"{field}.x"),
        _finite_number(raw["y"], field=f"{field}.y"),
        _finite_number(raw["z"], field=f"{field}.z"),
    )


def _p95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=20, method="inclusive")[18]


def _required_string(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise QualificationError(f"{field} must be a non-empty string")
    return value


def evaluate_records(
    records: Iterable[Mapping[str, object]],
    *,
    criteria: QualificationCriteria | None = None,
) -> QualificationSummary:
    """Evaluate schema-v1 sample/outcome records.

    Samples with invalid observation or telemetry flags remain part of the
    dataset as invalid samples.  They are not silently dropped for a passing
    qualification decision.  Every run, including aborted and failed runs,
    remains in the success-rate denominator.
    """

    run_order: list[str] = []
    outcomes: dict[str, str] = {}
    horizontal_errors: list[float] = []
    altitude_errors: list[float] = []
    observation_ages: list[float] = []
    intent_ages: list[float] = []
    command_periods: list[float] = []
    samples_total = 0
    valid_position_samples = 0
    invalid_sample_count = 0
    saturated_count = 0
    fault_sample_count = 0

    def see_run(run_id: str) -> None:
        if run_id not in run_order:
            run_order.append(run_id)

    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise QualificationError(f"record {index} must be an object")
        if record.get("schema_version") != 1:
            raise QualificationError(f"record {index} has unsupported schema_version")
        run_id = _required_string(record, "run_id")
        see_run(run_id)
        kind = _required_string(record, "kind")

        if kind == "outcome":
            outcome = _required_string(record, "outcome")
            if outcome not in {"completed", "aborted", "failed"}:
                raise QualificationError(f"record {index} has invalid outcome")
            if run_id in outcomes:
                raise QualificationError(f"run {run_id!r} has more than one outcome")
            outcomes[run_id] = outcome
            continue

        if kind != "sample":
            raise QualificationError(f"record {index} has invalid kind")

        samples_total += 1
        observation_valid = _strict_bool(record.get("observation_valid"), field="observation_valid")
        telemetry_valid = _strict_bool(record.get("telemetry_valid"), field="telemetry_valid")
        saturated = _strict_bool(record.get("saturated"), field="saturated")
        saturated_count += int(saturated)

        observation_age = _finite_number(
            record.get("observation_age_seconds"), field="observation_age_seconds", non_negative=True
        )
        intent_age = _finite_number(record.get("intent_age_seconds"), field="intent_age_seconds", non_negative=True)
        command_period = _finite_number(
            record.get("command_period_seconds"), field="command_period_seconds", non_negative=True
        )
        observation_ages.append(observation_age)
        intent_ages.append(intent_age)
        command_periods.append(command_period)

        fault = record.get("fault")
        if fault is not None and (not isinstance(fault, str) or not fault):
            raise QualificationError("fault must be null or a non-empty string")
        fault_sample_count += int(fault is not None)

        target = _position(record, "target_position_m")
        observed = _position(record, "observed_position_m")
        if not observation_valid or not telemetry_valid:
            invalid_sample_count += 1
            continue

        dx = observed[0] - target[0]
        dy = observed[1] - target[1]
        dz = observed[2] - target[2]
        horizontal_errors.append(math.hypot(dx, dy))
        altitude_errors.append(abs(dz))
        valid_position_samples += 1

    completed = sum(outcomes.get(run_id) == "completed" for run_id in run_order)
    aborted = sum(outcomes.get(run_id) == "aborted" for run_id in run_order)
    failed = sum(outcomes.get(run_id) == "failed" for run_id in run_order)
    missing = sum(run_id not in outcomes for run_id in run_order)
    success_rate = completed / len(run_order) if run_order else None

    longest = 0
    current = 0
    for run_id in run_order:
        if outcomes.get(run_id) == "completed":
            current += 1
            longest = max(longest, current)
        else:
            current = 0

    horizontal_p95 = _p95(horizontal_errors)
    horizontal_max = max(horizontal_errors) if horizontal_errors else None
    altitude_p95 = _p95(altitude_errors)
    altitude_max = max(altitude_errors) if altitude_errors else None

    criteria_dict: dict[str, object] | None = None
    criteria_checks: dict[str, bool] | None = None
    qualified: bool | None = None
    if criteria is not None:
        criteria_dict = asdict(criteria)
        criteria_checks = {
            "has_runs": bool(run_order),
            "all_runs_have_outcomes": missing == 0,
            "all_samples_valid": invalid_sample_count == 0,
            "has_position_samples": valid_position_samples > 0,
            "horizontal_error_p95": horizontal_p95 is not None
            and horizontal_p95 <= criteria.horizontal_error_p95_m,
            "horizontal_error_max": horizontal_max is not None
            and horizontal_max <= criteria.horizontal_error_max_m,
            "altitude_error_p95": altitude_p95 is not None and altitude_p95 <= criteria.altitude_error_p95_m,
            "consecutive_completions": longest >= criteria.required_consecutive_completions,
            "success_rate": success_rate is not None and success_rate >= criteria.minimum_success_rate,
        }
        qualified = all(criteria_checks.values())

    return QualificationSummary(
        schema_version=1,
        runs_total=len(run_order),
        runs_completed=completed,
        runs_aborted=aborted,
        runs_failed=failed,
        runs_missing_outcome=missing,
        success_rate=success_rate,
        longest_consecutive_completions=longest,
        samples_total=samples_total,
        valid_position_samples=valid_position_samples,
        invalid_sample_count=invalid_sample_count,
        horizontal_error_p95_m=horizontal_p95,
        horizontal_error_max_m=horizontal_max,
        altitude_error_p95_m=altitude_p95,
        altitude_error_max_m=altitude_max,
        observation_age_max_seconds=max(observation_ages) if observation_ages else None,
        intent_age_max_seconds=max(intent_ages) if intent_ages else None,
        command_period_max_seconds=max(command_periods) if command_periods else None,
        saturation_fraction=(saturated_count / samples_total) if samples_total else None,
        fault_sample_count=fault_sample_count,
        criteria=criteria_dict,
        criteria_checks=criteria_checks,
        qualified=qualified,
    )


def load_jsonl(path: Path) -> list[Mapping[str, object]]:
    records: list[Mapping[str, object]] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise QualificationError(f"invalid JSON on line {line_number}: {exc.msg}") from exc
            if not isinstance(value, Mapping):
                raise QualificationError(f"line {line_number} must contain a JSON object")
            records.append(value)
    return records


def load_criteria(path: Path) -> QualificationCriteria:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QualificationError(f"invalid criteria JSON: {exc.msg}") from exc
    if not isinstance(value, Mapping):
        raise QualificationError("criteria must be a JSON object")
    return QualificationCriteria.from_mapping(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="evaluate flight qualification JSONL without opening hardware")
    parser.add_argument("input", type=Path, help="qualification JSONL")
    parser.add_argument("--criteria", type=Path, help="explicit JSON criteria; omitted means metrics only")
    parser.add_argument("--output", type=Path, help="write summary JSON instead of stdout only")
    args = parser.parse_args()

    try:
        summary = evaluate_records(
            load_jsonl(args.input),
            criteria=load_criteria(args.criteria) if args.criteria is not None else None,
        )
    except (OSError, QualificationError) as exc:
        parser.exit(2, f"qualification error: {exc}\n")

    encoded = json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        try:
            args.output.write_text(encoded, encoding="utf-8")
        except OSError as exc:
            parser.exit(2, f"qualification error: {exc}\n")
    print(encoded, end="")
    if summary.qualified is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
