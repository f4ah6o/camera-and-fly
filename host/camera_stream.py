"""Bounded decoded-frame contract and worker for local RTSP/WebRTC streams.

The consumer sees a single latest frame and never waits on decoder I/O.  The
FFmpeg backend deliberately exposes missing source PTS/DTS as ``None`` rather
than substituting receive time; a backend with trustworthy timestamps can fill
those fields without changing the consumer contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import subprocess
import threading
import time
from typing import Callable, Iterable, Iterator, Protocol


class DecoderError(RuntimeError):
    pass


class DecoderDisconnected(DecoderError):
    pass


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    return float(value)


@dataclass(frozen=True)
class DecodedFrame:
    """One completed decoded frame with deliberately separate clocks."""

    frame_id: int
    data: bytes
    width: int
    height: int
    pixel_format: str
    received_monotonic: float
    decode_complete_monotonic: float
    source_pts: int | float | None = None
    source_dts: int | float | None = None
    source_time_base: tuple[int, int] | None = None
    decoder_generation: int = 0

    def __post_init__(self) -> None:
        if type(self.frame_id) is not int or self.frame_id < 1:
            raise ValueError("frame_id must be a positive integer")
        if not isinstance(self.data, bytes) or not self.data:
            raise ValueError("decoded frame data must be non-empty bytes")
        if type(self.width) is not int or self.width < 1 or type(self.height) is not int or self.height < 1:
            raise ValueError("decoded frame dimensions must be positive integers")
        if not self.pixel_format or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in self.pixel_format):
            raise ValueError("pixel_format must be a stable lowercase token")
        received = _finite(self.received_monotonic, "received_monotonic")
        complete = _finite(self.decode_complete_monotonic, "decode_complete_monotonic")
        if complete < received:
            raise ValueError("decode_complete_monotonic cannot precede receive time")
        for name, value in (("source_pts", self.source_pts), ("source_dts", self.source_dts)):
            if value is not None:
                _finite(value, name)
        if self.source_time_base is not None:
            if (
                type(self.source_time_base) is not tuple
                or len(self.source_time_base) != 2
                or type(self.source_time_base[0]) is not int
                or type(self.source_time_base[1]) is not int
                or self.source_time_base[0] <= 0
                or self.source_time_base[1] <= 0
            ):
                raise ValueError("source_time_base must be a positive (numerator, denominator) tuple")
        if type(self.decoder_generation) is not int or self.decoder_generation < 0:
            raise ValueError("decoder_generation must be non-negative")

    @property
    def source_timestamp_available(self) -> bool:
        return self.source_pts is not None or self.source_dts is not None

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        if self.source_time_base is not None:
            value["source_time_base"] = list(self.source_time_base)
        return value


@dataclass(frozen=True)
class DecodedFrameSnapshot:
    frame: DecodedFrame | None
    age_seconds: float | None
    valid: bool
    connected: bool
    generation: int
    error: str | None
    overwritten: int
    published: int


class LatestDecodedFrameSlot:
    """Thread-safe bounded latest-value storage with no backlog."""

    def __init__(self, *, monotonic_clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._frame: DecodedFrame | None = None
        self._connected = False
        self._generation = 0
        self._error: str | None = None
        self._published = 0
        self._overwritten = 0
        self._monotonic = monotonic_clock

    def begin_generation(self, generation: int) -> None:
        if type(generation) is not int or generation < 0:
            raise ValueError("generation must be a non-negative integer")
        with self._lock:
            self._generation = generation
            self._connected = False

    def publish(self, frame: DecodedFrame) -> None:
        with self._lock:
            if self._frame is not None:
                self._overwritten += 1
            self._frame = frame
            self._generation = frame.decoder_generation
            self._connected = True
            self._error = None
            self._published += 1

    def publish_error(self, error: BaseException | str) -> None:
        with self._lock:
            self._error = str(error)
            self._connected = False

    def mark_disconnected(self, error: BaseException | str | None = None) -> None:
        with self._lock:
            self._connected = False
            if error is not None:
                self._error = str(error)

    def snapshot(self, *, now: float | None = None, max_age: float | None = None) -> DecodedFrameSnapshot:
        if max_age is not None and (not math.isfinite(max_age) or max_age < 0):
            raise ValueError("max_age must be finite and non-negative")
        current = self._monotonic() if now is None else _finite(now, "now")
        with self._lock:
            frame = self._frame
            connected = self._connected
            generation = self._generation
            error = self._error
            overwritten = self._overwritten
            published = self._published
        if frame is None:
            return DecodedFrameSnapshot(None, None, False, connected, generation, error, overwritten, published)
        age = max(0.0, current - frame.decode_complete_monotonic)
        valid = connected and (max_age is None or age <= max_age)
        return DecodedFrameSnapshot(frame, age, valid, connected, generation, error, overwritten, published)


class DecoderBackend(Protocol):
    name: str

    def decode(self, stop: threading.Event, generation: int) -> Iterable[DecodedFrame]: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class DecoderDescriptor:
    name: str
    version: str
    install: str
    source: str


class DecoderWorker:
    """Own all decoder I/O and publish only the latest completed frame."""

    def __init__(
        self,
        backend_factory: Callable[[int], DecoderBackend],
        *,
        slot: LatestDecodedFrameSlot | None = None,
        retry_initial_seconds: float = 0.1,
        retry_max_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 < retry_initial_seconds <= retry_max_seconds:
            raise ValueError("retry backoff must be positive and ordered")
        self.backend_factory = backend_factory
        self.slot = slot or LatestDecodedFrameSlot()
        self.retry_initial_seconds = retry_initial_seconds
        self.retry_max_seconds = retry_max_seconds
        self._sleep = sleep
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._backend: DecoderBackend | None = None
        self._lock = threading.Lock()
        self._generation = 0
        self._frames = 0
        self._errors = 0
        self._disconnects = 0

    @property
    def frames_received(self) -> int:
        with self._lock:
            return self._frames

    @property
    def errors(self) -> int:
        with self._lock:
            return self._errors

    @property
    def disconnects(self) -> int:
        with self._lock:
            return self._disconnects

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="decoded-frame-worker", daemon=True)
            self._thread.start()

    def stop(self, *, timeout: float | None = None) -> None:
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError("timeout must be finite and non-negative")
        self._stop.set()
        with self._lock:
            backend = self._backend
            thread = self._thread
        if backend is not None:
            try:
                backend.close()
            except Exception:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self.slot.mark_disconnected("decoder stopped")

    def snapshot(self, *, now: float | None = None, max_age: float | None = None) -> DecodedFrameSnapshot:
        return self.slot.snapshot(now=now, max_age=max_age)

    def _run(self) -> None:
        backoff = self.retry_initial_seconds
        while not self._stop.is_set():
            with self._lock:
                self._generation += 1
                generation = self._generation
            self.slot.begin_generation(generation)
            backend: DecoderBackend | None = None
            try:
                backend = self.backend_factory(generation)
                with self._lock:
                    self._backend = backend
                for frame in backend.decode(self._stop, generation):
                    if self._stop.is_set():
                        break
                    if not isinstance(frame, DecodedFrame):
                        raise DecoderError("decoder yielded a non-frame value")
                    self.slot.publish(frame)
                    with self._lock:
                        self._frames += 1
                if self._stop.is_set():
                    break
                raise DecoderDisconnected("decoder stopped producing frames")
            except Exception as exc:
                with self._lock:
                    self._errors += 1
                    self._disconnects += 1
                self.slot.publish_error(exc)
                self.slot.mark_disconnected(exc)
                if self._stop.wait(backoff):
                    break
                backoff = min(self.retry_max_seconds, backoff * 2.0)
            else:
                backoff = self.retry_initial_seconds
            finally:
                try:
                    if backend is not None:
                        backend.close()
                except Exception:
                    pass
                with self._lock:
                    if self._backend is backend:
                        self._backend = None


class FFmpegDecoderBackend:
    """Decode fixed-size raw RGB frames from the FFmpeg CLI.

    Rawvideo output has no portable timestamp side channel, so source PTS/DTS
    remain ``None``. Receive and decode-complete monotonic times are still
    recorded independently. A future demuxer backend can supply source timing
    without relabelling these clocks.
    """

    name = "ffmpeg-cli"
    descriptor = DecoderDescriptor(
        name=name,
        version="runtime; use descriptor_for() for the installed version",
        install="brew install ffmpeg",
        source="https://ffmpeg.org/documentation.html",
    )

    @classmethod
    def descriptor_for(cls, executable: str = "ffmpeg") -> DecoderDescriptor:
        """Return the selected backend plus the locally installed version."""

        if not executable:
            raise ValueError("ffmpeg executable is required")
        version = "unavailable"
        try:
            result = subprocess.run(
                [executable, "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=2.0,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        else:
            first_line = result.stdout.splitlines()[0].strip() if result.stdout else ""
            if first_line:
                version = first_line
        return DecoderDescriptor(
            name=cls.name,
            version=version,
            install=cls.descriptor.install,
            source=cls.descriptor.source,
        )

    def __init__(
        self,
        url: str,
        *,
        width: int,
        height: int,
        pixel_format: str = "rgb24",
        executable: str = "ffmpeg",
        monotonic_clock: Callable[[], float] = time.monotonic,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        if not url or "\n" in url or "\r" in url:
            raise ValueError("url must be a non-empty single-line value")
        if type(width) is not int or width < 1 or type(height) is not int or height < 1:
            raise ValueError("width and height must be positive integers")
        if pixel_format not in {"gray", "rgb24", "rgba"}:
            raise ValueError("unsupported raw pixel format")
        if not executable:
            raise ValueError("ffmpeg executable is required")
        self.url = url
        self.width = width
        self.height = height
        self.pixel_format = pixel_format
        self.executable = executable
        self._monotonic = monotonic_clock
        self._popen = popen
        self._process: subprocess.Popen[bytes] | None = None
        self._frame_id = 0

    @property
    def frame_bytes(self) -> int:
        channels = {"gray": 1, "rgb24": 3, "rgba": 4}[self.pixel_format]
        return self.width * self.height * channels

    def command(self) -> list[str]:
        return [
            self.executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            self.url,
            "-an",
            "-sn",
            "-dn",
            "-f",
            "rawvideo",
            "-pix_fmt",
            self.pixel_format,
            "pipe:1",
        ]

    def decode(self, stop: threading.Event, generation: int) -> Iterator[DecodedFrame]:
        process = self._popen(
            self.command(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
        self._process = process
        if process.stdout is None:
            raise DecoderError("ffmpeg stdout is unavailable")
        try:
            while not stop.is_set():
                received = self._monotonic()
                data = self._read_exact(process.stdout, self.frame_bytes, stop)
                if data is None or not data:
                    break
                complete = self._monotonic()
                self._frame_id += 1
                yield DecodedFrame(
                    frame_id=self._frame_id,
                    data=data,
                    width=self.width,
                    height=self.height,
                    pixel_format=self.pixel_format,
                    received_monotonic=received,
                    decode_complete_monotonic=complete,
                    decoder_generation=generation,
                )
            if not stop.is_set() and process.poll() not in (None, 0):
                raise DecoderDisconnected(f"ffmpeg exited with status {process.returncode}")
        finally:
            self.close()

    @staticmethod
    def _read_exact(stream: object, length: int, stop: threading.Event) -> bytes | None:
        chunks: list[bytes] = []
        remaining = length
        while remaining and not stop.is_set():
            read = getattr(stream, "read")
            chunk = read(remaining)
            if not chunk:
                if chunks:
                    raise DecoderDisconnected("ffmpeg ended with a partial frame")
                return None
            if not isinstance(chunk, bytes):
                raise DecoderError("ffmpeg stdout returned a non-byte value")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"" if stop.is_set() else b"".join(chunks)

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)


@dataclass(frozen=True)
class StreamEvent:
    at: float
    kind: str
    frame: DecodedFrame | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.at) or self.at < 0:
            raise ValueError("event time must be finite and non-negative")
        if self.kind not in {"frame", "disconnect", "error", "reconnect"}:
            raise ValueError("unsupported stream event kind")
        if self.kind == "frame" and self.frame is None:
            raise ValueError("frame event requires a frame")


@dataclass(frozen=True)
class StreamProbeReport:
    schema_version: int
    backend: str
    backend_version: str
    install: str
    source: str
    events: int
    frames_received: int
    slot_overwrites: int
    disconnects: int
    reconnects: int
    valid_frames: int
    invalid_frames: int
    source_pts_available: bool
    source_dts_available: bool
    max_decode_duration_seconds: float | None
    condition: str
    decision: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def probe_events(
    events: Iterable[StreamEvent],
    *,
    descriptor: DecoderDescriptor | None = None,
    max_age: float = 0.5,
    condition: str = "synthetic",
    decision: str = "measurement-only",
) -> StreamProbeReport:
    if not math.isfinite(max_age) or max_age < 0:
        raise ValueError("max_age must be finite and non-negative")
    descriptor = descriptor or DecoderDescriptor(
        name="synthetic-fixture",
        version="local",
        install="none",
        source="repository fixture",
    )
    slot = LatestDecodedFrameSlot(monotonic_clock=lambda: 0.0)
    count = 0
    disconnects = 0
    reconnects = 0
    valid = 0
    invalid = 0
    source_pts = False
    source_dts = False
    decode_durations: list[float] = []
    previous_at = -1.0
    for event in events:
        if event.at < previous_at:
            raise ValueError("stream events must be monotonic")
        previous_at = event.at
        count += 1
        if event.kind == "frame":
            assert event.frame is not None
            slot.publish(event.frame)
            snapshot = slot.snapshot(now=event.at, max_age=max_age)
            valid += int(snapshot.valid)
            invalid += int(not snapshot.valid)
            source_pts = source_pts or event.frame.source_pts is not None
            source_dts = source_dts or event.frame.source_dts is not None
            decode_durations.append(event.frame.decode_complete_monotonic - event.frame.received_monotonic)
        elif event.kind in {"disconnect", "error"}:
            disconnects += 1
            slot.mark_disconnected(event.error or event.kind)
        else:
            reconnects += 1
            slot.begin_generation(slot.snapshot(now=event.at).generation + 1)
    snapshot = slot.snapshot(now=max(previous_at, 0.0), max_age=max_age)
    return StreamProbeReport(
        schema_version=1,
        backend=descriptor.name,
        backend_version=descriptor.version,
        install=descriptor.install,
        source=descriptor.source,
        events=count,
        frames_received=snapshot.published,
        slot_overwrites=snapshot.overwritten,
        disconnects=disconnects,
        reconnects=reconnects,
        valid_frames=valid,
        invalid_frames=invalid,
        source_pts_available=source_pts,
        source_dts_available=source_dts,
        max_decode_duration_seconds=max(decode_durations) if decode_durations else None,
        condition=condition,
        decision=decision,
    )


def synthetic_events(*, count: int = 8, burst: int = 1, start: float = 0.0, period: float = 0.05) -> list[StreamEvent]:
    if type(count) is not int or count < 1 or type(burst) is not int or burst < 1:
        raise ValueError("count and burst must be positive integers")
    if not math.isfinite(start) or start < 0 or not math.isfinite(period) or period <= 0:
        raise ValueError("start and period must be finite and valid")
    events: list[StreamEvent] = []
    timestamp = start
    for frame_id in range(1, count + 1):
        events.append(
            StreamEvent(
                timestamp,
                "frame",
                DecodedFrame(frame_id, bytes([frame_id & 0xFF]), 1, 1, "gray", timestamp, timestamp, decoder_generation=1),
            )
        )
        if frame_id % burst == 0:
            timestamp += period
    return events


def probe_live(
    url: str,
    *,
    width: int,
    height: int,
    duration: float,
    max_age: float = 0.5,
    executable: str = "ffmpeg",
) -> StreamProbeReport:
    """Run a bounded live probe through the same worker used by consumers."""

    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration must be finite and positive")
    descriptor = FFmpegDecoderBackend.descriptor_for(executable)
    worker = DecoderWorker(
        lambda generation: FFmpegDecoderBackend(
            url,
            width=width,
            height=height,
            executable=executable,
        )
    )
    events: list[StreamEvent] = []
    started = time.monotonic()
    last_published = 0
    last_generation = 0
    try:
        worker.start()
        while time.monotonic() - started < duration:
            now = time.monotonic()
            snapshot = worker.snapshot(now=now, max_age=max_age)
            if snapshot.generation != last_generation:
                if last_generation:
                    events.append(StreamEvent(now, "reconnect"))
                last_generation = snapshot.generation
            if snapshot.published > last_published and snapshot.frame is not None:
                events.append(StreamEvent(now, "frame", snapshot.frame))
                last_published = snapshot.published
            if not snapshot.connected and snapshot.error is not None:
                if not events or events[-1].kind not in {"disconnect", "error"}:
                    events.append(StreamEvent(now, "disconnect", error=snapshot.error))
            time.sleep(0.01)
    finally:
        worker.stop(timeout=2.0)
    return probe_events(
        events,
        descriptor=descriptor,
        max_age=max_age,
        condition="live",
        decision="measurement-only",
    )


__all__ = [
    "DecodedFrame",
    "DecodedFrameSnapshot",
    "DecoderDescriptor",
    "DecoderDisconnected",
    "DecoderError",
    "DecoderWorker",
    "FFmpegDecoderBackend",
    "LatestDecodedFrameSlot",
    "StreamEvent",
    "StreamProbeReport",
    "probe_events",
    "probe_live",
    "synthetic_events",
]
