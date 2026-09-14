#!/usr/bin/env python3
"""Run the prop-off telemetry-validity hardware acceptance.

The helper has a deliberately narrow hardware surface: it claims the
explicitly supplied StampFly port, sends only zero-control SET heartbeats, and
polls the canonical STATUS parser while the operator changes the bottom ToF
target between the timed phases.
"""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import threading
import time
from typing import Callable

try:
    import serial
except ImportError:  # pragma: no cover - live hardware requires pyserial
    serial = None  # type: ignore[assignment]

try:
    from .stampfly import ProtocolError, StampFlyError, StampFlyStatus
except ImportError:  # pragma: no cover - script entrypoint
    from stampfly import ProtocolError, StampFlyError, StampFlyStatus


PHASES = ("normal", "tof_invalid", "recovery")
RESPONSE_TIMEOUT_SECONDS = 2.0
MAX_RX_LINE_BYTES = 2048


def _positive_seconds(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("seconds must be finite and positive")
    return parsed


def _write_record(handle, record: dict[str, object]) -> None:
    handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    handle.flush()


def _phase_prompt(phase: str) -> str:
    if phase == "normal":
        return "NORMAL: keep the bottom ToF unobstructed over a matte target."
    if phase == "tof_invalid":
        return "TOF_INVALID: cover or otherwise block the bottom ToF aperture."
    return "RECOVERY: uncover the bottom ToF and restore the same matte target."


class _LineReader:
    """Continuously drain USB replies so ACKs cannot back up the device."""

    def __init__(self, serial_port, *, max_line_bytes: int = MAX_RX_LINE_BYTES) -> None:
        self._serial = serial_port
        self._max_line_bytes = max_line_bytes
        self._condition = threading.Condition()
        self._write_lock = threading.Lock()
        self._lines: deque[str] = deque(maxlen=4096)
        self._buffer = bytearray()
        self._error: Exception | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._read_loop, name="stampfly-rx", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _set_error(self, error: Exception) -> None:
        with self._condition:
            if self._error is None:
                self._error = error
            self._condition.notify_all()

    def _read_loop(self) -> None:
        try:
            while not self._stop.is_set():
                chunk = self._serial.read(512)
                if not chunk:
                    continue
                self._buffer.extend(chunk)
                if len(self._buffer) > self._max_line_bytes:
                    raise ProtocolError("received line exceeds bounded buffer")
                while b"\n" in self._buffer:
                    raw, _, rest = bytes(self._buffer).partition(b"\n")
                    self._buffer[:] = rest
                    line = raw.replace(b"\r", b"").decode("utf-8", "replace").strip()
                    if line:
                        with self._condition:
                            self._lines.append(line)
                            self._condition.notify_all()
        except Exception as exc:  # notify the command waiter and fail closed
            self._set_error(exc)

    def send(self, command: str) -> None:
        if "\r" in command or "\n" in command:
            raise ValueError("command must not contain line breaks")
        payload = (command + "\r\n").encode("ascii")
        try:
            with self._write_lock:
                written = self._serial.write(payload)
                if written is not None and written != len(payload):
                    raise StampFlyError(
                        f"serial short write: expected {len(payload)} bytes, wrote {written}"
                    )
                self._serial.flush()
        except (OSError, getattr(serial, "SerialException", OSError)) as exc:
            self._set_error(exc)
            raise StampFlyError(f"serial write failed: {exc}") from exc

    def wait_for(self, predicate: Callable[[str], bool], *, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                if self._error is not None:
                    raise StampFlyError(f"serial reader failed: {self._error}") from self._error
                while self._lines:
                    line = self._lines.popleft()
                    if line.startswith("CF1 ERR "):
                        raise ProtocolError(line)
                    if predicate(line):
                        return line
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProtocolError("timeout waiting for response")
                self._condition.wait(remaining)

    def stop(self) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        self._thread.join(timeout=1.0)


class _ZeroHeartbeat:
    """Send only zero ANGLE/MANUAL SET packets at the requested rate."""

    def __init__(self, reader: _LineReader, hz: float) -> None:
        self._reader = reader
        self._period = 1.0 / hz
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._set_count = 0
        self._error: Exception | None = None
        self._thread = threading.Thread(target=self._run, name="stampfly-zero-heartbeat", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        sequence = 1
        next_send = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now < next_send:
                self._stop.wait(next_send - now)
                continue
            try:
                self._reader.send(f"CF1 SET {sequence} 0 0 0 0 0 5")
                with self._lock:
                    self._set_count += 1
                sequence += 1
            except Exception as exc:
                self._error = exc
                self._stop.set()
                return
            next_send += self._period
            if next_send < now - self._period:
                next_send = now + self._period

    @property
    def set_count(self) -> int:
        with self._lock:
            return self._set_count

    @property
    def error(self) -> Exception | None:
        return self._error

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)


def _status(reader: _LineReader) -> StampFlyStatus:
    reader.send("CF1 STATUS")
    line = reader.wait_for(lambda value: value.startswith("CF1 STATUS "), timeout=RESPONSE_TIMEOUT_SECONDS)
    return StampFlyStatus.parse(line)


def run(args: argparse.Namespace) -> int:
    args.log.parent.mkdir(parents=True, exist_ok=True)
    try:
        log_handle = args.log.open("x", encoding="utf-8")
    except FileExistsError:
        raise StampFlyError(f"refusing to overwrite existing artifact: {args.log}")

    started_wall = datetime.now(timezone.utc).isoformat()
    started_mono = time.monotonic()
    arm_count = 0
    non_zero_set_count = 0
    status_count = 0
    status_errors: list[str] = []
    cleanup_errors: list[str] = []
    phase_samples: dict[str, list[dict[str, str]]] = {phase: [] for phase in PHASES}
    serial_port = None
    reader: _LineReader | None = None
    heartbeat: _ZeroHeartbeat | None = None
    completed_normally = False

    try:
        if serial is None:
            raise StampFlyError("pyserial is required for live acceptance")
        serial_port = serial.Serial(
            port=args.port,
            baudrate=115200,
            timeout=0.02,
            write_timeout=0.20,
        )
        # Opening USB-Serial/JTAG may reset the board.  Drain startup
        # diagnostics before handing the stream to the bounded CF1 parser.
        # USB open can reset the ESP32-S3 before the long I2C/IMU startup has
        # finished.  Drain the boot stream long enough for CF1 polling to be
        # available, while keeping the wait bounded.
        startup_deadline = time.monotonic() + 15.0
        while time.monotonic() < startup_deadline:
            serial_port.read(512)
        serial_port.reset_input_buffer()
        reader = _LineReader(serial_port)
        reader.start()

        reader.send("CF1 HELLO")
        hello = reader.wait_for(lambda value: value.startswith("CF1 HELLO "), timeout=RESPONSE_TIMEOUT_SECONDS)
        reader.send("CF1 CLAIM")
        reader.wait_for(lambda value: value == "CF1 OK CLAIM", timeout=RESPONSE_TIMEOUT_SECONDS)
        startup_status = _status(reader)
        if not startup_status.safe_test or not startup_status.claimed or startup_status.armed:
            raise StampFlyError("startup STATUS is not claimed disarmed camfly-safe hardware")
        _write_record(
            log_handle,
            {
                "type": "metadata",
                "schema_version": 1,
                "started_at_utc": started_wall,
                "firmware_hello": hello,
                "build_environment": "camfly-safe",
                "safe_test_expected": 1,
                "propellers": "removed",
                "arm_count": 0,
                "non_zero_set_count": 0,
                "port_recorded": False,
            },
        )
        _write_record(
            log_handle,
            {
                "type": "status",
                "phase": "startup",
                "elapsed_seconds": time.monotonic() - started_mono,
                "fields": startup_status.fields,
            },
        )
        status_count += 1

        print(f"connected: {hello}", flush=True)
        print("startup STATUS captured; ARM is unavailable and SET is zero-only", flush=True)
        print("The phase prompts below are timed. Keep the propellers removed.", flush=True)

        heartbeat = _ZeroHeartbeat(reader, args.hz)
        heartbeat.start()
        for phase, phase_seconds in zip(PHASES, args.phase_seconds):
            print(_phase_prompt(phase), flush=True)
            phase_started = time.monotonic()
            phase_deadline = phase_started + phase_seconds
            next_status = phase_started
            last_printed_status = 0.0
            while time.monotonic() < phase_deadline:
                if heartbeat.error is not None:
                    raise StampFlyError(f"zero heartbeat failed: {heartbeat.error}") from heartbeat.error
                now = time.monotonic()
                if now >= next_status:
                    try:
                        status = _status(reader)
                        status_count += 1
                        _write_record(
                            log_handle,
                            {
                                "type": "status",
                                "phase": phase,
                                "elapsed_seconds": now - started_mono,
                                "phase_elapsed_seconds": now - phase_started,
                                "fields": status.fields,
                            },
                        )
                        phase_samples[phase].append(status.fields)
                        if not status.safe_test or not status.claimed or status.armed:
                            raise StampFlyError("unsafe STATUS during acceptance")
                        if now - last_printed_status >= 1.0:
                            print(
                                f"{phase}: range_valid={int(status.range_valid)} "
                                f"range_age_ms={status.range_age_ms} "
                                f"altitude_valid={int(status.altitude_valid)} "
                                f"altitude_age_ms={status.altitude_age_ms} "
                                f"imu_valid={int(status.imu_valid)} "
                                f"imu_age_ms={status.imu_age_ms} "
                                f"tof_stream_count={status.fields.get('tof_stream_count')} "
                                f"tof_data_ready_count={status.fields.get('tof_data_ready_count')}",
                                flush=True,
                            )
                            last_printed_status = now
                    except (StampFlyError, OSError, ValueError) as exc:
                        error = f"{type(exc).__name__}: {exc}"
                        status_errors.append(error)
                        _write_record(
                            log_handle,
                            {
                                "type": "status_error",
                                "phase": phase,
                                "elapsed_seconds": now - started_mono,
                                "error": error,
                            },
                        )
                        raise
                    next_status += 1.0
                time.sleep(max(0.0, min(next_status, phase_deadline) - time.monotonic()))

        final_status = _status(reader)
        status_count += 1
        _write_record(
            log_handle,
            {
                "type": "status",
                "phase": "final",
                "elapsed_seconds": time.monotonic() - started_mono,
                "fields": final_status.fields,
            },
        )
        if not final_status.safe_test or not final_status.claimed or final_status.armed:
            raise StampFlyError("final STATUS is unsafe")

        heartbeat.stop()
        for command, expected in (
            ("CF1 DISARM", "CF1 OK DISARM"),
            ("CF1 RELEASE", "CF1 OK RELEASE"),
        ):
            try:
                reader.send(command)
                reader.wait_for(lambda value, expected=expected: value == expected, timeout=RESPONSE_TIMEOUT_SECONDS)
            except (StampFlyError, OSError, ValueError) as exc:
                cleanup_errors.append(f"{command}: {type(exc).__name__}: {exc}")

        cleanup_status = _status(reader)
        status_count += 1
        _write_record(
            log_handle,
            {
                "type": "status",
                "phase": "cleanup",
                "elapsed_seconds": time.monotonic() - started_mono,
                "fields": cleanup_status.fields,
            },
        )
        if cleanup_status.claimed or cleanup_status.armed:
            raise StampFlyError("cleanup STATUS is still claimed or armed")
        completed_normally = True
        return 0
    except (KeyboardInterrupt, StampFlyError, OSError, ValueError) as exc:
        print(f"acceptance error: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        if heartbeat is not None:
            heartbeat.stop()
        if reader is not None:
            if not completed_normally:
                try:
                    reader.send("CF1 DISARM")
                except (StampFlyError, OSError, ValueError):
                    pass
            reader.stop()
        if serial_port is not None:
            serial_port.close()
        duration = time.monotonic() - started_mono
        _write_record(
            log_handle,
            {
                "type": "summary",
                "schema_version": 1,
                "completed_normally": completed_normally,
                "duration_seconds": duration,
                "set_count": heartbeat.set_count if heartbeat is not None else 0,
                "arm_count": arm_count,
                "non_zero_set_count": non_zero_set_count,
                "status_count": status_count,
                "status_errors": status_errors,
                "cleanup_errors": cleanup_errors,
                "phase_status_counts": {phase: len(samples) for phase, samples in phase_samples.items()},
                "build_environment": "camfly-safe",
                "safe_test": 1,
                "port_recorded": False,
            },
        )
        log_handle.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="prop-off StampFly telemetry-validity acceptance")
    parser.add_argument("--port", required=True, help="explicit StampFly serial device")
    parser.add_argument("--log", required=True, type=Path, help="new JSONL artifact path")
    parser.add_argument("--hz", type=_positive_seconds, default=25.0)
    parser.add_argument(
        "--phase-seconds",
        type=_positive_seconds,
        nargs=3,
        metavar=("NORMAL", "TOF_INVALID", "RECOVERY"),
        default=(20.0, 20.0, 20.0),
    )
    args = parser.parse_args()
    if args.hz < 20.0:
        parser.error("--hz must be at least 20 Hz")
    try:
        return run(args)
    except StampFlyError as exc:
        print(f"acceptance error: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
