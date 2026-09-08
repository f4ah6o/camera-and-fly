"""Deterministic, hardware-free flight-dynamics fixture for G1 qualification.

This is a deliberately low-fidelity plant, not an aircraft model. It uses the
documented ``world_frd`` frame (+X forward, +Y right, +Z down) and keeps all
time in an explicit simulation clock. Delay, loss, reorder, jitter, producer
stop, and command expiry are input fixtures rather than wall-clock behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import math
import random
from pathlib import Path
from typing import Iterable, Sequence


SCHEMA_VERSION = 1
WORLD_FRAME = "world_frd"


class SimulationError(ValueError):
    pass


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise SimulationError(f"{name} must be finite")
    return float(value)


def saturate(value: float, lower: float, upper: float) -> float:
    value = _finite(value, "value")
    lower = _finite(lower, "lower")
    upper = _finite(upper, "upper")
    if lower > upper:
        raise SimulationError("saturation bounds are reversed")
    return min(upper, max(lower, value))


def slew_towards(current: float, target: float, rate_per_second: float, dt: float) -> float:
    current = _finite(current, "current")
    target = _finite(target, "target")
    rate_per_second = _finite(rate_per_second, "rate_per_second")
    dt = _finite(dt, "dt")
    if rate_per_second < 0 or dt < 0:
        raise SimulationError("slew rate and dt must be non-negative")
    step = rate_per_second * dt
    if target > current:
        return min(target, current + step)
    return max(target, current - step)


@dataclass(frozen=True)
class SimCommand:
    at: float
    roll_rad: float = 0.0
    pitch_rad: float = 0.0
    yaw_rad: float = 0.0
    altitude_m: float = 0.0
    ttl_seconds: float = 0.2

    def __post_init__(self) -> None:
        _finite(self.at, "command.at")
        _finite(self.roll_rad, "command.roll_rad")
        _finite(self.pitch_rad, "command.pitch_rad")
        _finite(self.yaw_rad, "command.yaw_rad")
        _finite(self.altitude_m, "command.altitude_m")
        ttl = _finite(self.ttl_seconds, "command.ttl_seconds")
        if self.at < 0 or ttl <= 0:
            raise SimulationError("command time must be non-negative and TTL positive")


@dataclass(frozen=True)
class PlantState:
    at: float
    x_m: float
    y_m: float
    z_m: float
    vx_mps: float
    vy_mps: float
    vz_mps: float
    yaw_rad: float
    applied_roll_rad: float = 0.0
    applied_pitch_rad: float = 0.0
    applied_altitude_m: float = 0.0

    @property
    def altitude_m(self) -> float:
        return -self.z_m

    def position(self) -> dict[str, float]:
        return {"x": self.x_m, "y": self.y_m, "z": self.z_m}


@dataclass(frozen=True)
class SimConfig:
    duration_seconds: float = 2.0
    dt_seconds: float = 0.01
    observation_period_seconds: float = 0.05
    command_period_seconds: float = 0.05
    max_roll_rad: float = 0.35
    max_pitch_rad: float = 0.35
    max_yaw_rad: float = math.pi
    max_altitude_m: float = 2.0
    angle_slew_rad_per_second: float = 2.0
    altitude_slew_mps: float = 1.0
    horizontal_accel_mps2: float = 3.0
    vertical_accel_mps2: float = 4.0
    horizontal_damping_per_second: float = 0.8
    vertical_damping_per_second: float = 1.0
    yaw_rate_rad_per_second: float = 2.0
    observation_max_age_seconds: float = 0.25

    def __post_init__(self) -> None:
        positive = (
            "duration_seconds",
            "dt_seconds",
            "observation_period_seconds",
            "command_period_seconds",
            "max_altitude_m",
            "angle_slew_rad_per_second",
            "altitude_slew_mps",
            "horizontal_accel_mps2",
            "vertical_accel_mps2",
            "horizontal_damping_per_second",
            "vertical_damping_per_second",
            "yaw_rate_rad_per_second",
            "observation_max_age_seconds",
        )
        for name in positive:
            if _finite(getattr(self, name), name) <= 0:
                raise SimulationError(f"{name} must be positive")
        for name in ("max_roll_rad", "max_pitch_rad", "max_yaw_rad"):
            if _finite(getattr(self, name), name) <= 0:
                raise SimulationError(f"{name} must be positive")


@dataclass(frozen=True)
class FaultInjection:
    """Deterministic transport/producer faults applied by packet index."""

    seed: int = 0
    command_delay_seconds: float = 0.0
    observation_delay_seconds: float = 0.0
    command_jitter_seconds: float = 0.0
    observation_jitter_seconds: float = 0.0
    command_drop_indices: frozenset[int] = frozenset()
    observation_drop_indices: frozenset[int] = frozenset()
    telemetry_drop_indices: frozenset[int] = frozenset()
    reorder_observations: bool = False
    producer_stop_at: float | None = None

    def __post_init__(self) -> None:
        if type(self.seed) is not int:
            raise SimulationError("seed must be an integer")
        for name in (
            "command_delay_seconds",
            "observation_delay_seconds",
            "command_jitter_seconds",
            "observation_jitter_seconds",
        ):
            if _finite(getattr(self, name), name) < 0:
                raise SimulationError(f"{name} must be non-negative")
        if self.producer_stop_at is not None and _finite(self.producer_stop_at, "producer_stop_at") < 0:
            raise SimulationError("producer_stop_at must be non-negative")
        for name in ("command_drop_indices", "observation_drop_indices", "telemetry_drop_indices"):
            values = getattr(self, name)
            if any(type(value) is not int or value < 0 for value in values):
                raise SimulationError(f"{name} must contain non-negative integer indices")


@dataclass(frozen=True)
class Observation:
    sequence: int
    source_monotonic: float
    received_monotonic: float
    state: PlantState
    valid: bool
    stale: bool
    dropped: bool
    telemetry_valid: bool
    fault: str | None = None

    @property
    def age_seconds(self) -> float:
        return max(0.0, self.received_monotonic - self.source_monotonic)


@dataclass(frozen=True)
class SimulationResult:
    run_id: str
    config: SimConfig
    injection: FaultInjection
    states: tuple[PlantState, ...]
    observations: tuple[Observation, ...]
    qualification_records: tuple[dict[str, object], ...]
    faults: tuple[str, ...]

    def to_jsonl(self) -> str:
        return "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in self.qualification_records)

    def write_jsonl(self, path: Path) -> None:
        path.write_text(self.to_jsonl(), encoding="utf-8")


@dataclass
class _AppliedCommand:
    command: SimCommand
    delivered_at: float


class FlightDynamicsSimulator:
    """Small deterministic plant plus delayed command/observation channels."""

    def __init__(
        self,
        config: SimConfig | None = None,
        injection: FaultInjection | None = None,
        *,
        run_id: str = "simulation-1",
    ) -> None:
        self.config = config or SimConfig()
        self.injection = injection or FaultInjection()
        if not run_id or "\n" in run_id or "\r" in run_id:
            raise SimulationError("run_id must be a non-empty single-line value")
        self.run_id = run_id

    def run(self, commands: Sequence[SimCommand]) -> SimulationResult:
        if any(commands[index].at > commands[index + 1].at for index in range(len(commands) - 1)):
            raise SimulationError("commands must be monotonic")
        cfg = self.config
        rng = random.Random(self.injection.seed)
        duration = cfg.duration_seconds
        state = PlantState(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        states: list[PlantState] = [state]
        delivered_commands: list[tuple[float, int, SimCommand]] = []
        active: _AppliedCommand | None = None
        last_applied_sequence = 0
        faults: list[str] = []
        command_cursor = 0
        command_index = 0
        observation_index = 0
        next_command_send = 0.0
        next_observation = 0.0
        pending_observations: list[tuple[float, int, PlantState, SimCommand | None, bool]] = []
        observations: list[Observation] = []
        last_observation_sequence = 0
        records: list[dict[str, object]] = []
        latest_intent: SimCommand | None = None

        tail_seconds = max(
            self.injection.command_delay_seconds + self.injection.command_jitter_seconds,
            self.injection.observation_delay_seconds + self.injection.observation_jitter_seconds,
        )
        simulation_end = duration + tail_seconds + cfg.dt_seconds
        tick_count = int(math.ceil(simulation_end / cfg.dt_seconds))
        for tick in range(tick_count + 1):
            now = tick * cfg.dt_seconds
            in_run = now <= duration + 1e-12

            # New commands are available to the producer only after their
            # explicit generation time. The producer stop fault prevents all
            # later heartbeat refreshes.
            while in_run and command_cursor < len(commands) and commands[command_cursor].at <= now:
                latest_intent = commands[command_cursor]
                command_cursor += 1

            if in_run and now + 1e-12 >= next_command_send:
                if latest_intent is not None and (
                    self.injection.producer_stop_at is None or now < self.injection.producer_stop_at
                ):
                    command_index += 1
                    if command_index not in self.injection.command_drop_indices:
                        jitter = rng.uniform(0.0, self.injection.command_jitter_seconds)
                        delivered_at = now + self.injection.command_delay_seconds + jitter
                        delivered_commands.append((delivered_at, command_index, latest_intent))
                    else:
                        faults.append(f"command_drop:{command_index}")
                next_command_send += cfg.command_period_seconds

            due_commands = [item for item in delivered_commands if item[0] <= now + 1e-12]
            delivered_commands = [item for item in delivered_commands if item[0] > now + 1e-12]
            due_commands.sort(key=lambda item: (item[0], item[1]))
            for delivered_at, sequence, command in due_commands:
                if command.at + command.ttl_seconds <= delivered_at:
                    faults.append(f"command_expired:{sequence}")
                    continue
                if sequence <= last_applied_sequence:
                    faults.append(f"command_stale:{sequence}")
                    continue
                active = _AppliedCommand(command, delivered_at)
                last_applied_sequence = sequence

            if in_run and active is not None and now >= active.command.at + active.command.ttl_seconds:
                faults.append(f"command_expired:{last_applied_sequence}")
                active = None

            if in_run:
                state = self._step(state, active.command if active is not None else None, cfg.dt_seconds)
                state = PlantState(
                    now,
                    state.x_m,
                    state.y_m,
                    state.z_m,
                    state.vx_mps,
                    state.vy_mps,
                    state.vz_mps,
                    state.yaw_rad,
                    state.applied_roll_rad,
                    state.applied_pitch_rad,
                    state.applied_altitude_m,
                )
                states.append(state)

            if in_run and now + 1e-12 >= next_observation:
                observation_index += 1
                command_for_target = active.command if active is not None else latest_intent
                dropped = observation_index in self.injection.observation_drop_indices
                if dropped:
                    faults.append(f"observation_drop:{observation_index}")
                jitter = rng.uniform(0.0, self.injection.observation_jitter_seconds)
                delivered_at = now + self.injection.observation_delay_seconds + jitter
                pending_observations.append((delivered_at, observation_index, state, command_for_target, dropped))
                next_observation += cfg.observation_period_seconds

            due_observations = [item for item in pending_observations if item[0] <= now + 1e-12]
            pending_observations = [item for item in pending_observations if item[0] > now + 1e-12]
            if self.injection.reorder_observations and len(due_observations) > 1:
                due_observations.reverse()
            due_observations.sort(key=lambda item: (item[0], item[1]), reverse=self.injection.reorder_observations)
            for delivered_at, sequence, sampled_state, command_for_target, dropped in due_observations:
                stale = sequence <= last_observation_sequence
                too_old = delivered_at - sampled_state.at > cfg.observation_max_age_seconds
                valid = not dropped and not stale and not too_old
                telemetry_valid = valid and sequence not in self.injection.telemetry_drop_indices
                if not telemetry_valid:
                    faults.append(f"telemetry_invalid:{sequence}")
                if stale:
                    faults.append(f"observation_stale:{sequence}")
                if valid:
                    last_observation_sequence = sequence
                fault = None if valid and telemetry_valid else (
                    "observation_dropped" if dropped else "observation_stale" if stale else "observation_too_old" if too_old else "telemetry_invalid"
                )
                observation = Observation(
                    sequence,
                    sampled_state.at,
                    delivered_at,
                    sampled_state,
                    valid,
                    stale or too_old,
                    dropped,
                    telemetry_valid,
                    fault,
                )
                observations.append(observation)
                records.append(
                    self._sample_record(
                        observation,
                        command_for_target,
                        run_id=self.run_id,
                        command_period_seconds=cfg.command_period_seconds,
                        saturated=self._is_saturated(command_for_target, cfg),
                    )
                )

            if now >= simulation_end:
                break

        outcome = "failed" if faults else "completed"
        records.append(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "kind": "outcome",
                "outcome": outcome,
                "evidence_kind": "simulation",
                "simulation": True,
                "faults": sorted(set(faults)),
            }
        )
        return SimulationResult(
            self.run_id,
            cfg,
            self.injection,
            tuple(states),
            tuple(observations),
            tuple(records),
            tuple(sorted(set(faults))),
        )

    def _step(self, state: PlantState, command: SimCommand | None, dt: float) -> PlantState:
        cfg = self.config
        if command is None:
            target_roll = target_pitch = target_yaw = 0.0
            target_altitude = -state.z_m
        else:
            target_roll = saturate(command.roll_rad, -cfg.max_roll_rad, cfg.max_roll_rad)
            target_pitch = saturate(command.pitch_rad, -cfg.max_pitch_rad, cfg.max_pitch_rad)
            target_yaw = saturate(command.yaw_rad, -cfg.max_yaw_rad, cfg.max_yaw_rad)
            target_altitude = saturate(command.altitude_m, 0.0, cfg.max_altitude_m)

        roll = slew_towards(state.applied_roll_rad, target_roll, cfg.angle_slew_rad_per_second, dt)
        pitch = slew_towards(state.applied_pitch_rad, target_pitch, cfg.angle_slew_rad_per_second, dt)
        yaw_target = slew_towards(state.yaw_rad, target_yaw, cfg.yaw_rate_rad_per_second, dt)
        altitude_target = slew_towards(state.applied_altitude_m, target_altitude, cfg.altitude_slew_mps, dt)

        # Documented sign convention: positive pitch (nose up) produces -X
        # body acceleration; positive roll (left shoulder rises) produces +Y.
        body_x = -cfg.horizontal_accel_mps2 * math.tan(pitch)
        body_y = cfg.horizontal_accel_mps2 * math.tan(roll)
        cos_yaw = math.cos(state.yaw_rad)
        sin_yaw = math.sin(state.yaw_rad)
        ax = body_x * cos_yaw - body_y * sin_yaw - cfg.horizontal_damping_per_second * state.vx_mps
        ay = body_x * sin_yaw + body_y * cos_yaw - cfg.horizontal_damping_per_second * state.vy_mps
        altitude_error = altitude_target - (-state.z_m)
        altitude_accel = saturate(
            altitude_error * cfg.vertical_accel_mps2 - cfg.vertical_damping_per_second * state.vz_mps,
            -cfg.vertical_accel_mps2,
            cfg.vertical_accel_mps2,
        )
        vx = state.vx_mps + ax * dt
        vy = state.vy_mps + ay * dt
        vz = state.vz_mps + altitude_accel * dt
        x = state.x_m + vx * dt
        y = state.y_m + vy * dt
        altitude = max(0.0, -state.z_m + vz * dt)
        return PlantState(
            state.at + dt,
            x,
            y,
            -altitude,
            vx,
            vy,
            vz,
            yaw_target,
            roll,
            pitch,
            altitude_target,
        )

    @staticmethod
    def _is_saturated(command: SimCommand | None, config: SimConfig | None = None) -> bool:
        if command is None or config is None:
            return False
        return (
            abs(command.roll_rad) > config.max_roll_rad
            or abs(command.pitch_rad) > config.max_pitch_rad
            or abs(command.yaw_rad) > config.max_yaw_rad
            or command.altitude_m < 0.0
            or command.altitude_m > config.max_altitude_m
        )

    @staticmethod
    def _sample_record(
        observation: Observation,
        command: SimCommand | None,
        *,
        run_id: str,
        command_period_seconds: float,
        saturated: bool,
    ) -> dict[str, object]:
        target_altitude = command.altitude_m if command is not None else observation.state.altitude_m
        target = {"x": 0.0, "y": 0.0, "z": -target_altitude}
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "kind": "sample",
            "evidence_kind": "simulation",
            "simulation": True,
            "frame": WORLD_FRAME,
            "target_position_m": target,
            "observed_position_m": observation.state.position(),
            "observation_valid": observation.valid,
            "telemetry_valid": observation.telemetry_valid,
            "observation_age_seconds": observation.age_seconds,
            "intent_age_seconds": observation.age_seconds if command is None else max(0.0, observation.received_monotonic - command.at),
            "command_period_seconds": command_period_seconds,
            "saturated": saturated,
            "fault": observation.fault,
            "observation_sequence": observation.sequence,
            "source_monotonic": observation.source_monotonic,
            "received_monotonic": observation.received_monotonic,
        }


def default_commands(*, duration: float = 2.0, altitude_m: float = 0.3) -> list[SimCommand]:
    if not math.isfinite(duration) or duration <= 0:
        raise SimulationError("duration must be positive")
    return [SimCommand(0.0, altitude_m=altitude_m, ttl_seconds=0.2)]


def main() -> int:
    parser = argparse.ArgumentParser(description="deterministic hardware-free flight dynamics fixture")
    parser.add_argument("--duration", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    simulator = FlightDynamicsSimulator(
        SimConfig(duration_seconds=args.duration),
        FaultInjection(seed=args.seed),
    )
    result = simulator.run(default_commands(duration=args.duration))
    result.write_jsonl(args.output)
    print(json.dumps({"run_id": result.run_id, "records": len(result.qualification_records), "faults": result.faults}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
