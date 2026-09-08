#!/usr/bin/env python3
"""Measure Atom Cam JPEG request/receive behavior without saving raw frames."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
import json
import math
from pathlib import Path
import time
from typing import Callable

try:
    from .atomcam import AtomCamError, AtomCamSource
except ImportError:  # pragma: no cover - exercised by the script entrypoint
    from atomcam import AtomCamError, AtomCamSource


def percentile(values: list[float], percentile_value: float) -> float | None:
    if not values:
        return None
    if not 0 <= percentile_value <= 100:
        raise ValueError("percentile must be in [0, 100]")
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile_value / 100.0
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def jpeg_dimensions(jpeg: bytes) -> tuple[int, int] | None:
    """Return JPEG width/height from a SOF marker without decoding pixels.

    The probe deliberately avoids bringing a decoder into the measurement
    path.  Invalid/truncated metadata returns ``None``; complete JPEG framing
    is still validated by :class:`AtomCamSource` before this function runs.
    """

    if len(jpeg) < 4 or not jpeg.startswith(b"\xff\xd8"):
        return None
    index = 2
    standalone = {0x01, *range(0xD0, 0xD8), 0xD8, 0xD9}
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while index < len(jpeg):
        if jpeg[index] != 0xFF:
            index += 1
            continue
        while index < len(jpeg) and jpeg[index] == 0xFF:
            index += 1
        if index >= len(jpeg):
            return None
        marker = jpeg[index]
        index += 1
        if marker == 0x00 or marker in standalone:
            continue
        if index + 2 > len(jpeg):
            return None
        segment_length = int.from_bytes(jpeg[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(jpeg):
            return None
        if marker in sof_markers:
            if segment_length < 7:
                return None
            height = int.from_bytes(jpeg[index + 3 : index + 5], "big")
            width = int.from_bytes(jpeg[index + 5 : index + 7], "big")
            if width <= 0 or height <= 0:
                return None
            return width, height
        if marker == 0xDA:  # start of scan: metadata must precede compressed pixels
            return None
        index += segment_length
    return None


@dataclass
class ProbeReport:
    schema_version: int
    started_at: float
    ended_at: float
    duration_seconds: float
    requested_fps: float
    received_frames: int
    failed_requests: int
    timeout_count: int
    loss_fraction: float
    achieved_receive_fps: float
    bytes_total: int
    bytes_min: int | None
    bytes_max: int | None
    resolutions: list[str]
    request_latency_seconds: dict[str, float | None]
    interarrival_seconds: dict[str, float | None]
    condition: str
    lighting: str
    endpoint_kind: str
    capture_timestamp_available: bool
    decision: str
    notes: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def run_probe(
    source: AtomCamSource,
    *,
    duration: float,
    fps: float,
    condition: str = "unspecified",
    lighting: str = "unspecified",
    decision: str = "unreviewed",
    monotonic_clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> ProbeReport:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration must be finite and positive")
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("fps must be finite and positive")
    for name, value in (("condition", condition), ("lighting", lighting), ("decision", decision)):
        if not value or "\n" in value or "\r" in value:
            raise ValueError(f"{name} must be a non-empty single-line string")

    started_mono = monotonic_clock()
    started_wall = wall_clock()
    deadline = started_mono + duration
    request_latencies: list[float] = []
    interarrivals: list[float] = []
    byte_counts: list[int] = []
    resolutions: set[str] = set()
    received = 0
    failures = 0
    timeouts = 0
    attempts = 0
    previous_received: float | None = None
    next_request = started_mono
    while monotonic_clock() < deadline:
        now = monotonic_clock()
        if now < next_request:
            sleep(min(next_request - now, max(0.001, deadline - now)))
            continue
        attempts += 1
        try:
            frame = source.snapshot()
        except (AtomCamError, OSError, TimeoutError) as exc:
            failures += 1
            if isinstance(exc, TimeoutError) or "timeout" in str(exc).lower():
                timeouts += 1
        else:
            received += 1
            request_latencies.append(
                max(0.0, frame.received_monotonic - frame.request_started_monotonic)
            )
            if previous_received is not None:
                interarrivals.append(max(0.0, frame.received_monotonic - previous_received))
            previous_received = frame.received_monotonic
            byte_counts.append(len(frame.jpeg))
            dimensions = jpeg_dimensions(frame.jpeg)
            if dimensions is not None:
                resolutions.add(f"{dimensions[0]}x{dimensions[1]}")
        next_request += 1.0 / fps
        if next_request < monotonic_clock() - 1.0:
            # A slow request must not cause an unbounded burst of catch-up
            # requests.  The next sample starts from the current time.
            next_request = monotonic_clock()

    ended_wall = wall_clock()
    ended_mono = monotonic_clock()
    elapsed = max(0.0, ended_mono - started_mono)
    return ProbeReport(
        schema_version=2,
        started_at=started_wall,
        ended_at=ended_wall,
        duration_seconds=elapsed,
        requested_fps=fps,
        received_frames=received,
        failed_requests=failures,
        timeout_count=timeouts,
        loss_fraction=(failures / attempts) if attempts else 0.0,
        achieved_receive_fps=(received / elapsed) if elapsed > 0 else 0.0,
        bytes_total=sum(byte_counts),
        bytes_min=min(byte_counts) if byte_counts else None,
        bytes_max=max(byte_counts) if byte_counts else None,
        resolutions=sorted(resolutions),
        request_latency_seconds={
            "p50": percentile(request_latencies, 50),
            "p95": percentile(request_latencies, 95),
            "p99": percentile(request_latencies, 99),
            "max": max(request_latencies) if request_latencies else None,
        },
        interarrival_seconds={
            "p50": percentile(interarrivals, 50),
            "p95": percentile(interarrivals, 95),
            "p99": percentile(interarrivals, 99),
            "max": max(interarrivals) if interarrivals else None,
        },
        condition=condition,
        lighting=lighting,
        endpoint_kind="jpeg_snapshot",
        capture_timestamp_available=False,
        decision=decision,
        notes=[
            "request latency is network/request time from local start to local receive",
            "capture time is unknown for the JPEG endpoint",
            "loss_fraction counts failed HTTP attempts, not sensor-frame loss",
            "identical JPEG hashes are not classified as a frozen camera",
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="measure a local Atom Cam JPEG endpoint")
    parser.add_argument("--url", required=True)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--max-jpeg-bytes", type=int, default=4 * 1024 * 1024)
    parser.add_argument("--condition", default="unspecified", help="single-line test condition label")
    parser.add_argument("--lighting", default="unspecified", help="single-line lighting description")
    parser.add_argument("--decision", default="unreviewed", help="single-line adoption/rejection note")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        source = AtomCamSource(
            args.url,
            max_jpeg_bytes=args.max_jpeg_bytes,
        )
        report = run_probe(
            source,
            duration=args.duration,
            fps=args.fps,
            condition=args.condition,
            lighting=args.lighting,
            decision=args.decision,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report.to_dict(), indent=2) + "\n")
        print(json.dumps(report.to_dict(), indent=2))
    except (ValueError, AtomCamError, OSError) as exc:
        parser.exit(1, f"camera probe error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
