#!/usr/bin/env python3
"""Deterministic input fixtures for the camera/CF1 dry-run."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Iterable


class ReplayFormatError(ValueError):
    pass


class ReplayClock:
    def __init__(self, start: float = 0.0) -> None:
        if not math.isfinite(start):
            raise ValueError("clock start must be finite")
        self._now = start

    def now(self) -> float:
        return self._now

    def set(self, value: float) -> None:
        if not math.isfinite(value) or value < self._now:
            raise ValueError("replay clock must be finite and monotonic")
        self._now = value

    def advance(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("advance must be finite and non-negative")
        self._now += seconds


@dataclass(frozen=True)
class ReplayEvent:
    at: float
    kind: str
    payload: dict[str, object]

    def __post_init__(self) -> None:
        if not math.isfinite(self.at) or self.at < 0:
            raise ValueError("event time must be finite and non-negative")
        if self.kind not in {"frame", "status", "serial_error", "camera_error", "stop"}:
            raise ValueError(f"unsupported replay event kind: {self.kind}")


def load_events(path: Path) -> list[ReplayEvent]:
    events: list[ReplayEvent] = []
    previous = -1.0
    for line_number, raw in enumerate(path.read_text().splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ReplayFormatError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ReplayFormatError(f"{path}:{line_number}: event must be an object")
        try:
            at = value["at"]
            kind = value["kind"]
            payload = value.get("payload", {})
            if not isinstance(at, (int, float)) or not isinstance(kind, str) or not isinstance(payload, dict):
                raise TypeError
            event = ReplayEvent(float(at), kind, dict(payload))
        except (KeyError, TypeError, ValueError) as exc:
            raise ReplayFormatError(f"{path}:{line_number}: invalid event fields") from exc
        if event.at < previous:
            raise ReplayFormatError(f"{path}:{line_number}: events are not monotonic")
        previous = event.at
        events.append(event)
    return events


def synthetic_events(*, duration: float = 2.0, frame_period: float = 0.1) -> list[ReplayEvent]:
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(frame_period) or frame_period <= 0:
        raise ValueError("duration and frame_period must be finite and positive")
    events: list[ReplayEvent] = []
    timestamp = 0.0
    frame_id = 1
    while timestamp <= duration:
        events.append(
            ReplayEvent(
                timestamp,
                "frame",
                {"frame_id": frame_id, "received_monotonic": timestamp},
            )
        )
        frame_id += 1
        timestamp += frame_period
    return events


def iter_ticks(start: float, end: float, period: float) -> Iterable[float]:
    if not all(math.isfinite(value) for value in (start, end, period)) or period <= 0 or end < start:
        raise ValueError("invalid tick interval")
    current = start
    while current < end:
        yield current
        current += period


__all__ = ["ReplayClock", "ReplayEvent", "ReplayFormatError", "iter_ticks", "load_events", "synthetic_events"]
