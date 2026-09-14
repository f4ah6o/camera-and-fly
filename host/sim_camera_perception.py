#!/usr/bin/env python3
"""Narrow simulation-only camera -> perception -> outer-control closed loop.

This module is Milestone B on top of the Milestone A SILS transport in
``host/stampfly_sils.py``.  It implements the narrowest *true* loop that keeps
the simulator state out of the controller except through real pixels:

    simulator STATE  ->  deterministic rendered frame (real pixel bytes)
                     ->  detector reads the pixels into a production
                         ``host.vision.PoseObservation``
                     ->  ``host.vision`` freshness/quality gate
                     ->  outer position controller
                     ->  expiring ``host.control_loop.ControlIntent``
                     ->  existing ``ControlScheduler`` / ``SilsControlAdapter``
                     ->  installed ``emu_vehicle``
                     ->  fresh STATE -> next frame -> next decision

The raw simulator STATE is never handed to the controller and no
``PoseObservation`` is fabricated: the controller only sees a pose that a
detector measured from the bytes of a rendered frame.

Coordinate / unit conventions (explicit)
----------------------------------------
``world_frd``  +X forward, +Y right, +Z down (same as ``host/vision.py``).
``body_frd``   +X forward, +Y right, +Z down.
``sim_camera_downward``  a fixed camera above the world origin looking straight
    down world +Z.  Image column ``u`` increases toward world +Y (right) and
    image row ``v`` increases toward world +X (forward).  A pinhole projection
    with focal length ``focal_px`` maps a marker at world ``(x_m, y_m, z_m)``
    with camera height ``C`` and depth ``D = z_m + C = C - height`` to::

        u = principal_x + focal_px * y_m / D
        v = principal_y + focal_px * x_m / D
        r_px = focal_px * marker_radius_m / D

    All host contract values are metres/radians.  Upstream SILS STATE emits
    attitude in degrees; ``host/stampfly_sils.parse_state_line`` converts that
    to radians at the transport boundary, so this module only ever sees
    radians and metres.

Marker model (simulation-only, not a real calibration claim)
------------------------------------------------------------
The marker is a disk of known physical radius.  Its in-plane yaw is encoded as
a linear intensity ramp across the disk, so a detector can recover yaw from the
image moments without a real fiducial library.  Horizontal marker offset is a
documented simulation mount model (a lever arm that tilts with body
roll/pitch) because the disarmed emu_vehicle STATE does not carry a horizontal
position.  This is *not* a real camera calibration and makes no accuracy claim.

Provenance: everything is ``provider=stampfly_ecosystem``,
``evidence_kind=simulation``, ``simulation=true``, ``flight_qualified=false``.
The loop never arms, never sends ``arm``/``land``/``disarm``, never opens a
serial/USB device, and always keeps ``throttle`` at zero.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Callable, Optional, Protocol, Sequence

try:  # pragma: no cover - exercised through the package import
    from .camera_stream import DecodedFrame
    from .control_loop import ControlIntent, ControlScheduler, SchedulerState
    from .mission import (
        ActionKind,
        HealthSnapshot,
        MissionConfig,
        MissionSupervisor,
        OperatorEvent,
    )
    from .stampfly_sils import (
        DEFAULT_DURATION_SECONDS,
        EVIDENCE_KIND,
        PROVIDER,
        SilsTelemetry,
        StampFlySilsError,
        StampFlySimTransport,
        SilsControlAdapter,
        resolve_sils_root,
        telemetry_to_health,
    )
    from .vision import (
        BODY_FRAME,
        WORLD_FRAME,
        PoseObservation,
        VisionDecision,
        VisionGateConfig,
        evaluate_observation,
    )
except ImportError:  # pragma: no cover - script entrypoint
    from camera_stream import DecodedFrame
    from control_loop import ControlIntent, ControlScheduler, SchedulerState
    from mission import (
        ActionKind,
        HealthSnapshot,
        MissionConfig,
        MissionSupervisor,
        OperatorEvent,
    )
    from stampfly_sils import (
        DEFAULT_DURATION_SECONDS,
        EVIDENCE_KIND,
        PROVIDER,
        SilsTelemetry,
        StampFlySilsError,
        StampFlySimTransport,
        SilsControlAdapter,
        resolve_sils_root,
        telemetry_to_health,
    )
    from vision import (
        BODY_FRAME,
        WORLD_FRAME,
        PoseObservation,
        VisionDecision,
        VisionGateConfig,
        evaluate_observation,
    )


SCHEMA_VERSION = 1
MILESTONE = "B"
SIM_BACKEND = "sils-emu-sim-camera"
SIM_CAMERA_FRAME = "sim_camera_downward"
SIM_PROVIDER = PROVIDER
SIM_EVIDENCE_KIND = EVIDENCE_KIND

_SAFE_MISSION_ACTIONS = frozenset(
    {
        ActionKind.NONE,
        ActionKind.BEGIN_PREFLIGHT,
        ActionKind.REQUEST_SAFE_RECOVERY,
        ActionKind.COMPLETE,
    }
)


class SimCameraError(RuntimeError):
    """Base class for simulation camera/perception faults."""


class MalformedFrameError(SimCameraError):
    """Raised when frame bytes cannot be interpreted by the detector."""


class DetectorNoResult(SimCameraError):
    """Raised when a frame contains no usable marker."""


class CameraStalled(SimCameraError):
    """Raised when the frame source produced no frame for a tick."""


class VisionRuntimeFault(SimCameraError):
    """Raised for an unrecoverable runtime boundary fault."""


def _finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    return float(value)


def wrap_angle(angle: float) -> float:
    """Wrap a radian angle into ``[-pi, pi)``."""

    value = _finite("angle", angle)
    return (value + math.pi) % (2.0 * math.pi) - math.pi


# ---------------------------------------------------------------------------
# Simulation camera model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SimCameraModel:
    """Deterministic simulation-only downward camera + marker mount model."""

    width: int = 128
    height: int = 128
    focal_px: float = 400.0
    camera_height_m: float = 1.0
    marker_radius_m: float = 0.1
    marker_altitude_offset_m: float = 0.05
    lever_arm_m: float = 0.25
    min_marker_pixels: int = 16
    marker_threshold: int = 40
    min_yaw_offset_px: float = 0.5
    yaw_image_sign: float = 1.0

    def __post_init__(self) -> None:
        if type(self.width) is not int or type(self.height) is not int:
            raise ValueError("width/height must be integers")
        if self.width < 8 or self.height < 8:
            raise ValueError("image must be at least 8x8")
        if type(self.min_marker_pixels) is not int or self.min_marker_pixels < 1:
            raise ValueError("min_marker_pixels must be a positive integer")
        if type(self.marker_threshold) is not int or not 0 <= self.marker_threshold <= 255:
            raise ValueError("marker_threshold must be in [0, 255]")
        for name in (
            "focal_px",
            "camera_height_m",
            "marker_radius_m",
            "lever_arm_m",
            "min_yaw_offset_px",
            "yaw_image_sign",
        ):
            if _finite(name, getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        _finite("marker_altitude_offset_m", self.marker_altitude_offset_m)
        if self.marker_altitude_offset_m < 0:
            raise ValueError("marker_altitude_offset_m must be non-negative")
        if self.marker_altitude_offset_m >= self.camera_height_m:
            raise ValueError("marker mount offset must be below the camera")

    @property
    def principal_x(self) -> float:
        return self.width / 2.0

    @property
    def principal_y(self) -> float:
        return self.height / 2.0

    @property
    def pixel_format(self) -> str:
        return "gray"

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height

    def project(self, *, pitch_rad: float, roll_rad: float, yaw_rad: float, altitude_m: float) -> tuple[float, float, float, float]:
        """Project a simulated vehicle pose to marker ``(u, v, radius_px, theta)``."""

        pitch = _finite("pitch_rad", pitch_rad)
        roll = _finite("roll_rad", roll_rad)
        yaw = _finite("yaw_rad", yaw_rad)
        altitude = _finite("altitude_m", altitude_m)
        marker_height = altitude + self.marker_altitude_offset_m
        depth = self.camera_height_m - marker_height
        if depth <= 0.05:
            raise SimCameraError("marker is at or above the camera near plane")
        x_m = self.lever_arm_m * math.sin(pitch)
        y_m = self.lever_arm_m * math.sin(roll)
        u = self.principal_x + self.focal_px * y_m / depth
        v = self.principal_y + self.focal_px * x_m / depth
        radius = self.focal_px * self.marker_radius_m / depth
        theta = self.yaw_image_sign * yaw
        return u, v, radius, theta


def _apply_frame_watermark(data: bytearray, model: SimCameraModel, *, frame_id: int, generation: int) -> None:
    """Write a sub-threshold frame/generation identity into the first pixels."""

    low = model.marker_threshold // 2
    code = ((frame_id & 0xFFFF) << 16) | (generation & 0xFFFF)
    for bit in range(32):
        index = bit
        if index >= model.frame_bytes:  # pragma: no cover - model enforces >= 8x8
            break
        data[index] = low if (code >> bit) & 1 else 0


def render_marker_frame(
    telemetry: SilsTelemetry,
    model: SimCameraModel,
    *,
    frame_id: int,
    generation: int = 0,
    received_monotonic: Optional[float] = None,
    decode_complete_monotonic: Optional[float] = None,
) -> DecodedFrame:
    """Render the deterministic simulation frame for one simulator STATE.

    The frame is a real grayscale image.  Only the geometry and photometry
    derived from the simulator STATE are written; no ``PoseObservation`` is
    constructed here.
    """

    if not isinstance(telemetry, SilsTelemetry):
        raise TypeError("telemetry must be a SilsTelemetry")
    if type(frame_id) is not int or frame_id < 1:
        raise ValueError("frame_id must be a positive integer")
    if type(generation) is not int or generation < 0:
        raise ValueError("generation must be a non-negative integer")
    received = telemetry.received_monotonic if received_monotonic is None else _finite("received_monotonic", received_monotonic)
    complete = received if decode_complete_monotonic is None else _finite("decode_complete_monotonic", decode_complete_monotonic)
    if complete < received:
        raise ValueError("decode_complete_monotonic cannot precede receive time")

    u, v, radius, theta = model.project(
        pitch_rad=telemetry.pitch_rad,
        roll_rad=telemetry.roll_rad,
        yaw_rad=telemetry.yaw_rad,
        altitude_m=telemetry.altitude_m,
    )
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    data = bytearray(model.frame_bytes)
    width = model.width
    for row in range(model.height):
        dy = row - v
        row_offset = row * width
        for col in range(width):
            dx = col - u
            if dx * dx + dy * dy > radius * radius:
                continue
            projection = dx * cos_t + dy * sin_t
            fraction = 0.5 + 0.5 * (projection / radius)
            fraction = min(1.0, max(0.0, fraction))
            data[row_offset + col] = int(round(80.0 + 175.0 * fraction))

    # A sub-threshold frame/generation watermark makes successive frame bytes
    # provably different even when the simulated attitude is static.  The
    # detector ignores pixels at or below ``marker_threshold``, so this is not
    # a pose channel and cannot change the measured pose.
    _apply_frame_watermark(data, model, frame_id=frame_id, generation=generation)
    return DecodedFrame(
        frame_id=frame_id,
        data=bytes(data),
        width=model.width,
        height=model.height,
        pixel_format=model.pixel_format,
        received_monotonic=received,
        decode_complete_monotonic=complete,
        source_pts=None,
        source_dts=None,
        decoder_generation=generation,
    )


def detect_pose(
    frame: DecodedFrame,
    model: SimCameraModel,
    *,
    sequence: int,
) -> PoseObservation:
    """Measure a ``PoseObservation`` from a rendered frame's real pixels.

    This is the simulation-only detector.  It performs image-moment detection
    on the grayscale bytes; it never receives a pose from the caller.
    """

    if not isinstance(frame, DecodedFrame):
        raise TypeError("frame must be a DecodedFrame")
    if frame.pixel_format != model.pixel_format:
        raise MalformedFrameError(f"unsupported pixel format: {frame.pixel_format!r}")
    if frame.width != model.width or frame.height != model.height:
        raise MalformedFrameError("frame resolution does not match the simulation camera")
    if len(frame.data) != model.frame_bytes:
        raise MalformedFrameError("frame byte length does not match the resolution")

    threshold = model.marker_threshold
    data = frame.data
    width = model.width
    count = 0
    sum_u = 0.0
    sum_v = 0.0
    weight = 0.0
    weighted_u = 0.0
    weighted_v = 0.0
    pixels: list[tuple[int, int]] = []
    for row in range(model.height):
        row_offset = row * width
        for col in range(width):
            value = data[row_offset + col]
            if value <= threshold:
                continue
            count += 1
            sum_u += col
            sum_v += row
            weight += value
            weighted_u += value * col
            weighted_v += value * row
            pixels.append((col, row))
    if count < model.min_marker_pixels:
        raise DetectorNoResult(f"only {count} marker pixels (need {model.min_marker_pixels})")

    center_u = sum_u / count
    center_v = sum_v / count
    if weight <= 0:
        raise DetectorNoResult("marker pixels carry no intensity")
    moment_u = weighted_u / weight
    moment_v = weighted_v / weight
    delta_u = moment_u - center_u
    delta_v = moment_v - center_v
    if math.hypot(delta_u, delta_v) < model.min_yaw_offset_px:
        raise DetectorNoResult("marker yaw direction is unresolved")
    theta = math.atan2(delta_v, delta_u)
    yaw_rad = theta / model.yaw_image_sign

    radius_area = math.sqrt(count / math.pi)
    max_distance_sq = 0.0
    for col, row in pixels:
        distance_sq = (col - center_u) ** 2 + (row - center_v) ** 2
        if distance_sq > max_distance_sq:
            max_distance_sq = distance_sq
    radius_extent = math.sqrt(max_distance_sq)
    reprojection_error_px = abs(radius_area - radius_extent)
    if radius_area < 1.0:
        raise DetectorNoResult("marker radius is too small to localize")

    depth = model.focal_px * model.marker_radius_m / radius_area
    x_m = (center_v - model.principal_y) * depth / model.focal_px
    y_m = (center_u - model.principal_x) * depth / model.focal_px
    marker_height = model.camera_height_m - depth
    z_m = -marker_height
    confidence = min(1.0, count / 64.0)
    return PoseObservation(
        sequence=sequence,
        received_monotonic=frame.decode_complete_monotonic,
        source_timestamp_monotonic=frame.received_monotonic,
        x_m=x_m,
        y_m=y_m,
        z_m=z_m,
        yaw_rad=yaw_rad,
        marker_count=1,
        reprojection_error_px=reprojection_error_px,
        confidence=confidence,
        world_frame=WORLD_FRAME,
        body_frame=BODY_FRAME,
    )


class SimulatedDownwardCamera:
    """Latest-value simulation frame source that owns no decoder I/O."""

    def __init__(
        self,
        model: Optional[SimCameraModel] = None,
        *,
        clock: Callable[[], float] = lambda: 0.0,
        generation: int = 0,
    ) -> None:
        self.model = model or SimCameraModel()
        if type(generation) is not int or generation < 0:
            raise ValueError("generation must be a non-negative integer")
        self._clock = clock
        self._generation = generation
        self._frame_id = 0

    @property
    def generation(self) -> int:
        return self._generation

    def next_frame(self, telemetry: SilsTelemetry, *, now: Optional[float] = None) -> DecodedFrame:
        self._frame_id += 1
        timestamp = self._clock() if now is None else _finite("now", now)
        return render_marker_frame(
            telemetry,
            self.model,
            frame_id=self._frame_id,
            generation=self._generation,
            received_monotonic=timestamp,
            decode_complete_monotonic=timestamp,
        )


# ---------------------------------------------------------------------------
# Outer position controller
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OuterControllerConfig:
    """Simulation-only bounded outer position controller configuration."""

    kp_xy: float = 1.0
    kp_yaw: float = 0.5
    max_output: float = 0.25
    slew_rate_per_second: float = 2.0
    intent_ttl_seconds: float = 0.2
    zero_ttl_seconds: float = 0.2
    target_x_m: float = 0.05
    target_y_m: float = 0.0
    target_yaw_rad: float = 0.0
    max_abs_x_m: float = 1.0
    max_abs_y_m: float = 1.0
    min_height_m: float = 0.0
    max_height_m: float = 3.0

    def __post_init__(self) -> None:
        for name in (
            "kp_xy",
            "kp_yaw",
            "max_output",
            "slew_rate_per_second",
            "intent_ttl_seconds",
            "zero_ttl_seconds",
            "max_abs_x_m",
            "max_abs_y_m",
            "max_height_m",
        ):
            if _finite(name, getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.max_output > 1.0:
            raise ValueError("max_output must be at most 1")
        _finite("target_x_m", self.target_x_m)
        _finite("target_y_m", self.target_y_m)
        _finite("target_yaw_rad", self.target_yaw_rad)
        _finite("min_height_m", self.min_height_m)
        if self.min_height_m < 0 or self.min_height_m >= self.max_height_m:
            raise ValueError("height bounds are invalid")


class OuterPositionController:
    """Pure bounded outer position controller producing expiring intents.

    The controller consumes only a valid ``PoseObservation``.  It computes the
    world error, rotates it into ``body_frd`` with the observed yaw, and emits a
    saturated, slew-limited, zero-throttle ``ControlIntent``.  It never touches
    a transport and never extends an existing intent.
    """

    def __init__(self, config: Optional[OuterControllerConfig] = None) -> None:
        self.config = config or OuterControllerConfig()
        self._last_roll = 0.0
        self._last_pitch = 0.0
        self._last_yaw = 0.0
        self._last_time: Optional[float] = None
        self._last_saturated = False

    @property
    def last_saturated(self) -> bool:
        return self._last_saturated

    def reset(self) -> None:
        self._last_roll = 0.0
        self._last_pitch = 0.0
        self._last_yaw = 0.0
        self._last_time = None
        self._last_saturated = False

    def _slew(self, previous: float, target: float, *, now: float) -> float:
        if self._last_time is None:
            return target
        elapsed = now - self._last_time
        if elapsed < 0:
            elapsed = 0.0
        limit = self.config.slew_rate_per_second * elapsed
        delta = target - previous
        return previous + max(-limit, min(limit, delta))

    def step(
        self,
        observation: PoseObservation,
        *,
        now: float,
        sequence: int,
    ) -> Optional[ControlIntent]:
        if not isinstance(observation, PoseObservation):
            raise TypeError("observation must be a PoseObservation")
        current = _finite("now", now)
        config = self.config
        if observation.x_m < -config.max_abs_x_m or observation.x_m > config.max_abs_x_m:
            self._last_saturated = False
            return None
        if observation.y_m < -config.max_abs_y_m or observation.y_m > config.max_abs_y_m:
            self._last_saturated = False
            return None
        height = observation.height_above_world_origin_m
        if height < config.min_height_m or height > config.max_height_m:
            self._last_saturated = False
            return None

        error_x = config.target_x_m - observation.x_m
        error_y = config.target_y_m - observation.y_m
        cos_yaw = math.cos(observation.yaw_rad)
        sin_yaw = math.sin(observation.yaw_rad)
        # Express the world error in body_frd: multiply by R(-yaw).
        body_x = cos_yaw * error_x + sin_yaw * error_y
        body_y = -sin_yaw * error_x + cos_yaw * error_y

        roll_target = config.kp_xy * body_y
        pitch_target = config.kp_xy * body_x
        yaw_target = config.kp_yaw * wrap_angle(config.target_yaw_rad - observation.yaw_rad)

        raw = (roll_target, pitch_target, yaw_target)
        saturated = any(abs(value) > config.max_output for value in raw)
        roll_target = max(-config.max_output, min(config.max_output, roll_target))
        pitch_target = max(-config.max_output, min(config.max_output, pitch_target))
        yaw_target = max(-config.max_output, min(config.max_output, yaw_target))

        roll = self._slew(self._last_roll, roll_target, now=current)
        pitch = self._slew(self._last_pitch, pitch_target, now=current)
        yaw = self._slew(self._last_yaw, yaw_target, now=current)

        self._last_roll = roll
        self._last_pitch = pitch
        self._last_yaw = yaw
        self._last_time = current
        self._last_saturated = saturated

        return ControlIntent(
            sequence=sequence,
            generated_monotonic=current,
            valid_until_monotonic=current + config.intent_ttl_seconds,
            roll=roll,
            pitch=pitch,
            yaw=yaw,
            throttle=0.0,
        )


def zero_intent(*, sequence: int, now: float, ttl: float) -> ControlIntent:
    return ControlIntent.zero(sequence=sequence, now=now, ttl=ttl)


# ---------------------------------------------------------------------------
# Runtime loop
# ---------------------------------------------------------------------------


class SimStateSource(Protocol):
    @property
    def is_ready(self) -> bool: ...

    @property
    def clock(self) -> Callable[[], float]: ...

    def read_telemetry(self, *, timeout: Optional[float] = None) -> SilsTelemetry: ...


def _coerce_decision(decision: Optional[VisionDecision]) -> tuple[bool, tuple[str, ...]]:
    if decision is None:
        return False, ("no_observation",)
    return decision.valid, tuple(decision.reasons)


def _merge_health(
    telemetry: SilsTelemetry,
    decision: Optional[VisionDecision],
    *,
    now: float,
    transport_ready: bool,
) -> HealthSnapshot:
    base = telemetry_to_health(
        telemetry,
        now_monotonic=now,
        transport_ready=transport_ready,
        calibration_valid=False,
        capabilities=frozenset(),
        armed=False,
    )
    observation_valid, _reasons = _coerce_decision(decision)
    return replace(
        base,
        observation_valid=observation_valid,
        observation_age_seconds=(decision.receive_age_seconds if decision is not None else None),
    )


@dataclass(frozen=True)
class LoopIteration:
    iteration: int
    generation: int
    frame_sequence: int
    perception_sequence: int
    command_sequence: int
    wire_sequence: Optional[int]
    simulator_time_before: float
    simulator_time_after: float
    receive_sequence_before: int
    receive_sequence_after: int
    frame_received_monotonic: Optional[float]
    frame_decode_complete_monotonic: Optional[float]
    frame_fingerprint: Optional[str]
    pose_received_monotonic: Optional[float]
    pose_source_monotonic: Optional[float]
    pose_x_m: Optional[float]
    pose_y_m: Optional[float]
    pose_z_m: Optional[float]
    pose_yaw_rad: Optional[float]
    pose_age_seconds: Optional[float]
    observation_valid: bool
    observation_reasons: tuple[str, ...]
    intent_generated_monotonic: float
    intent_valid_until_monotonic: float
    intent_roll: float
    intent_pitch: float
    intent_yaw: float
    intent_throttle: float
    controller_saturated: bool
    safe_zero: bool
    safe_reason: Optional[str]
    mission_state: str
    health_telemetry_valid: bool
    health_observation_valid: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "iteration": self.iteration,
            "generation": self.generation,
            "simulator_t": self.simulator_time_before,
            "emulator_receive_sequence": self.receive_sequence_before,
            "frame_sequence": self.frame_sequence,
            "frame_generated_from_sim_t": self.simulator_time_before,
            "perception_sequence": self.perception_sequence,
            "pose": {
                "x_m": self.pose_x_m,
                "y_m": self.pose_y_m,
                "z_m": self.pose_z_m,
                "yaw_rad": self.pose_yaw_rad,
            },
            "pose_age": self.pose_age_seconds,
            "pose_valid": self.observation_valid,
            "controller_decision": "safe_zero" if self.safe_zero else "control_intent",
            "command": {
                "intent_sequence": self.command_sequence,
                "wire_sequence": self.wire_sequence,
                "roll": self.intent_roll,
                "pitch": self.intent_pitch,
                "yaw": self.intent_yaw,
                "throttle": self.intent_throttle,
            },
            "command_sequence": self.command_sequence,
            "wire_sequence": self.wire_sequence,
            "simulator_time_before": self.simulator_time_before,
            "simulator_time_after": self.simulator_time_after,
            "next_simulator_t": self.simulator_time_after,
            "receive_sequence_before": self.receive_sequence_before,
            "receive_sequence_after": self.receive_sequence_after,
            "next_emulator_receive_sequence": self.receive_sequence_after,
            "frame_received_monotonic": self.frame_received_monotonic,
            "frame_decode_complete_monotonic": self.frame_decode_complete_monotonic,
            "frame_fingerprint": self.frame_fingerprint,
            "pose_received_monotonic": self.pose_received_monotonic,
            "pose_source_monotonic": self.pose_source_monotonic,
            "pose_x_m": self.pose_x_m,
            "pose_y_m": self.pose_y_m,
            "pose_z_m": self.pose_z_m,
            "pose_yaw_rad": self.pose_yaw_rad,
            "pose_age_seconds": self.pose_age_seconds,
            "observation_valid": self.observation_valid,
            "observation_reasons": list(self.observation_reasons),
            "intent_generated_monotonic": self.intent_generated_monotonic,
            "intent_valid_until_monotonic": self.intent_valid_until_monotonic,
            "intent": {
                "generated_at": self.intent_generated_monotonic,
                "valid_until": self.intent_valid_until_monotonic,
            },
            "intent_roll": self.intent_roll,
            "intent_pitch": self.intent_pitch,
            "intent_yaw": self.intent_yaw,
            "intent_throttle": self.intent_throttle,
            "controller_saturated": self.controller_saturated,
            "safe_zero": self.safe_zero,
            "safe_reason": self.safe_reason,
            "fault_drop_reason": self.safe_reason,
            "mission_state": self.mission_state,
            "health_telemetry_valid": self.health_telemetry_valid,
            "health_observation_valid": self.health_observation_valid,
        }


@dataclass(frozen=True)
class SimCameraLoopResult:
    schema_version: int
    milestone: str
    provider: str
    evidence_kind: str
    simulation: bool
    flight_qualified: bool
    backend: str
    iterations: tuple[LoopIteration, ...]
    faults: tuple[str, ...]
    final_state: str
    controller_saturated: bool = False

    def _iteration_dicts(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for index, iteration in enumerate(self.iterations):
            row = iteration.to_dict()
            next_iteration = self.iterations[index + 1] if index + 1 < len(self.iterations) else None
            row["next_frame_sequence"] = (
                next_iteration.frame_sequence if next_iteration is not None else None
            )
            row["next_perception_sequence"] = (
                next_iteration.perception_sequence if next_iteration is not None else None
            )
            row["provenance"] = {
                "provider": self.provider,
                "evidence_kind": self.evidence_kind,
                "simulation": self.simulation,
                "flight_qualified": self.flight_qualified,
                "backend": self.backend,
            }
            rows.append(row)
        return rows

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "milestone": self.milestone,
            "provider": self.provider,
            "evidence_kind": self.evidence_kind,
            "simulation": self.simulation,
            "flight_qualified": self.flight_qualified,
            "backend": self.backend,
            "iterations": self._iteration_dicts(),
            "faults": list(self.faults),
            "final_state": self.final_state,
            "controller_saturated": self.controller_saturated,
        }

    def to_jsonl(self) -> str:
        return "\n".join(
            json.dumps(iteration, sort_keys=True, separators=(",", ":"))
            for iteration in self._iteration_dicts()
        )


class SimVisionControlRuntime:
    """Run the narrow simulation-only STATE -> pixels -> pose -> command loop."""

    def __init__(
        self,
        *,
        state_source: SimStateSource,
        scheduler: ControlScheduler,
        camera_model: Optional[SimCameraModel] = None,
        controller: Optional[OuterPositionController] = None,
        gate_config: Optional[VisionGateConfig] = None,
        frame_source: Optional[Callable[[SilsTelemetry, float], Optional[DecodedFrame]]] = None,
        clock: Optional[Callable[[], float]] = None,
        max_iterations: int = 4,
        mission_config: Optional[MissionConfig] = None,
    ) -> None:
        if type(max_iterations) is not int or max_iterations <= 0:
            raise ValueError("max_iterations must be a positive integer")
        self.state_source = state_source
        self.scheduler = scheduler
        self.camera_model = camera_model or SimCameraModel()
        self.controller = controller or OuterPositionController()
        self.gate_config = gate_config or VisionGateConfig(
            max_receive_age_seconds=0.5,
            max_source_age_seconds=0.5,
            max_reprojection_error_px=5.0,
            min_marker_count=1,
            min_confidence=0.5,
            max_abs_x_m=1.0,
            max_abs_y_m=1.0,
            min_height_m=0.0,
            max_height_m=3.0,
        )
        self._clock = clock or state_source.clock
        camera = SimulatedDownwardCamera(self.camera_model, clock=self._clock)
        self.frame_source = frame_source or (lambda telemetry, now: camera.next_frame(telemetry, now=now))
        self.max_iterations = max_iterations
        self.mission_config = mission_config or MissionConfig()
        self._fault: Optional[str] = None
        self._frame_sequence = 0
        self._perception_sequence = 0
        self._command_sequence = 0
        self._last_frame_id = 0
        self._generation: Optional[int] = None

    @property
    def fault_reason(self) -> Optional[str]:
        return self._fault

    def _latch(self, reason: str) -> None:
        if self._fault is None:
            self._fault = reason
        if self.scheduler.state is SchedulerState.READY:
            self.scheduler.fault(reason)
        if self.scheduler.state is SchedulerState.READY:
            self.scheduler.request_stop()

    def _health(
        self,
        telemetry: SilsTelemetry,
        decision: Optional[VisionDecision],
        *,
        now: float,
    ) -> HealthSnapshot:
        return _merge_health(
            telemetry,
            decision,
            now=now,
            transport_ready=self.state_source.is_ready,
        )

    def _iteration(
        self,
        index: int,
        state: SilsTelemetry,
        next_state: SilsTelemetry,
        frame: Optional[DecodedFrame],
        observation: Optional[PoseObservation],
        decision: Optional[VisionDecision],
        intent: ControlIntent,
        *,
        wire_sequence: Optional[int],
        safe_reason: Optional[str],
        mission_state: str,
        health: HealthSnapshot,
    ) -> LoopIteration:
        observation_valid, observation_reasons = _coerce_decision(decision)
        saturated = (
            bool(observation_valid and decision is not None and decision.valid)
            and self.controller.last_saturated
        )
        return LoopIteration(
            iteration=index,
            generation=self._generation if self._generation is not None else 0,
            frame_sequence=frame.frame_id if frame is not None else 0,
            perception_sequence=self._perception_sequence,
            command_sequence=intent.sequence,
            wire_sequence=wire_sequence,
            simulator_time_before=state.sim_time,
            simulator_time_after=next_state.sim_time,
            receive_sequence_before=state.receive_sequence,
            receive_sequence_after=next_state.receive_sequence,
            frame_received_monotonic=frame.received_monotonic if frame is not None else None,
            frame_decode_complete_monotonic=frame.decode_complete_monotonic if frame is not None else None,
            frame_fingerprint=(
                hashlib.sha256(frame.data).hexdigest()[:16] if frame is not None else None
            ),
            pose_received_monotonic=observation.received_monotonic if observation is not None else None,
            pose_source_monotonic=observation.source_timestamp_monotonic if observation is not None else None,
            pose_x_m=observation.x_m if observation is not None else None,
            pose_y_m=observation.y_m if observation is not None else None,
            pose_z_m=observation.z_m if observation is not None else None,
            pose_yaw_rad=observation.yaw_rad if observation is not None else None,
            pose_age_seconds=(decision.receive_age_seconds if decision is not None else None),
            observation_valid=observation_valid,
            observation_reasons=observation_reasons,
            intent_generated_monotonic=intent.generated_monotonic,
            intent_valid_until_monotonic=intent.valid_until_monotonic,
            intent_roll=intent.roll,
            intent_pitch=intent.pitch,
            intent_yaw=intent.yaw,
            intent_throttle=intent.throttle,
            controller_saturated=saturated,
            safe_zero=safe_reason is not None,
            safe_reason=safe_reason,
            mission_state=mission_state,
            health_telemetry_valid=health.telemetry_valid,
            health_observation_valid=health.observation_valid,
        )

    def _result(self, iterations: list[LoopIteration], faults: list[str], final_state: str) -> SimCameraLoopResult:
        return SimCameraLoopResult(
            schema_version=SCHEMA_VERSION,
            milestone=MILESTONE,
            provider=SIM_PROVIDER,
            evidence_kind=SIM_EVIDENCE_KIND,
            simulation=True,
            flight_qualified=False,
            backend=SIM_BACKEND,
            iterations=tuple(iterations),
            faults=tuple(faults),
            final_state=final_state,
            controller_saturated=self.controller.last_saturated,
        )

    def _publish_zero(self, now: float) -> ControlIntent:
        self._command_sequence += 1
        return zero_intent(
            sequence=self._command_sequence,
            now=now,
            ttl=self.controller.config.zero_ttl_seconds,
        )

    def run(self) -> SimCameraLoopResult:
        iterations: list[LoopIteration] = []
        faults: list[str] = []
        mission = MissionSupervisor(config=self.mission_config)
        mission_event = OperatorEvent.START
        final_state = "RUNNING"

        if self.scheduler.state is not SchedulerState.READY:
            reason = f"scheduler_not_ready:{self.scheduler.state.value}"
            return self._result(iterations, [reason], "FAULT")

        try:
            state = self.state_source.read_telemetry()
        except StampFlySilsError as exc:
            reason = f"state_initial:{type(exc).__name__}"
            self._latch(reason)
            return self._result(iterations, [reason], "FAULT")

        for index in range(self.max_iterations):
            frame: Optional[DecodedFrame] = None
            observation: Optional[PoseObservation] = None
            decision: Optional[VisionDecision] = None
            intent: Optional[ControlIntent] = None
            safe_reason: Optional[str] = None

            now_capture = self._clock()
            try:
                frame = self.frame_source(state, now_capture)
            except SimCameraError as exc:
                if isinstance(exc, CameraStalled):
                    safe_reason = f"camera_stall:{exc}"
                else:
                    safe_reason = f"frame:{type(exc).__name__}:{exc}"
            if frame is None and safe_reason is None:
                safe_reason = "frame_missing"

            if frame is not None:
                if frame.frame_id <= self._last_frame_id:
                    reason = f"frame_sequence_rollback:{frame.frame_id}<={self._last_frame_id}"
                    self._latch(reason)
                    faults.append(reason)
                    final_state = "FAULT"
                    break
                if self._generation is None:
                    self._generation = frame.decoder_generation
                elif frame.decoder_generation != self._generation:
                    reason = f"frame_generation_changed:{self._generation}->{frame.decoder_generation}"
                    self._latch(reason)
                    faults.append(reason)
                    final_state = "FAULT"
                    break
                self._last_frame_id = frame.frame_id
                self._frame_sequence += 1
                self._perception_sequence += 1
                try:
                    observation = detect_pose(frame, self.camera_model, sequence=self._perception_sequence)
                except SimCameraError as exc:
                    observation = None
                    safe_reason = f"perception:{type(exc).__name__}:{exc}"

            if observation is not None:
                decision = evaluate_observation(
                    observation,
                    now_monotonic=self._clock(),
                    config=self.gate_config,
                )
                if decision.valid:
                    self._command_sequence += 1
                    intent = self.controller.step(
                        observation,
                        now=self._clock(),
                        sequence=self._command_sequence,
                    )
                    if intent is None:
                        safe_reason = "controller_no_intent"
                else:
                    safe_reason = "vision:" + ",".join(decision.reasons)

            if intent is None:
                if self.scheduler.state is not SchedulerState.READY:
                    reason = f"scheduler_not_ready_midloop:{self.scheduler.state.value}"
                    faults.append(reason)
                    final_state = "FAULT"
                    break
                if safe_reason is None:
                    safe_reason = "no_valid_intent"
                intent = self._publish_zero(self._clock())

            try:
                self.scheduler.publish(intent)
            except RuntimeError as exc:
                reason = f"publish_rejected:{exc}"
                faults.append(reason)
                final_state = "FAULT"
                break
            tick = self.scheduler.tick(now=self._clock())
            if tick.state is SchedulerState.FAULT:
                reason = tick.fault_reason or "scheduler_fault"
                self._latch(reason)
                faults.append(reason)
                final_state = "FAULT"
                break

            health = self._health(state, decision, now=self._clock())
            transition = mission.step(mission_event, health, self._clock())
            mission_event = OperatorEvent.NONE
            if transition.action.kind not in _SAFE_MISSION_ACTIONS:
                reason = f"mission_action:{transition.action.kind.value}"
                self._latch(reason)
                faults.append(reason)
                final_state = "FAULT"
                break

            try:
                next_state = self.state_source.read_telemetry()
            except StampFlySilsError as exc:
                reason = f"state_next:{type(exc).__name__}:{exc}"
                self._latch(reason)
                faults.append(reason)
                final_state = "FAULT"
                break

            iterations.append(
                self._iteration(
                    index + 1,
                    state,
                    next_state,
                    frame,
                    observation,
                    decision,
                    intent,
                    wire_sequence=tick.sequence,
                    safe_reason=safe_reason,
                    mission_state=transition.context.state.value,
                    health=health,
                )
            )
            state = next_state

        if final_state == "RUNNING":
            final_state = "STOPPED"
        return self._result(iterations, faults, final_state)


# ---------------------------------------------------------------------------
# Real installed SILS entrypoint
# ---------------------------------------------------------------------------


def run_sils_camera_smoke(
    transport: StampFlySimTransport,
    *,
    max_iterations: int = 4,
    camera_model: Optional[SimCameraModel] = None,
    controller: Optional[OuterPositionController] = None,
) -> SimCameraLoopResult:
    """Drive the genuine loop over an already-resolved SILS transport.

    The caller must not have started ``transport``; this function sends the
    mandatory first non-arming safe center frame via ``transport.start()`` and
    always closes the process afterwards.
    """

    if not isinstance(transport, StampFlySimTransport):
        raise TypeError("transport must be a StampFlySimTransport")
    resolved_controller = controller or OuterPositionController()
    adapter = SilsControlAdapter(transport, max_stick=resolved_controller.config.max_output)
    scheduler = ControlScheduler(adapter, monotonic_clock=transport.clock)
    runtime = SimVisionControlRuntime(
        state_source=transport,
        scheduler=scheduler,
        camera_model=camera_model,
        controller=resolved_controller,
        clock=transport.clock,
        max_iterations=max_iterations,
    )
    transport.start()
    try:
        return runtime.run()
    finally:
        transport.close()


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    launcher: Optional[object] = None,
    root_resolver: Callable[..., object] = resolve_sils_root,
    source_marker_probe: Optional[Callable[[str], None]] = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="sim_camera_perception",
        description="simulation-only camera/perception closed loop over the SILS emu_vehicle",
    )
    parser.add_argument("--root", "--ecosystem-root", dest="ecosystem_root", default=None)
    parser.add_argument("--json", action="store_true", help="print full JSON evidence")
    parser.add_argument("--jsonl", type=Path, default=None, help="also write bounded per-iteration JSONL")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("resolve", help="resolve the SILS root read-only")
    smoke = subparsers.add_parser("camera-smoke", help="run the bounded simulation-only camera loop")
    smoke.add_argument("--iterations", type=int, default=4)
    smoke.add_argument("--duration", type=float, default=DEFAULT_DURATION_SECONDS)

    args = parser.parse_args(argv)
    transport_kwargs: dict[str, object] = {
        "ecosystem_root": args.ecosystem_root,
        "root_resolver": root_resolver,
    }
    if launcher is not None:
        transport_kwargs["launcher"] = launcher
    if source_marker_probe is not None:
        transport_kwargs["source_marker_probe"] = source_marker_probe
    if getattr(args, "duration", None) is not None:
        transport_kwargs["duration_seconds"] = args.duration
    transport = StampFlySimTransport(**transport_kwargs)  # type: ignore[arg-type]

    if args.command in (None, "resolve"):
        diagnostics = transport.diagnostics()
        print(json.dumps(diagnostics, sort_keys=True))
        return 0 if diagnostics.get("found") else 2

    try:
        result = run_sils_camera_smoke(transport, max_iterations=args.iterations)
    except StampFlySilsError as exc:
        print(f"sim_camera_perception error: {exc}", file=sys.stderr)
        return 2

    payload = result.to_dict()
    if args.jsonl is not None:
        args.jsonl.parent.mkdir(parents=True, exist_ok=True)
        args.jsonl.write_text(result.to_jsonl() + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            json.dumps(
                {
                    "provider": result.provider,
                    "evidence_kind": result.evidence_kind,
                    "simulation": result.simulation,
                    "flight_qualified": result.flight_qualified,
                    "milestone": result.milestone,
                    "iterations": len(result.iterations),
                    "final_state": result.final_state,
                    "faults": list(result.faults),
                },
                sort_keys=True,
            )
        )
    return 0 if result.final_state == "STOPPED" and not result.faults else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CameraStalled",
    "DetectorNoResult",
    "LoopIteration",
    "MalformedFrameError",
    "OuterControllerConfig",
    "OuterPositionController",
    "SIM_BACKEND",
    "SIM_CAMERA_FRAME",
    "SimCameraError",
    "SimCameraLoopResult",
    "SimCameraModel",
    "SimStateSource",
    "SimVisionControlRuntime",
    "SimulatedDownwardCamera",
    "VisionRuntimeFault",
    "detect_pose",
    "render_marker_frame",
    "run_sils_camera_smoke",
    "wrap_angle",
    "zero_intent",
]
