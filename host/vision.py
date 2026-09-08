#!/usr/bin/env python3
"""Pure vision observation contract and quality gate.

No decoder or marker detector is selected here.  The module defines the
coordinate/freshness boundary that any detector must satisfy before an
observation can reach mission or position-control code.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


BODY_FRAME = "body_frd"  # +X forward, +Y right, +Z down
WORLD_FRAME = "world_frd"  # room-fixed test frame with the same handed axes


def _finite(name: str, value: float) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


@dataclass(frozen=True)
class PoseObservation:
    sequence: int
    received_monotonic: float
    x_m: float
    y_m: float
    z_m: float
    yaw_rad: float
    marker_count: int
    reprojection_error_px: float
    confidence: float
    source_timestamp_monotonic: float | None = None
    world_frame: str = WORLD_FRAME
    body_frame: str = BODY_FRAME

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("sequence must be a positive integer")
        for name in (
            "received_monotonic",
            "x_m",
            "y_m",
            "z_m",
            "yaw_rad",
            "reprojection_error_px",
            "confidence",
        ):
            _finite(name, getattr(self, name))
        if self.source_timestamp_monotonic is not None:
            _finite("source_timestamp_monotonic", self.source_timestamp_monotonic)
            if self.source_timestamp_monotonic > self.received_monotonic:
                raise ValueError("source timestamp cannot be newer than receive timestamp")
        if type(self.marker_count) is not int or self.marker_count < 0:
            raise ValueError("marker_count must be a non-negative integer")
        if self.reprojection_error_px < 0:
            raise ValueError("reprojection_error_px must be non-negative")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        if self.world_frame != WORLD_FRAME or self.body_frame != BODY_FRAME:
            raise ValueError("unsupported coordinate frame")

    @property
    def height_above_world_origin_m(self) -> float:
        """Positive-up convenience value for a world frame whose +Z is down."""

        return -self.z_m


@dataclass(frozen=True)
class VisionGateConfig:
    max_receive_age_seconds: float = 0.25
    max_source_age_seconds: float = 0.25
    max_reprojection_error_px: float = 3.0
    min_marker_count: int = 1
    min_confidence: float = 0.5
    max_abs_x_m: float = 2.0
    max_abs_y_m: float = 2.0
    min_height_m: float = 0.0
    max_height_m: float = 2.0

    def __post_init__(self) -> None:
        for name in (
            "max_receive_age_seconds",
            "max_source_age_seconds",
            "max_reprojection_error_px",
            "max_abs_x_m",
            "max_abs_y_m",
            "max_height_m",
        ):
            value = _finite(name, getattr(self, name))
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        _finite("min_height_m", self.min_height_m)
        if self.min_height_m < 0 or self.min_height_m >= self.max_height_m:
            raise ValueError("height bounds are invalid")
        if type(self.min_marker_count) is not int or self.min_marker_count < 1:
            raise ValueError("min_marker_count must be a positive integer")
        _finite("min_confidence", self.min_confidence)
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be in [0, 1]")


@dataclass(frozen=True)
class VisionDecision:
    valid: bool
    reasons: tuple[str, ...]
    receive_age_seconds: float
    source_age_seconds: float | None


def evaluate_observation(
    observation: PoseObservation,
    *,
    now_monotonic: float,
    config: VisionGateConfig | None = None,
) -> VisionDecision:
    config = config or VisionGateConfig()
    now = _finite("now_monotonic", now_monotonic)
    receive_age = now - observation.received_monotonic
    source_age = (
        None
        if observation.source_timestamp_monotonic is None
        else now - observation.source_timestamp_monotonic
    )
    reasons: list[str] = []
    if receive_age < 0:
        reasons.append("observation_from_future")
    elif receive_age > config.max_receive_age_seconds:
        reasons.append("receive_stale")
    if source_age is None:
        reasons.append("source_timestamp_unknown")
    elif source_age < 0:
        reasons.append("source_from_future")
    elif source_age > config.max_source_age_seconds:
        reasons.append("source_stale")
    if observation.marker_count < config.min_marker_count:
        reasons.append("marker_count_low")
    if observation.reprojection_error_px > config.max_reprojection_error_px:
        reasons.append("reprojection_error_high")
    if observation.confidence < config.min_confidence:
        reasons.append("confidence_low")
    if abs(observation.x_m) > config.max_abs_x_m or abs(observation.y_m) > config.max_abs_y_m:
        reasons.append("outside_xy_bounds")
    height = observation.height_above_world_origin_m
    if height < config.min_height_m or height > config.max_height_m:
        reasons.append("outside_height_bounds")
    return VisionDecision(not reasons, tuple(reasons), receive_age, source_age)


@dataclass(frozen=True)
class ObservationSequenceGate:
    """Immutable sequence check helper for replay/state machines."""

    last_sequence: int = 0

    def accept(self, observation: PoseObservation) -> "ObservationSequenceGate":
        if observation.sequence <= self.last_sequence:
            raise ValueError("observation sequence is stale or reordered")
        return ObservationSequenceGate(observation.sequence)


def reject_if_any_invalid(
    observations: Iterable[PoseObservation],
    *,
    now_monotonic: float,
    config: VisionGateConfig | None = None,
) -> tuple[VisionDecision, ...]:
    return tuple(
        evaluate_observation(observation, now_monotonic=now_monotonic, config=config)
        for observation in observations
    )


__all__ = [
    "BODY_FRAME",
    "WORLD_FRAME",
    "ObservationSequenceGate",
    "PoseObservation",
    "VisionDecision",
    "VisionGateConfig",
    "evaluate_observation",
    "reject_if_any_invalid",
]
