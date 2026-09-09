#!/usr/bin/env python3
"""Deadline-aware host control scheduling.

Only the scheduler/owner talks to a StampFly transport.  Producers publish an
immutable intent into a one-slot mailbox.  An expired intent is a fault, even
if the serial device is healthy; this prevents a transport thread from
turning an old non-zero command into an apparently healthy heartbeat.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import threading
import time
from typing import Callable, Optional, Protocol

try:
    from .stampfly import ALT_MODE_MANUAL, CONTROL_MODE_ANGLE, MAX_SEQUENCE, StampFlyError
except ImportError:  # pragma: no cover - exercised by the script entrypoint
    from stampfly import ALT_MODE_MANUAL, CONTROL_MODE_ANGLE, MAX_SEQUENCE, StampFlyError


class ControlTransport(Protocol):
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
    ) -> int: ...

    def best_effort_disarm(self) -> None: ...


@dataclass(frozen=True)
class ControlIntent:
    """A producer-owned command with a fixed validity deadline."""

    sequence: int
    generated_monotonic: float
    valid_until_monotonic: float
    roll: float
    pitch: float
    yaw: float
    throttle: float
    control_mode: int = CONTROL_MODE_ANGLE
    alt_mode: int = ALT_MODE_MANUAL

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or not 1 <= self.sequence <= MAX_SEQUENCE:
            raise ValueError("intent sequence must be an integer in [1, 2^32-1]")
        for name, value in (
            ("generated_monotonic", self.generated_monotonic),
            ("valid_until_monotonic", self.valid_until_monotonic),
            ("roll", self.roll),
            ("pitch", self.pitch),
            ("yaw", self.yaw),
            ("throttle", self.throttle),
        ):
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.valid_until_monotonic <= self.generated_monotonic:
            raise ValueError("intent validity must end after generation")
        for name, value in (("roll", self.roll), ("pitch", self.pitch), ("yaw", self.yaw)):
            if not -1.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [-1, 1]")
        if not 0.0 <= self.throttle <= 1.0:
            raise ValueError("throttle must be in [0, 1]")
        if self.control_mode not in (0, 1) or self.alt_mode not in (4, 5):
            raise ValueError("unsupported CF1 control mode")

    @classmethod
    def zero(cls, *, sequence: int, now: float, ttl: float = 0.2) -> "ControlIntent":
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("ttl must be finite and positive")
        return cls(sequence, now, now + ttl, 0.0, 0.0, 0.0, 0.0)


class IntentMailbox:
    """Bounded latest-value mailbox."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._intent: Optional[ControlIntent] = None

    def publish(self, intent: ControlIntent) -> None:
        with self._lock:
            self._intent = intent

    def latest(self) -> Optional[ControlIntent]:
        with self._lock:
            return self._intent

    def clear(self) -> None:
        with self._lock:
            self._intent = None


class SchedulerState(str, Enum):
    READY = "READY"
    FAULT = "FAULT"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class SchedulerTick:
    state: SchedulerState
    sent: bool
    sequence: Optional[int] = None
    fault_reason: Optional[str] = None


