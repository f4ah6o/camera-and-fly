#!/usr/bin/env python3
"""Safe camera/StampFly integration and replay CLI.

The default mode is replay and never opens a camera or serial device.  The
hardware adapter deliberately exposes only zero-control SET and STATUS; it has
no ARM method and rejects every non-zero control value.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from enum import Enum
import json
import math
from pathlib import Path
import statistics
import time
from typing import Any, Callable, Iterable, Optional
import uuid

try:
    from .atomcam import AtomCamSource
    from .camera_worker import CameraWorker, FrameSnapshot, LatestFrameSlot
    from .control_loop import ControlIntent, ControlScheduler, SchedulerState
    from .replay import ReplayClock, ReplayEvent, iter_ticks, load_events, synthetic_events
    from .stampfly import ALT_MODE_MANUAL, CONTROL_MODE_ANGLE, StampFly, StampFlyError, StampFlyStatus
except ImportError:  # pragma: no cover - exercised by the script entrypoint
    from atomcam import AtomCamSource
    from camera_worker import CameraWorker, FrameSnapshot, LatestFrameSlot
    from control_loop import ControlIntent, ControlScheduler, SchedulerState
    from replay import ReplayClock, ReplayEvent, iter_ticks, load_events, synthetic_events
    from stampfly import ALT_MODE_MANUAL, CONTROL_MODE_ANGLE, StampFly, StampFlyError, StampFlyStatus


class IntegrationState(str, Enum):
    RUNNING = "RUNNING"
    FAULT = "FAULT"
    STOPPED = "STOPPED"


class IntegrationFault(RuntimeError):
    pass


class SafeZeroAdapter:
    """A write gate for dry-run/safe-hardware use.

    It intentionally does not implement ``arm``.  Python callers cannot use
    this adapter to bypass the safe-test boundary accidentally.
    """

    def __init__(self, transport: Any) -> None:
        self._transport = transport
        self.commands: list[tuple[float, float, float, float, int, int]] = []
        self.arm_attempts = 0

    @property
    def disarm_count(self) -> int:
        return int(getattr(self._transport, "disarm_count", 0))

    def set_control(
        self,
        roll: float,
        pitch: float,
        yaw: float,
        throttle: float,
        *,
        control_mode: int,
        alt_mode: int,
        wait_ack: bool,
    ) -> int:
        values = (roll, pitch, yaw, throttle)
        if any(not math.isfinite(value) for value in values):
            raise IntegrationFault("safe gate received non-finite control")
        if values != (0.0, 0.0, 0.0, 0.0) or control_mode != CONTROL_MODE_ANGLE or alt_mode != ALT_MODE_MANUAL:
            raise IntegrationFault("safe gate permits only zero ANGLE/MANUAL SET")
        self.commands.append((roll, pitch, yaw, throttle, control_mode, alt_mode))
        return self._transport.set_control(
            0.0,
            0.0,
            0.0,
            0.0,
            control_mode=CONTROL_MODE_ANGLE,
            alt_mode=ALT_MODE_MANUAL,
            wait_ack=wait_ack,
        )

    def best_effort_disarm(self) -> None:
        self._transport.best_effort_disarm()

    def status(self) -> StampFlyStatus:
        return self._transport.status()


class FakeSafeTransport:
    """Wire-log transport used by replay tests."""

    def __init__(self) -> None:
        self.commands: list[dict[str, object]] = []
        self.disarm_count = 0
        self.fail = False
        self._sequence = 0

    def set_control(self, roll: float, pitch: float, yaw: float, throttle: float, *, control_mode: int, alt_mode: int, wait_ack: bool) -> int:
        if self.fail:
            raise StampFlyError("fake serial disconnected")
        self._sequence += 1
        self.commands.append(
            {
                "command": "SET",
                "sequence": self._sequence,
                "roll": roll,
                "pitch": pitch,
                "yaw": yaw,
                "throttle": throttle,
                "control_mode": control_mode,
                "alt_mode": alt_mode,
                "wait_ack": wait_ack,
            }
        )
        return self._sequence

    def best_effort_disarm(self) -> None:
        self.disarm_count += 1
        self.commands.append({"command": "DISARM"})


class BoundedJsonlLogger:
    def __init__(self, path: Path | None, *, max_bytes: int = 8 * 1024 * 1024) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.path = path
        self.max_bytes = max_bytes
        self.bytes_written = 0
        self.records: list[dict[str, object]] = []
        self._file = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("w", encoding="utf-8")

    def write(self, record: dict[str, object]) -> None:
        line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        encoded = line.encode("utf-8")
        if self.bytes_written + len(encoded) > self.max_bytes:
            raise IntegrationFault("integration log size limit exceeded")
        self.bytes_written += len(encoded)
        self.records.append(record)
        if self._file is not None:
            self._file.write(line)
            self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


@dataclass
class IntegrationSummary:
    schema_version: int
    session_id: str
    state: str
    duration_seconds: float
    ticks: int
    tick_period_max_seconds: float | None
    tick_period_p95_seconds: float | None
    camera_gaps: int
    fault_reasons: list[str]
    set_count: int
    arm_count: int
    disarm_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.quantiles(values, n=20, method="inclusive")[18] if len(values) >= 2 else values[0]


class DryRunSession:
    """Run zero-control integration against replay or fake dependencies."""

    def __init__(
        self,
        transport: Any,
        *,
        clock: ReplayClock | None = None,
        camera_max_age: float = 0.5,
        tick_period: float = 0.05,
        logger: BoundedJsonlLogger | None = None,
        session_id: str = "replay",
    ) -> None:
        if not math.isfinite(camera_max_age) or camera_max_age <= 0:
            raise ValueError("camera_max_age must be positive")
        if not math.isfinite(tick_period) or tick_period <= 0:
            raise ValueError("tick_period must be positive")
        self.clock = clock or ReplayClock()
        self.transport = SafeZeroAdapter(transport)
        self.scheduler = ControlScheduler(
            self.transport,
            local_watchdog_seconds=min(0.2, max(0.01, tick_period * 3.0)),
            monotonic_clock=self.clock.now,
        )
        self.camera_slot = LatestFrameSlot(monotonic_clock=self.clock.now)
        self.camera_max_age = camera_max_age
        self.tick_period = tick_period
        self.logger = logger or BoundedJsonlLogger(None)
        self.session_id = session_id
        self.state = IntegrationState.RUNNING
        self.fault_reasons: list[str] = []
        self._intent_sequence = 0
        self._tick_times: list[float] = []
        self._last_tick: float | None = None
        self._camera_gaps = 0
        self._status: StampFlyStatus | None = None
        self._status_error: str | None = None

    @property
    def commands(self) -> list[tuple[float, float, float, float, int, int]]:
        return self.transport.commands

    def process_event(self, event: ReplayEvent) -> None:
        self.clock.set(event.at)
        if event.kind == "frame":
            frame_id = int(event.payload.get("frame_id", 1))
            # The replay does not need raw image bytes.  A tiny valid marker is
            # enough to prove that the latest-value path received an event.
            try:
                from .atomcam import AtomCamFrame
            except ImportError:  # pragma: no cover
                from atomcam import AtomCamFrame

            self.camera_slot.publish(
                AtomCamFrame(
                    captured_at=event.at,
                    jpeg=b"\xff\xd8\xff\xd9",
                    content_type="image/jpeg",
                    frame_id=frame_id,
                    request_started_monotonic=event.at,
                    received_monotonic=float(event.payload.get("received_monotonic", event.at)),
                )
            )
        elif event.kind == "status":
            line = event.payload.get("line")
            if not isinstance(line, str):
                self._status_error = "status_missing_line"
            else:
                try:
                    self._status = StampFlyStatus.parse(line)
                    self._status_error = None
                except Exception as exc:  # parser failures are part of the replay input
                    self._status_error = f"status_invalid:{type(exc).__name__}"
        elif event.kind == "serial_error":
            self._latch_fault(str(event.payload.get("reason", "serial_error")))
        elif event.kind == "camera_error":
            reason = str(event.payload.get("reason", "camera_error"))
            self.camera_slot.publish_error(reason)
            self._latch_fault(f"camera:{reason}")
        elif event.kind == "stop":
            self.state = IntegrationState.STOPPED

    def tick(self) -> None:
        if self.state is IntegrationState.STOPPED:
            return
        now = self.clock.now()
        if self._last_tick is not None:
            self._tick_times.append(max(0.0, now - self._last_tick))
        self._last_tick = now
        frame = self.camera_slot.snapshot(now=now, max_age=self.camera_max_age)
        if not frame.valid:
            self._camera_gaps += 1

        if self._status_error is not None:
            self._latch_fault(self._status_error)
        elif self._status is not None and (not self._status.safe_test or not self._status.claimed):
            self._latch_fault("unsafe_status")

        if self.state is IntegrationState.RUNNING:
            self._intent_sequence += 1
            self.scheduler.publish(ControlIntent.zero(sequence=self._intent_sequence, now=now, ttl=max(0.01, self.tick_period * 2.0)))
            result = self.scheduler.tick(now=now)
            if result.state is SchedulerState.FAULT:
                self._latch_fault(result.fault_reason or "scheduler_fault")

        self.logger.write(
            {
                "schema_version": 1,
                "session_id": self.session_id,
                "monotonic": now,
                "state": self.state.value,
                "camera": {
                    "valid": frame.valid,
                    "age_seconds": frame.age_seconds,
                    "frame_id": frame.frame.frame_id if frame.frame else None,
                    "error": frame.error,
                },
                "serial": {
                    "status_valid": self._status is not None and self._status_error is None,
                    "status_error": self._status_error,
                    "last_send_age_seconds": None if self.scheduler.last_sent_at is None else max(0.0, now - self.scheduler.last_sent_at),
                    "fault": self.scheduler.fault_reason,
                },
                "fault_reasons": list(self.fault_reasons),
            }
        )

    def run(self, events: Iterable[ReplayEvent]) -> IntegrationSummary:
        sorted_events = list(events)
        previous = self.clock.now()
        for event in sorted_events:
            if event.at < previous:
                raise ValueError("replay events must be monotonic")
            for timestamp in iter_ticks(previous, event.at, self.tick_period):
                self.clock.set(timestamp)
                self.tick()
            self.process_event(event)
            self.tick()
            previous = event.at
        self.state = IntegrationState.STOPPED if self.state is IntegrationState.RUNNING else self.state
        duration = max(0.0, self.clock.now())
        summary = IntegrationSummary(
            schema_version=1,
            session_id=self.session_id,
            state=self.state.value,
            duration_seconds=duration,
            ticks=len(self._tick_times) + (1 if self._last_tick is not None else 0),
            tick_period_max_seconds=max(self._tick_times) if self._tick_times else None,
            tick_period_p95_seconds=_p95(self._tick_times),
            camera_gaps=self._camera_gaps,
            fault_reasons=list(self.fault_reasons),
            set_count=len(self.transport.commands),
            arm_count=0,
            disarm_count=self.transport.disarm_count,
        )
        return summary

    def _latch_fault(self, reason: str) -> None:
        if reason not in self.fault_reasons:
            self.fault_reasons.append(reason)
        if self.state is IntegrationState.RUNNING:
            self.state = IntegrationState.FAULT
            self.scheduler.fault(reason)


def _default_status() -> ReplayEvent:
    return ReplayEvent(
        0.0,
        "status",
        {
            "line": "CF1 STATUS claimed=1 armed=0 connected=1 mode=3 voltage=4.1 roll=0 pitch=0 yaw=0 altitude=0 range=0 safe_test=1"
        },
    )


def run_replay(events: list[ReplayEvent], *, output: Path | None = None) -> IntegrationSummary:
    logger = BoundedJsonlLogger(output)
    try:
        session = DryRunSession(FakeSafeTransport(), logger=logger, session_id=str(uuid.uuid4()))
        summary = session.run(events)
        if output is not None:
            summary_path = output.with_suffix(output.suffix + ".summary.json")
            summary_path.write_text(json.dumps(summary.to_dict(), indent=2) + "\n")
        return summary
    finally:
        logger.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="camera/StampFly safe integration")
    parser.add_argument("--mode", choices=("replay", "safe-hardware"), default="replay")
    parser.add_argument("--input", type=Path, help="JSONL replay; omitted uses synthetic input")
    parser.add_argument("--log", type=Path)
    parser.add_argument("--camera-url")
    parser.add_argument("--port")
    args = parser.parse_args()

    if args.mode == "replay":
        events = load_events(args.input) if args.input else [_default_status(), *synthetic_events()]
        summary = run_replay(events, output=args.log)
        print(json.dumps(summary.to_dict(), indent=2))
        return 0 if not summary.fault_reasons else 1

    if not args.camera_url or not args.port:
        parser.error("--mode safe-hardware requires --camera-url and --port")
    # The live mode is deliberately conservative: it only starts after the
    # explicit safe-test status check and never exposes ARM or non-zero SET.
    source = AtomCamSource(args.camera_url)
    worker = CameraWorker(source)
    logger = BoundedJsonlLogger(args.log)
    scheduler: ControlScheduler | None = None
    stampfly: StampFly | None = None
    try:
        stampfly = StampFly(args.port)
        stampfly.connect()
        status = stampfly.status()
        if not status.safe_test or not status.claimed:
            raise IntegrationFault("safe-hardware mode requires safe_test=1 and claimed=1")
        adapter = SafeZeroAdapter(stampfly)
        scheduler = ControlScheduler(adapter, local_watchdog_seconds=0.2)
        worker.start()
        print("safe-hardware mode started; ARM is unavailable and all SET values are zero", flush=True)
        sequence = 0
        last_tick: float | None = None
        next_status = time.monotonic()
        while True:
            cycle_started = time.monotonic()
            if last_tick is not None and cycle_started - last_tick > 0.2:
                scheduler.fault("safe_hardware_tick_late")
                raise IntegrationFault("safe-hardware control tick exceeded 200 ms")
            last_tick = cycle_started
            sequence += 1
            scheduler.publish(ControlIntent.zero(sequence=sequence, now=cycle_started))
            result = scheduler.tick(now=cycle_started)
            if result.state is SchedulerState.FAULT:
                raise IntegrationFault(result.fault_reason or "safe-hardware scheduler fault")

            frame = worker.snapshot(now=cycle_started, max_age=0.5)
            status_error: str | None = None
            if cycle_started >= next_status:
                try:
                    status = stampfly.status()
                    if not status.safe_test or not status.claimed:
                        status_error = "unsafe_status"
                except (StampFlyError, OSError, ValueError) as exc:
                    status_error = f"status:{type(exc).__name__}"
                next_status = cycle_started + 1.0
            if status_error is not None:
                scheduler.fault(status_error)
                raise IntegrationFault(status_error)
            logger.write(
                {
                    "schema_version": 1,
                    "session_id": "safe-hardware",
                    "monotonic": cycle_started,
                    "state": "RUNNING",
                    "camera": {
                        "valid": frame.valid,
                        "age_seconds": frame.age_seconds,
                        "frame_id": frame.frame.frame_id if frame.frame else None,
                        "error": frame.error,
                    },
                    "serial": {
                        "status_valid": True,
                        "status_error": None,
                        "last_send_age_seconds": 0.0,
                        "fault": None,
                    },
                    "fault_reasons": [],
                }
            )
            time.sleep(max(0.0, 0.05 - (time.monotonic() - cycle_started)))
    except KeyboardInterrupt:
        return 130
    except (StampFlyError, IntegrationFault, OSError, ValueError) as exc:
        parser.exit(1, f"integration error: {exc}\n")
    finally:
        worker.stop(timeout=2.0)
        if scheduler is not None:
            scheduler.stop()
        if stampfly is not None:
            stampfly.best_effort_disarm()
            stampfly.close()
        logger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
