#!/usr/bin/env python3
"""Pure, latched mission supervisor for fake-input testing.

This module intentionally contains no serial, camera, sleep, or ARM adapter.
It returns semantic actions only.  A real adapter must be designed and
validated by the later flight-link/altitude issues before these actions can be
connected to hardware.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import math
from typing import FrozenSet


class MissionState(str, Enum):
    IDLE = "IDLE"
    PREFLIGHT = "PREFLIGHT"
    READY = "READY"
    ARMING = "ARMING"
    TAKING_OFF = "TAKING_OFF"
    HOLDING = "HOLDING"
    LANDING = "LANDING"
    COMPLETE = "COMPLETE"
    FAULT = "FAULT"


class OperatorEvent(str, Enum):
    NONE = "NONE"
    START = "START"
    LAND = "LAND"
    RESET = "RESET"
    EMERGENCY_STOP = "EMERGENCY_STOP"


class ActionKind(str, Enum):
    NONE = "NONE"
    BEGIN_PREFLIGHT = "BEGIN_PREFLIGHT"
    ARM = "ARM"
    TAKEOFF = "TAKEOFF"
    HOLD = "HOLD"
    LAND = "LAND"
    REQUEST_SAFE_RECOVERY = "REQUEST_SAFE_RECOVERY"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    COMPLETE = "COMPLETE"
    RESET = "RESET"


@dataclass(frozen=True)
class HealthSnapshot:
    calibration_valid: bool = False
    observation_valid: bool = False
    observation_age_seconds: float | None = None
    telemetry_valid: bool = False
    telemetry_age_seconds: float | None = None
    transport_ready: bool = False
    battery_ok: bool = False
    within_bounds: bool = False
    capabilities: FrozenSet[str] = field(default_factory=frozenset)
    armed: bool = False
    grounded: bool = True
    altitude_m: float | None = None
    takeoff_reached: bool = False

    def __post_init__(self) -> None:
        for name in (
            "calibration_valid",
            "observation_valid",
            "telemetry_valid",
            "transport_ready",
            "battery_ok",
            "within_bounds",
            "armed",
            "grounded",
            "takeoff_reached",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a bool")
        for name, value in (
            ("observation_age_seconds", self.observation_age_seconds),
            ("telemetry_age_seconds", self.telemetry_age_seconds),
            ("altitude_m", self.altitude_m),
        ):
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValueError(f"{name} must be finite or None")
            if name.endswith("age_seconds") and value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True)
class MissionConfig:
    observation_max_age_seconds: float = 0.25
    telemetry_max_age_seconds: float = 0.25
    preflight_timeout_seconds: float = 30.0
    arm_timeout_seconds: float = 5.0
    takeoff_timeout_seconds: float = 10.0
    hold_timeout_seconds: float = 60.0
    landing_timeout_seconds: float = 10.0
    target_altitude_m: float = 0.3
    altitude_tolerance_m: float = 0.08

    def __post_init__(self) -> None:
        for name in (
            "observation_max_age_seconds",
            "telemetry_max_age_seconds",
            "preflight_timeout_seconds",
            "arm_timeout_seconds",
            "takeoff_timeout_seconds",
            "hold_timeout_seconds",
            "landing_timeout_seconds",
            "target_altitude_m",
            "altitude_tolerance_m",
        ):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True)
class MissionContext:
    state: MissionState = MissionState.IDLE
    entered_at: float = 0.0
    next_action_id: int = 1
    fault_reason: str | None = None


@dataclass(frozen=True)
class MissionAction:
    kind: ActionKind
    action_id: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class MissionTransition:
    context: MissionContext
    action: MissionAction


def _action(context: MissionContext, kind: ActionKind, *, reason: str | None = None) -> tuple[MissionContext, MissionAction]:
    if kind in {ActionKind.NONE}:
        return context, MissionAction(kind, reason=reason)
    action = MissionAction(kind, context.next_action_id, reason)
    return replace(context, next_action_id=context.next_action_id + 1), action


def _enter(context: MissionContext, state: MissionState, now: float, *, reason: str | None = None) -> MissionContext:
    return replace(context, state=state, entered_at=now, fault_reason=reason)


def _healthy_for_flight(
    health: HealthSnapshot,
    config: MissionConfig,
    *,
    require_grounded: bool = False,
    require_armed: bool = False,
) -> tuple[bool, str | None]:
    if require_grounded and not health.grounded:
        return False, "not_grounded"
    if require_armed and not health.armed:
        return False, "not_armed"
    if not health.calibration_valid:
        return False, "uncalibrated"
    if not health.observation_valid or health.observation_age_seconds is None:
        return False, "observation_unknown"
    if health.observation_age_seconds > config.observation_max_age_seconds:
        return False, "observation_stale"
    if not health.telemetry_valid or health.telemetry_age_seconds is None:
        return False, "telemetry_unknown"
    if health.telemetry_age_seconds > config.telemetry_max_age_seconds:
        return False, "telemetry_stale"
    if not health.transport_ready:
        return False, "transport_not_ready"
    if not health.battery_ok:
        return False, "battery_not_ok"
    if not health.within_bounds:
        return False, "outside_bounds"
    return True, None


def step(
    context: MissionContext,
    operator_event: OperatorEvent,
    health: HealthSnapshot,
    now_monotonic: float,
    *,
    config: MissionConfig | None = None,
) -> MissionTransition:
    """Advance the deterministic mission FSM by one observation."""

    config = config or MissionConfig()
    if not isinstance(now_monotonic, (int, float)) or not math.isfinite(now_monotonic):
        raise ValueError("now_monotonic must be finite")
    if now_monotonic < context.entered_at:
        raise ValueError("now_monotonic cannot move backwards")
    if not isinstance(operator_event, OperatorEvent):
        operator_event = OperatorEvent(operator_event)

    if operator_event is OperatorEvent.EMERGENCY_STOP and context.state not in {
        MissionState.IDLE,
        MissionState.COMPLETE,
    }:
        faulted = _enter(context, MissionState.FAULT, now_monotonic, reason="operator_emergency_stop")
        updated, action = _action(faulted, ActionKind.EMERGENCY_STOP, reason="operator_emergency_stop")
        return MissionTransition(updated, action)

    if context.state is MissionState.IDLE:
        if operator_event is OperatorEvent.START:
            entered = _enter(context, MissionState.PREFLIGHT, now_monotonic)
            updated, action = _action(entered, ActionKind.BEGIN_PREFLIGHT)
            return MissionTransition(updated, action)
        return MissionTransition(context, MissionAction(ActionKind.NONE))

    if context.state is MissionState.PREFLIGHT:
        healthy, reason = _healthy_for_flight(health, config, require_grounded=True)
        if healthy:
            entered = _enter(context, MissionState.READY, now_monotonic)
            updated, action = _action(entered, ActionKind.NONE)
            return MissionTransition(updated, action)
        if now_monotonic - context.entered_at > config.preflight_timeout_seconds:
            faulted = _enter(context, MissionState.FAULT, now_monotonic, reason=reason or "preflight_timeout")
            updated, action = _action(faulted, ActionKind.REQUEST_SAFE_RECOVERY, reason=reason or "preflight_timeout")
            return MissionTransition(updated, action)
        return MissionTransition(context, MissionAction(ActionKind.NONE, reason=reason))

    if context.state is MissionState.READY:
        healthy, reason = _healthy_for_flight(health, config, require_grounded=True)
        if not healthy:
            faulted = _enter(context, MissionState.FAULT, now_monotonic, reason=reason or "ready_unhealthy")
            updated, action = _action(faulted, ActionKind.REQUEST_SAFE_RECOVERY, reason=reason or "ready_unhealthy")
            return MissionTransition(updated, action)
        if operator_event is OperatorEvent.START:
            entered = _enter(context, MissionState.ARMING, now_monotonic)
            updated, action = _action(entered, ActionKind.ARM)
            return MissionTransition(updated, action)
        return MissionTransition(context, MissionAction(ActionKind.NONE))

    if context.state is MissionState.ARMING:
        healthy, reason = _healthy_for_flight(health, config)
        if not healthy:
            faulted = _enter(context, MissionState.FAULT, now_monotonic, reason=reason or "arming_unhealthy")
            updated, action = _action(faulted, ActionKind.REQUEST_SAFE_RECOVERY, reason=reason or "arming_unhealthy")
            return MissionTransition(updated, action)
        if health.armed:
            entered = _enter(context, MissionState.TAKING_OFF, now_monotonic)
            updated, action = _action(entered, ActionKind.TAKEOFF)
            return MissionTransition(updated, action)
        if now_monotonic - context.entered_at > config.arm_timeout_seconds:
            faulted = _enter(context, MissionState.FAULT, now_monotonic, reason="arm_timeout")
            updated, action = _action(faulted, ActionKind.REQUEST_SAFE_RECOVERY, reason="arm_timeout")
            return MissionTransition(updated, action)
        return MissionTransition(context, MissionAction(ActionKind.NONE))

    if context.state is MissionState.TAKING_OFF:
        healthy, reason = _healthy_for_flight(health, config, require_armed=True)
        if not healthy:
            faulted = _enter(context, MissionState.FAULT, now_monotonic, reason=reason or "takeoff_unhealthy")
            updated, action = _action(faulted, ActionKind.REQUEST_SAFE_RECOVERY, reason=reason or "takeoff_unhealthy")
            return MissionTransition(updated, action)
        reached = health.takeoff_reached
        if health.altitude_m is not None:
            reached = reached or abs(health.altitude_m - config.target_altitude_m) <= config.altitude_tolerance_m
        if reached:
            entered = _enter(context, MissionState.HOLDING, now_monotonic)
            updated, action = _action(entered, ActionKind.HOLD)
            return MissionTransition(updated, action)
        if now_monotonic - context.entered_at > config.takeoff_timeout_seconds:
            faulted = _enter(context, MissionState.FAULT, now_monotonic, reason="takeoff_timeout")
            updated, action = _action(faulted, ActionKind.REQUEST_SAFE_RECOVERY, reason="takeoff_timeout")
            return MissionTransition(updated, action)
        return MissionTransition(context, MissionAction(ActionKind.NONE))

    if context.state is MissionState.HOLDING:
        healthy, reason = _healthy_for_flight(health, config, require_armed=True)
        if not healthy:
            faulted = _enter(context, MissionState.FAULT, now_monotonic, reason=reason or "holding_unhealthy")
            updated, action = _action(faulted, ActionKind.REQUEST_SAFE_RECOVERY, reason=reason or "holding_unhealthy")
            return MissionTransition(updated, action)
        if operator_event is OperatorEvent.LAND:
            entered = _enter(context, MissionState.LANDING, now_monotonic)
            updated, action = _action(entered, ActionKind.LAND)
            return MissionTransition(updated, action)
        if now_monotonic - context.entered_at > config.hold_timeout_seconds:
            faulted = _enter(context, MissionState.FAULT, now_monotonic, reason="hold_timeout")
            updated, action = _action(faulted, ActionKind.REQUEST_SAFE_RECOVERY, reason="hold_timeout")
            return MissionTransition(updated, action)
        return MissionTransition(context, MissionAction(ActionKind.NONE))

    if context.state is MissionState.LANDING:
        if not health.telemetry_valid or health.telemetry_age_seconds is None:
            reason = "telemetry_unknown"
        elif health.telemetry_age_seconds > config.telemetry_max_age_seconds:
            reason = "telemetry_stale"
        elif not health.transport_ready:
            reason = "transport_not_ready"
        else:
            reason = None
        if reason is not None:
            faulted = _enter(context, MissionState.FAULT, now_monotonic, reason=reason)
            updated, action = _action(faulted, ActionKind.REQUEST_SAFE_RECOVERY, reason=reason)
            return MissionTransition(updated, action)
        if health.grounded:
            entered = _enter(context, MissionState.COMPLETE, now_monotonic)
            updated, action = _action(entered, ActionKind.COMPLETE)
            return MissionTransition(updated, action)
        if now_monotonic - context.entered_at > config.landing_timeout_seconds:
            faulted = _enter(context, MissionState.FAULT, now_monotonic, reason="landing_timeout")
            updated, action = _action(faulted, ActionKind.REQUEST_SAFE_RECOVERY, reason="landing_timeout")
            return MissionTransition(updated, action)
        return MissionTransition(context, MissionAction(ActionKind.NONE))

    if context.state is MissionState.COMPLETE:
        if operator_event is OperatorEvent.RESET:
            reset = _enter(context, MissionState.IDLE, now_monotonic)
            updated, action = _action(reset, ActionKind.RESET)
            return MissionTransition(updated, action)
        return MissionTransition(context, MissionAction(ActionKind.NONE))

    if context.state is MissionState.FAULT:
        if operator_event is OperatorEvent.RESET:
            reset = _enter(context, MissionState.IDLE, now_monotonic)
            updated, action = _action(reset, ActionKind.RESET)
            return MissionTransition(updated, action)
        return MissionTransition(context, MissionAction(ActionKind.NONE, reason=context.fault_reason))

    raise AssertionError(f"unhandled mission state: {context.state}")


class MissionSupervisor:
    """Small stateful facade around the pure ``step`` function."""

    def __init__(self, *, config: MissionConfig | None = None) -> None:
        self.config = config or MissionConfig()
        self.context = MissionContext()

    def step(self, operator_event: OperatorEvent, health: HealthSnapshot, now_monotonic: float) -> MissionTransition:
        transition = step(
            self.context,
            operator_event,
            health,
            now_monotonic,
            config=self.config,
        )
        self.context = transition.context
        return transition


__all__ = [
    "ActionKind",
    "HealthSnapshot",
    "MissionAction",
    "MissionConfig",
    "MissionContext",
    "MissionState",
    "MissionSupervisor",
    "MissionTransition",
    "OperatorEvent",
    "step",
]
