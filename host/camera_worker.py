#!/usr/bin/env python3
"""Bounded, latest-value Atom Cam frame acquisition.

The worker owns all blocking camera I/O.  Consumers never wait for a request;
they inspect the most recently received frame and its age instead.  A single
slot is intentional: stale frames have no value to a control loop and an
unbounded queue would turn network delay into control delay.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time
from typing import Callable, Optional

try:  # Support both ``python host/foo.py`` and ``import host.foo``.
    from .atomcam import AtomCamFrame, AtomCamSource
except ImportError:  # pragma: no cover - exercised by the script entrypoint
    from atomcam import AtomCamFrame, AtomCamSource


@dataclass(frozen=True)
class FrameSnapshot:
    frame: Optional[AtomCamFrame]
    age_seconds: Optional[float]
    valid: bool
    error: Optional[str] = None


class LatestFrameSlot:
    """Thread-safe latest-frame slot with no history growth."""

    def __init__(self, *, monotonic_clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._frame: Optional[AtomCamFrame] = None
        self._last_error: Optional[str] = None
        self._monotonic = monotonic_clock

    def publish(self, frame: AtomCamFrame) -> None:
        with self._lock:
            self._frame = frame
            self._last_error = None

    def publish_error(self, error: BaseException | str) -> None:
        with self._lock:
            self._last_error = str(error)

    def snapshot(self, *, now: float | None = None, max_age: float | None = None) -> FrameSnapshot:
        if max_age is not None and max_age < 0:
            raise ValueError("max_age must be non-negative")
        current = self._monotonic() if now is None else now
        if not isinstance(current, (int, float)) or not math.isfinite(current):
            raise ValueError("now must be a finite number")
        with self._lock:
            frame = self._frame
            error = self._last_error
        if frame is None:
            return FrameSnapshot(None, None, False, error)
        age = max(0.0, current - frame.received_monotonic)
        valid = max_age is None or age <= max_age
        return FrameSnapshot(frame, age, valid, error)


class CameraWorker:
    """Fetch frames on a private thread and expose only the latest frame."""

    def __init__(
        self,
        source: AtomCamSource,
        *,
        slot: LatestFrameSlot | None = None,
        retry_initial_seconds: float = 0.1,
        retry_max_seconds: float = 2.0,
        monotonic_clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 < retry_initial_seconds <= retry_max_seconds:
            raise ValueError("retry backoff must be positive and ordered")
        self.source = source
        self.slot = slot or LatestFrameSlot(monotonic_clock=monotonic_clock)
        self.retry_initial_seconds = retry_initial_seconds
        self.retry_max_seconds = retry_max_seconds
        self._monotonic = monotonic_clock
        self._sleep = sleep
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._requests = 0
        self._errors = 0

    @property
    def requests(self) -> int:
        with self._lock:
            return self._requests

    @property
    def errors(self) -> int:
        with self._lock:
            return self._errors

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="atomcam-frame-worker",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout: float | None = None) -> None:
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def snapshot(self, *, now: float | None = None, max_age: float | None = None) -> FrameSnapshot:
        return self.slot.snapshot(now=now, max_age=max_age)

    def _run(self) -> None:
        backoff = self.retry_initial_seconds
        while not self._stop.is_set():
            try:
                with self._lock:
                    self._requests += 1
                frame = self.source.snapshot()
                self.slot.publish(frame)
                backoff = self.retry_initial_seconds
            except Exception as exc:  # keep camera faults out of the control owner
                with self._lock:
                    self._errors += 1
                self.slot.publish_error(exc)
                if self._stop.wait(backoff):
                    break
                backoff = min(self.retry_max_seconds, backoff * 2.0)


__all__ = ["CameraWorker", "FrameSnapshot", "LatestFrameSlot"]
