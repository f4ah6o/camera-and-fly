#!/usr/bin/env python3
"""Local-only Atom Cam 1 stream helpers.

The original Atom Cam exposes these endpoints after the atomcam_tools microSD
environment has booted.  This module never contacts a cloud service.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
import time
from pathlib import Path
from typing import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class AtomCamError(RuntimeError):
    """A local Atom Cam request failed."""


@dataclass(frozen=True)
class AtomCamFrame:
    """One received JPEG and the clocks that describe its freshness.

    ``captured_at`` is retained for callers of the original helper.  It is a
    wall-clock timestamp taken when the response was received; it is *not* a
    sensor exposure timestamp.  The camera endpoint does not expose a capture
    timestamp, so ``capture_monotonic`` remains ``None`` unless a future input
    source provides one.
    """

    captured_at: float
    jpeg: bytes
    content_type: str
    frame_id: int = 0
    request_started_monotonic: float = 0.0
    received_monotonic: float = 0.0
    capture_monotonic: float | None = None


class _SameHostRedirectHandler(HTTPRedirectHandler):
    """Follow only redirects that stay on the configured Atom Cam host."""

    def __init__(self, expected: tuple[str, str, int | None]) -> None:
        super().__init__()
        self._expected = expected

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        parsed = urlparse(newurl)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            raise AtomCamError("Atom Cam redirect is not a local http(s) URL")
        try:
            port = parsed.port
        except ValueError as exc:
            raise AtomCamError("Atom Cam redirect has an invalid port") from exc
        target = (parsed.scheme, parsed.hostname or "", port)
        if target != self._expected:
            raise AtomCamError("Atom Cam redirect changed host or port")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class AtomCamSource:
    """Consume the local JPEG endpoint and derive local RTSP URLs."""

    def __init__(
        self,
        url: str,
        *,
        jpeg_path: str = "/cgi-bin/get_jpeg.cgi",
        timeout: float = 2.0,
        max_jpeg_bytes: int = 4 * 1024 * 1024,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Atom Cam base URL must be an http(s) URL")
        if parsed.username or parsed.password:
            raise ValueError("Atom Cam base URL must not contain userinfo")
        if parsed.query or parsed.fragment:
            raise ValueError("Atom Cam base URL must not contain a query or fragment")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Atom Cam base URL has an invalid port") from exc
        if not parsed.hostname:
            raise ValueError("Atom Cam base URL has no hostname")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if type(max_jpeg_bytes) is not int or max_jpeg_bytes <= 0:
            raise ValueError("max_jpeg_bytes must be a positive integer")
        if not jpeg_path.startswith("/"):
            jpeg_path = "/" + jpeg_path
        jpeg_parsed = urlparse(jpeg_path)
        if jpeg_parsed.query or jpeg_parsed.fragment or jpeg_parsed.scheme or jpeg_parsed.netloc:
            raise ValueError("jpeg_path must be a path without query or fragment")
        self.base_url = urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", "")
        )
        self.jpeg_url = self.base_url + jpeg_path
        self.timeout = timeout
        self.max_jpeg_bytes = max_jpeg_bytes
        self._monotonic = monotonic_clock
        self._wall_clock = wall_clock
        self._frame_id = 0
        self._opener = build_opener(
            _SameHostRedirectHandler((parsed.scheme, parsed.hostname, port))
        )

    def snapshot(self) -> AtomCamFrame:
        request_started = self._monotonic()
        request = Request(
            self.jpeg_url,
            headers={
                "Accept": "image/jpeg",
                "Cache-Control": "no-cache",
                "User-Agent": "camera-and-fly/0.1",
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError as exc:
                        raise AtomCamError("Atom Cam response has invalid Content-Length") from exc
                    if declared_size < 0 or declared_size > self.max_jpeg_bytes:
                        raise AtomCamError(
                            f"Atom Cam JPEG exceeds {self.max_jpeg_bytes} byte limit"
                        )
                chunks: list[bytes] = []
                size = 0
                while True:
                    chunk = response.read(min(64 * 1024, self.max_jpeg_bytes + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_jpeg_bytes:
                        raise AtomCamError(
                            f"Atom Cam JPEG exceeds {self.max_jpeg_bytes} byte limit"
                        )
                    chunks.append(chunk)
                jpeg = b"".join(chunks)
                content_type = response.headers.get_content_type()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise AtomCamError(f"Atom Cam JPEG request failed: {type(exc).__name__}: {exc}") from exc

        received = self._monotonic()

        if len(jpeg) < 4 or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise AtomCamError(
                f"Atom Cam endpoint did not return a complete JPEG "
                f"(content-type={content_type!r}, bytes={len(jpeg)})"
            )
        self._frame_id += 1
        return AtomCamFrame(
            captured_at=self._wall_clock(),
            jpeg=jpeg,
            content_type=content_type,
            frame_id=self._frame_id,
            request_started_monotonic=request_started,
            received_monotonic=received,
        )

    def snapshots(self, *, fps: float = 5.0) -> Iterator[AtomCamFrame]:
        if fps <= 0:
            raise ValueError("fps must be positive")
        interval = 1.0 / fps
        while True:
            started = time.monotonic()
            yield self.snapshot()
            time.sleep(max(0.0, interval - (time.monotonic() - started)))

    def rtsp_url(self, stream: str = "video0", *, port: int = 8554) -> str:
        if stream not in {"video0", "video1", "video2"}:
            raise ValueError("stream must be video0, video1, or video2")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("port must be an integer in [1, 65535]")
        parsed = urlparse(self.base_url)
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("Atom Cam URL has no hostname")
        host = f"[{hostname}]" if ":" in hostname else hostname
        return f"rtsp://{host}:{port}/{stream}_unicast"


def main() -> int:
    parser = argparse.ArgumentParser(description="local Atom Cam 1 stream probe")
    parser.add_argument("--url", required=True, help="for example http://atomcam.local")
    parser.add_argument("--once", action="store_true", help="fetch one local JPEG and exit")
    parser.add_argument("--save", type=Path, help="save the fetched JPEG to this path")
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--rtsp", choices=("video0", "video1", "video2"))
    parser.add_argument("--rtsp-port", type=int, default=8554)
    args = parser.parse_args()

    source = AtomCamSource(args.url)
    if args.rtsp:
        print(source.rtsp_url(args.rtsp, port=args.rtsp_port))
        return 0

    try:
        frames = source.snapshots(fps=args.fps)
        while True:
            frame = next(frames)
            if args.save:
                args.save.write_bytes(frame.jpeg)
                print(f"saved {len(frame.jpeg)} bytes to {args.save}", flush=True)
            else:
                print(
                    f"jpeg bytes={len(frame.jpeg)} content_type={frame.content_type} "
                    f"captured_at={frame.captured_at:.3f}",
                    flush=True,
                )
            if args.once:
                break
    except (AtomCamError, OSError, KeyboardInterrupt) as exc:
        if isinstance(exc, KeyboardInterrupt):
            return 130
        print(f"atomcam error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