class ControlScheduler:
    """Single-owner, deterministic scheduler.

    ``tick`` is deliberately public so replay tests can drive the exact same
    deadline checks without sleeping.  Callers must invoke ``tick`` from the
    thread that owns the transport; no other class in this module accesses it.
    """

    def __init__(
        self,
        transport: ControlTransport,
        *,
        mailbox: IntentMailbox | None = None,
        local_watchdog_seconds: float = 0.2,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(local_watchdog_seconds) or not 0 < local_watchdog_seconds < 0.25:
            raise ValueError("local_watchdog_seconds must be in (0, 0.25)")
        self.transport = transport
        self.mailbox = mailbox or IntentMailbox()
        self.local_watchdog_seconds = local_watchdog_seconds
        self._clock = monotonic_clock
        self._state = SchedulerState.READY
        self._fault_reason: str | None = None
        self._last_sent_at: float | None = None
        self._last_intent_sequence: int | None = None
        self._disarm_sent = False
        self._stop_requested = threading.Event()

    @property
    def state(self) -> SchedulerState:
        return self._state

    @property
    def fault_reason(self) -> str | None:
        return self._fault_reason

    @property
    def last_sent_at(self) -> float | None:
        return self._last_sent_at

    def publish(self, intent: ControlIntent) -> None:
        if self._state is SchedulerState.FAULT:
            raise RuntimeError("control scheduler is fault-latched")
        if self._stop_requested.is_set() or self._state is SchedulerState.STOPPED:
            raise RuntimeError("control scheduler is stopping")
        self.mailbox.publish(intent)

    def fault(self, reason: str) -> SchedulerTick:
        if self._state is SchedulerState.STOPPED:
            return SchedulerTick(SchedulerState.STOPPED, False)
        self._latch_fault(reason)
        return SchedulerTick(SchedulerState.FAULT, False, fault_reason=reason)

    def tick(self, *, now: float | None = None, wait_ack: bool = False) -> SchedulerTick:
        current = self._clock() if now is None else now
        if not math.isfinite(current):
            return self.fault("clock_invalid")
        if self._state is SchedulerState.FAULT:
            return SchedulerTick(SchedulerState.FAULT, False, fault_reason=self._fault_reason)
        if self._state is SchedulerState.STOPPED:
            return SchedulerTick(SchedulerState.STOPPED, False)
        if self._stop_requested.is_set():
            # The owner will perform the final DISARM and publish STOPPED.
            # Keeping READY visible here prevents a stop caller from
            # mistaking a request for a completed shutdown.
            return SchedulerTick(SchedulerState.READY, False)

        intent = self.mailbox.latest()
        if intent is None:
            return self.fault("intent_missing")
        if intent.valid_until_monotonic <= current:
            return self.fault(f"intent_expired:{intent.sequence}")
        if intent.generated_monotonic > current:
            return self.fault(f"intent_from_future:{intent.sequence}")
        if self._last_sent_at is not None and current - self._last_sent_at > self.local_watchdog_seconds:
            return self.fault("local_watchdog_before_send")
        if self._last_intent_sequence is not None and intent.sequence < self._last_intent_sequence:
            return self.fault("intent_sequence_reversed")

        try:
            wire_sequence = self.transport.set_control(
                intent.roll,
                intent.pitch,
                intent.yaw,
                intent.throttle,
                control_mode=intent.control_mode,
                alt_mode=intent.alt_mode,
                wait_ack=wait_ack,
            )
        except (OSError, StampFlyError, RuntimeError) as exc:
            return self.fault(f"transport:{type(exc).__name__}:{exc}")
        self._last_sent_at = current
        self._last_intent_sequence = intent.sequence
        return SchedulerTick(SchedulerState.READY, True, wire_sequence)

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested.is_set()

    def request_stop(self) -> None:
        """Request owner-thread shutdown without performing transport I/O."""

        self._stop_requested.set()

    def stop(self) -> None:
        """Finalize shutdown from the transport-owning thread.

        The caller must be the thread that owns ``transport``.  The
        ``ControlLoop`` enforces this by waiting for its worker to finish
        before a fallback caller finalizes an already-ended worker.
        """

        self._stop_requested.set()
        if self._state is SchedulerState.STOPPED:
            return
        self.mailbox.clear()
        self._send_disarm_once()
        # Do not publish STOPPED until the best-effort DISARM attempt has
        # returned, including its handled exception path.
        self._state = SchedulerState.STOPPED

    def _latch_fault(self, reason: str) -> None:
        if self._state in {SchedulerState.FAULT, SchedulerState.STOPPED}:
            return
        self._state = SchedulerState.FAULT
        self._fault_reason = reason
        self.mailbox.clear()
        self._send_disarm_once()

    def _send_disarm_once(self) -> None:
        if not self._disarm_sent:
            self._disarm_sent = True
            try:
                self.transport.best_effort_disarm()
            except Exception:
                # DISARM is deliberately best effort.  The one-shot guard
                # remains consumed even when a transport implementation
                # raises an unexpected ordinary exception.
                pass


class ControlLoop:
    """Optional real-time wrapper around :class:`ControlScheduler`."""

    def __init__(
        self,
        scheduler: ControlScheduler,
        *,
        hz: float = 20.0,
        on_tick: Callable[[SchedulerTick], None] | None = None,
    ) -> None:
        if not math.isfinite(hz) or hz < 20.0:
            raise ValueError("hz must be finite and at least 20")
        self.scheduler = scheduler
        self.period = 1.0 / hz
        self.on_tick = on_tick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="stampfly-control-owner", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float | None = None) -> bool:
        if timeout is not None and (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError("timeout must be finite and non-negative or None")
        self.scheduler.request_stop()
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                # The worker still owns the transport.  Do not publish
                # STOPPED or issue a competing DISARM from this caller.
                return False
        self.scheduler.stop()
        return self.scheduler.state is SchedulerState.STOPPED

    def _run(self) -> None:
        next_tick = time.monotonic()
        try:
            while (
                not self._stop.is_set()
                and not self.scheduler.stop_requested
                and self.scheduler.state is SchedulerState.READY
            ):
                now = time.monotonic()
                if now < next_tick:
                    self._stop.wait(next_tick - now)
                    continue
                result = self.scheduler.tick(now=now)
                if self.on_tick is not None:
                    self.on_tick(result)
                next_tick += self.period
                if next_tick < time.monotonic() - self.period:
                    next_tick = time.monotonic()
        finally:
            if self._stop.is_set() or self.scheduler.stop_requested:
                self.scheduler.stop()


__all__ = [
    "ControlIntent",
    "ControlLoop",
    "ControlScheduler",
    "IntentMailbox",
    "SchedulerState",
    "SchedulerTick",
]
