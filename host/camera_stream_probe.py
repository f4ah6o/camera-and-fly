#!/usr/bin/env python3
"""Probe decoded-frame freshness without saving raw video."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .camera_stream import probe_events, probe_live, synthetic_events
except ImportError:  # pragma: no cover
    from camera_stream import probe_events, probe_live, synthetic_events


def main() -> int:
    parser = argparse.ArgumentParser(description="bounded decoded-frame adapter probe")
    parser.add_argument("--backend", choices=("synthetic", "ffmpeg"), default="synthetic")
    parser.add_argument("--url", help="RTSP/WebRTC input for --backend ffmpeg")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--burst", type=int, default=1)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg executable for --backend ffmpeg")
    parser.add_argument("--max-age", type=float, default=0.5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.backend == "synthetic":
        events = synthetic_events(count=args.count, burst=args.burst)
        report = probe_events(events, max_age=args.max_age)
    else:
        if not args.url:
            parser.error("--backend ffmpeg requires --url")
        report = probe_live(
            args.url,
            width=args.width,
            height=args.height,
            duration=args.duration,
            max_age=args.max_age,
            executable=args.ffmpeg,
        )

    encoded = json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
