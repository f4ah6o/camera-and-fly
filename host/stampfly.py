#!/usr/bin/env python3
"""Safe CF1 host-side protocol for an M5Stack StampFly.

This module deliberately has no implicit ARM path.  A caller must explicitly
invoke arm(), and the caller should keep a SET heartbeat running while the
aircraft is claimed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
import re
import threading
import time
from typing import Any, Callable, Optional

try:
    import serial
    from serial import SerialException
except ImportError:  # Replay and parser tests do not require pyserial.
    serial = None  # type: ignore[assignment]

    class SerialException(OSError):
        pass


CONTROL_MODE_ANGLE = 0
CONTROL_MODE_RATE = 1
ALT_MODE_AUTO = 4
ALT_MODE_MANUAL = 5
FIRMWARE_WATCHDOG_SECONDS = 0.250
DEFAULT_LOCAL_WATCHDOG_SECONDS = 0.200
MAX_SEQUENCE = 0xFFFFFFFF
MAX_RX_LINE_BYTES = 512
UNKNOWN_TELEMETRY_AGE_MS = 0xFFFFFFFF

_MODE_NAMES = {
    0: "INIT",
    1: "AVERAGE",
    2: "FLIGHT",
    3: "PARKING",
    4: "AUTO_LANDING",
    5: "FLIP",
}


class StampFlyError(RuntimeError):
    """Base class for host-side StampFly errors."""


class ProtocolError(StampFlyError):
    """The firmware rejected a command or returned an invalid response."""


class LocalWatchdogError(StampFlyError):
    """The host heartbeat was late and the host forced a best-effort DISARM."""


@dataclass(frozen=True)
class StampFlyStatus:
    claimed: bool
    armed: bool
    connected: bool
    mode: int
    voltage: float
    roll: float
    pitch: float
    yaw: float
    altitude: float
    range_mm: int
    altitude_m: float
    altitude_valid: bool
    altitude_age_ms: int
    altitude_source: str
    range_valid: bool
    range_age_ms: int
    range_source: str
    imu_valid: bool
    imu_age_ms: int
    imu_source: str
    capabilities: frozenset[str]
    safe_test: bool
    fields: dict[str, str]

    @property
    def mode_name(self) -> str:
        return _MODE_NAMES.get(self.mode, f"UNKNOWN({self.mode})")

    @property
    def telemetry_valid(self) -> bool:
        """Whether all required sensor observations are currently usable."""
        return (
            self.altitude_valid
            and self.range_valid
            and self.imu_valid
            and self.altitude_age_ms != UNKNOWN_TELEMETRY_AGE_MS
            and self.range_age_ms != UNKNOWN_TELEMETRY_AGE_MS
            and self.imu_age_ms != UNKNOWN_TELEMETRY_AGE_MS
        )

    @classmethod
    def parse(cls, line: str) -> "StampFlyStatus":
        prefix = "CF1 STATUS "
        if not line.startswith(prefix):
            raise ProtocolError(f"not a STATUS line: {line!r}")

        fields: dict[str, str] = {}
        for token in line[len(prefix):].split():
            if "=" not in token:
                raise ProtocolError(f"STATUS has a malformed token: {token!r}")
            key, value = token.split("=", 1)
            if not key or not value or key in fields:
                raise ProtocolError(f"STATUS has a malformed or duplicate field: {token!r}")
            fields[key] = value

        for name in (
            "claimed",
            "armed",
            "connected",
            "altitude_valid",
            "range_valid",
            "imu_valid",
            "safe_test",
        ):
            if name not in fields or fields[name] not in {"0", "1"}:
                raise ProtocolError(f"STATUS has invalid boolean {name}: {line!r}")

        def integer(name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
            raw = fields.get(name)
            if raw is None or not re.fullmatch(r"(?:0|[1-9][0-9]*)", raw):
                raise ProtocolError(f"STATUS missing or invalid integer {name}: {line!r}")
            try:
                value = int(raw)
            except ValueError as exc:
                raise ProtocolError(f"STATUS missing integer {name}: {line!r}") from exc
            if minimum is not None and value < minimum or maximum is not None and value > maximum:
                raise ProtocolError(f"STATUS integer {name} is out of range: {line!r}")
            return value

        def number(name: str) -> float:
            try:
                value = float(fields[name])
            except (KeyError, ValueError) as exc:
                raise ProtocolError(f"STATUS missing number {name}: {line!r}") from exc
            if not math.isfinite(value):
                raise ProtocolError(f"STATUS has non-finite {name}: {line!r}")
            return value

        mode = integer("mode", minimum=0, maximum=5)
        if mode not in _MODE_NAMES:
            raise ProtocolError(f"STATUS has unknown mode {mode}: {line!r}")

        def age(name: str) -> int:
            return integer(name, minimum=0, maximum=UNKNOWN_TELEMETRY_AGE_MS)

        def source(name: str) -> str:
            value = fields.get(name, "")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
                raise ProtocolError(f"STATUS has invalid source {name}: {line!r}")
            return value

        capabilities = frozenset(fields.get("capabilities", "").split(","))
        if "telemetry_validity_v1" not in capabilities:
            raise ProtocolError(f"STATUS lacks telemetry_validity_v1: {line!r}")

        return cls(
            claimed=fields.get("claimed") == "1",
            armed=fields.get("armed") == "1",
            connected=fields.get("connected") == "1",
            mode=integer("mode"),
            voltage=number("voltage"),
            roll=number("roll"),
            pitch=number("pitch"),
            yaw=number("yaw"),
            altitude=number("altitude"),
            range_mm=integer("range_mm", minimum=0),
            altitude_m=number("altitude_m"),
            altitude_valid=fields["altitude_valid"] == "1",
            altitude_age_ms=age("altitude_age_ms"),
            altitude_source=source("altitude_source"),
            range_valid=fields["range_valid"] == "1",
            range_age_ms=age("range_age_ms"),
            range_source=source("range_source"),
            imu_valid=fields["imu_valid"] == "1",
            imu_age_ms=age("imu_age_ms"),
            imu_source=source("imu_source"),
            capabilities=capabilities,
            safe_test=fields.get("safe_test") == "1",
            fields=fields,
        )


class StampFly:
    """Thread-safe, fail-closed CF1 client.

    The class opens exactly the port named by the caller.  Port discovery is
    intentionally outside this class so a caller cannot silently choose the
    wrong USB device when multiple devices are present.
    """

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = 115200,
        response_timeout: float = 0.15,
        local_watchdog_seconds: float = DEFAULT_LOCAL_WATCHDOG_SECONDS,
        serial_instance: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_rx_line_bytes: int = MAX_RX_LINE_BYTES,
    ) -> None:
        if not math.isfinite(response_timeout) or response_timeout <= 0:
            raise ValueError("response_timeout must be finite and positive")
        if not math.isfinite(local_watchdog_seconds) or not 0 < local_watchdog_seconds < FIRMWARE_WATCHDOG_SECONDS:
            raise ValueError(
                "local_watchdog_seconds must be positive and shorter than the "
                "firmware watchdog"
            )
        if type(max_rx_line_bytes) is not int or max_rx_line_bytes < 32:
            raise ValueError("max_rx_line_bytes must be an integer >= 32")

        self.port = port
        self.response_timeout = response_timeout
        self.local_watchdog_seconds = local_watchdog_seconds
        self._clock = clock
        self._sleep = sleep
        self._max_rx_line_bytes = max_rx_line_bytes
        if serial_instance is not None:
            self._serial = serial_instance
        else:
            if serial is None:
                raise StampFlyError("pyserial is required for a live StampFly connection")
            try:
                self._serial = serial.Serial(
                    port=port,
                    baudrate=baudrate,
                    timeout=0.02,
                    write_timeout=0.20,
                )
            except SerialException as exc:
                raise StampFlyError(f"cannot open StampFly serial port {port}: {exc}") from exc

        self._lock = threading.RLock()
        self._sequence = 1
        self._sequence_exhausted = False
        self._claimed = False
        self._armed = False
        self._closed = False
        self._last_set_at: Optional[float] = None
        self._setpoint = (0.0, 0.0, 0.0, 0.0, CONTROL_MODE_ANGLE, ALT_MODE_MANUAL)
        self._events: list[str] = []
        self._rx_buffer = bytearray()

    def __enter__(self) -> "StampFly":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    @property
    def claimed(self) -> bool:
        return self._claimed

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def last_set_age(self) -> Optional[float]:
        if self._last_set_at is None:
            return None
        return self._clock() - self._last_set_at

    def connect(self) -> str:
        """Perform HELLO and CLAIM.  CLAIM does not ARM."""
        hello = self.hello()
        self.claim()
        return hello

    def hello(self) -> str:
        return self._request(
            "CF1 HELLO",
            lambda line: line.startswith("CF1 HELLO "),
        )

    def claim(self) -> None:
        self._request("CF1 CLAIM", lambda line: line == "CF1 OK CLAIM")
        self._claimed = True
        self._armed = False
        self._last_set_at = None
        self._sequence = 1
        self._sequence_exhausted = False

    def release(self) -> None:
        """Explicitly DISARM and release the USB claim."""
        try:
            self.disarm()
        except StampFlyError:
            self.best_effort_disarm()
        self._request("CF1 RELEASE", lambda line: line == "CF1 OK RELEASE")
        self._claimed = False
        self._armed = False
        self._last_set_at = None

    def set_control(
        self,
        roll: float,
        pitch: float,
        yaw: float,
        throttle: float,
        *,
        control_mode: int = CONTROL_MODE_ANGLE,
        alt_mode: int = ALT_MODE_MANUAL,
        wait_ack: bool = True,
    ) -> int:
        """Send one sequence-numbered SET packet.

        SET is the heartbeat.  Keep the interval below 200 ms on the host;
        20 Hz is the intended initial rate.
        """
        self._validate_setpoint(roll, pitch, yaw, throttle, control_mode, alt_mode)
        if not self._claimed:
            raise ProtocolError("SET requires CLAIM")

        with self._lock:
            if self._sequence_exhausted or self._sequence > MAX_SEQUENCE:
                raise ProtocolError("SET sequence exhausted; CLAIM is required")
            sequence = self._sequence
            self._sequence_exhausted = sequence == MAX_SEQUENCE
            if not self._sequence_exhausted:
                self._sequence += 1
        self._setpoint = (roll, pitch, yaw, throttle, control_mode, alt_mode)
        command = (
            f"CF1 SET {sequence} {roll:.6f} {pitch:.6f} {yaw:.6f} "
            f"{throttle:.6f} {control_mode} {alt_mode}"
        )
        sent_at = self._clock()
        if wait_ack:
            self._request(command, lambda line: line == f"CF1 OK {sequence}")
        else:
            with self._lock:
                self._write_unlocked(command)
        self._last_set_at = sent_at
        return sequence

    def heartbeat(self, *, wait_ack: bool = False) -> int:
        """Repeat the current setpoint, initially a zero-control setpoint."""
        roll, pitch, yaw, throttle, control_mode, alt_mode = self._setpoint
        return self.set_control(
            roll,
            pitch,
            yaw,
            throttle,
            control_mode=control_mode,
            alt_mode=alt_mode,
            wait_ack=wait_ack,
        )

    def arm(self) -> None:
        """Explicit ARM only; this method is never called automatically."""
        if not self._claimed:
            raise ProtocolError("ARM requires CLAIM")
        age = self.last_set_age
        if age is None or age > FIRMWARE_WATCHDOG_SECONDS:
            raise ProtocolError("ARM requires a recent SET")
        if self._setpoint[3] > 0.05:
            raise ProtocolError("ARM requires throttle <= 0.05")
        self._request("CF1 ARM", lambda line: line == "CF1 OK ARM")
        self._armed = True

    def disarm(self) -> None:
        """Send a normal, acknowledged DISARM command."""
        self._request("CF1 DISARM", lambda line: line == "CF1 OK DISARM")
        self._armed = False

    def best_effort_disarm(self) -> None:
        """Write DISARM without waiting, for exception and signal paths."""
        with self._lock:
            if self._closed:
                return
            try:
                self._write_unlocked("CF1 DISARM")
            except (OSError, SerialException, StampFlyError):
                pass
            self._armed = False

    def watchdog_check(self) -> None:
        """Fail closed if the host heartbeat is late."""
        age = self.last_set_age
        if self._claimed and age is not None and age > self.local_watchdog_seconds:
            self.best_effort_disarm()
            raise LocalWatchdogError(
                f"host heartbeat age {age:.3f}s exceeded "
                f"{self.local_watchdog_seconds:.3f}s"
            )

    def status(self) -> StampFlyStatus:
        line = self._request(
            "CF1 STATUS",
            lambda value: value.startswith("CF1 STATUS "),
        )
        status = StampFlyStatus.parse(line)
        self._claimed = status.claimed
        self._armed = status.armed
        return status

    def drain_events(self) -> list[str]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
            return events

    def close(self) -> None:
        """Best-effort DISARM, then close the serial port."""
        with self._lock:
            if self._closed:
                return
            self.best_effort_disarm()
            try:
                self._serial.close()
            finally:
                self._closed = True

    @staticmethod
    def _validate_setpoint(
        roll: float,
        pitch: float,
        yaw: float,
        throttle: float,
        control_mode: int,
        alt_mode: int,
    ) -> None:
        for name, value in (("roll", roll), ("pitch", pitch), ("yaw", yaw)):
            if not math.isfinite(value) or not -1.0 <= value <= 1.0:
                raise ValueError(f"{name} must be finite and in [-1, 1]")
        if not math.isfinite(throttle) or not 0.0 <= throttle <= 1.0:
            raise ValueError("throttle must be finite and in [0, 1]")
        if control_mode not in (CONTROL_MODE_ANGLE, CONTROL_MODE_RATE):
            raise ValueError("control_mode must be ANGLECONTROL (0) or RATECONTROL (1)")
        if alt_mode not in (ALT_MODE_AUTO, ALT_MODE_MANUAL):
            raise ValueError("alt_mode must be AUTO_ALT (4) or MANUAL_ALT (5)")

    def _request(self, command: str, expected: Callable[[str], bool]) -> str:
        with self._lock:
            self._write_unlocked(command)
            deadline = self._clock() + self.response_timeout
            while self._clock() < deadline:
                line = self._read_line_until(deadline)
                if line is None:
                    break
                if line.startswith("CF1 EVENT "):
                    self._events.append(line)
                    if "DISARM" in line:
                        self._armed = False
                    continue
                if line.startswith("CF1 ERR "):
                    raise ProtocolError(line)
                if expected(line):
                    return line
                # Ignore delayed output from an earlier request.  It cannot
                # be treated as an acknowledgement for this request.
            raise ProtocolError(f"timeout waiting for response to {command!r}")

    def _read_line_until(self, deadline: float) -> Optional[str]:
        while self._clock() < deadline:
            newline = self._rx_buffer.find(b"\n")
            if newline >= 0:
                raw = bytes(self._rx_buffer[:newline])
                del self._rx_buffer[: newline + 1]
                return raw.decode("utf-8", "replace").rstrip("\r").strip()
            if len(self._rx_buffer) > self._max_rx_line_bytes:
                self._rx_buffer.clear()
                raise ProtocolError("received line exceeds bounded buffer")
            try:
                chunk = self._read_chunk()
            except (OSError, SerialException) as exc:
                raise StampFlyError(f"serial read failed: {exc}") from exc
            if chunk:
                self._rx_buffer.extend(chunk)
            else:
                self._sleep(min(0.001, max(0.0, deadline - self._clock())))
        return None

    def _read_chunk(self) -> bytes:
        """Read a bounded chunk while supporting small fake serial objects."""
        read = getattr(self._serial, "read", None)
        if callable(read):
            waiting = getattr(self._serial, "in_waiting", 0)
            if callable(waiting):
                waiting = waiting()
            size = min(64, max(1, int(waiting or 0)))
            raw = read(size)
        else:
            raw = self._serial.readline()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if not isinstance(raw, (bytes, bytearray)):
            raise StampFlyError("serial read returned a non-byte value")
        return bytes(raw)

    def _write_unlocked(self, command: str) -> None:
        if self._closed:
            raise StampFlyError("StampFly serial connection is closed")
        try:
            payload = (command + "\r\n").encode("ascii")
            written = self._serial.write(payload)
            if written is not None and written != len(payload):
                raise StampFlyError(
                    f"serial short write: expected {len(payload)} bytes, wrote {written}"
                )
            self._serial.flush()
        except (OSError, SerialException) as exc:
            raise StampFlyError(f"serial write failed: {exc}") from exc


def main() -> int:
    try:
        from .control_loop import ControlIntent, ControlScheduler, SchedulerState
    except ImportError:  # pragma: no cover - script entrypoint
        from control_loop import ControlIntent, ControlScheduler, SchedulerState

    parser = argparse.ArgumentParser(description="zero-control CF1 StampFly heartbeat")
    parser.add_argument("--port", required=True, help="explicit StampFly serial device")
    parser.add_argument("--hz", type=float, default=20.0)
    parser.add_argument("--duration", type=float, default=0.0, help="0 means until Ctrl-C")
    args = parser.parse_args()

    if args.hz < 20.0:
        parser.error("--hz must be at least 20 Hz")
    period = 1.0 / args.hz

    try:
        with StampFly(args.port) as stampfly:
            print(stampfly.connect(), flush=True)
            scheduler = ControlScheduler(stampfly, local_watchdog_seconds=DEFAULT_LOCAL_WATCHDOG_SECONDS)
            intent_sequence = 0
            started = time.monotonic()
            next_status = started
            while args.duration <= 0 or time.monotonic() - started < args.duration:
                cycle = time.monotonic()
                intent_sequence += 1
                scheduler.publish(ControlIntent.zero(sequence=intent_sequence, now=cycle))
                result = scheduler.tick(now=cycle)
                if result.state is SchedulerState.FAULT:
                    raise StampFlyError(result.fault_reason or "control scheduler fault")
                if cycle >= next_status:
                    print(stampfly.status(), flush=True)
                    next_status = cycle + 1.0
                time.sleep(max(0.0, period - (time.monotonic() - cycle)))
    except KeyboardInterrupt:
        return 130
    except StampFlyError as exc:
        print(f"stampfly error: {exc}", flush=False)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
