#!/usr/bin/env python3
"""Simulation-only interactive transport for the installed StampFly Ecosystem SILS.

This module connects the host to the *installed* StampFly Ecosystem SILS by
launching its built ``emu_vehicle`` directly.  The upstream CLI entrypoint
``sf sils fly`` is **not** the transport seam: ``lib/sfcli/commands/sils.py``
``run_fly()`` checks ``sys.stdin.isatty()`` and refuses to run without an
interactive terminal, so its stdin is raw keyboard input rather than a pipe.
The stable seam is the process that ``sf sils fly`` itself launches:

* ``<root>/simulator/sils/build/emu_vehicle``
* ``<root>/simulator/sils/models/stampfly.xml``
* argv ``[emu_vehicle, model, <duration_us>]`` with ``shell=False``
* env ``SILS_EMU_REALTIME=1`` and ``SILS_EMU_RC_STDIN=1`` over the inherited env

``emu_vehicle`` accepts line-oriented ``rc <roll> <pitch> <yaw> <throttle>``
commands (plus ``quit``) on stdin and emits structured
``STATE t=... alt=... roll=... pitch=... yaw=... mode=... vbatt=...`` lines on
stdout.  Upstream emits attitude angles in **degrees** despite the names lacking
units; this transport converts them to radians at the parse boundary.

The transport is deliberately narrow and fail-closed:

* it resolves an explicit or narrowly defaulted installed root, verifies that
  the built ``emu_vehicle`` and model exist, and verifies the required read-only
  source seam markers (``SILS_EMU_RC_STDIN``, the ``rc`` line format, and the
  unique ``STATE t=`` format) before launching; incompatible builds raise
  ``SilsUnsupportedBuild`` instead of guessing the protocol;
* it launches an allow-listed argv array with ``shell=False`` and never opens a
  serial/USB device, never selects a port, never sends an ARM/land/disarm
  command, and never commands a non-zero throttle through the policy adapters;
* it sends a safe centered RC frame immediately after launch, validates the
  ``STATE`` schema strictly, enforces strict upstream ``t`` monotonicity, tracks
  a *local* receive sequence (transport metadata only, not an upstream field),
  and faults on missing/malformed/stale telemetry;
* it faults closed on process exit, broken stdin, and read timeout;
* ``close()`` sends only ``quit`` then bounds the wait and terminates/kills its
  own child.

Everything produced here is ``provider=stampfly_ecosystem``,
``evidence_kind=simulation``, ``simulation=true``, ``flight_qualified=false``.
This is Milestone A (vehicle/control/telemetry closed loop).  It makes **no**
camera/perception closed-loop claim: simulator telemetry is never routed into
``host/vision.py`` and is only translated into the existing
``host/mission.py``/``host/control_loop.py`` health and scheduling structures.

Live integration smoke is currently BLOCKED / NOT RUN: the installed artifact
``simulator/sils/build/emu_vehicle`` does not exist in the checked environment.
This module fails closed for that case and never fakes a live PASS.  The batch
wrapper ``host/stampfly_sim.py`` (``sf sim headless`` -> ``.sflog.zip``) remains a
one-way evidence adapter and is not interactive; ``host/flight_sim.py`` remains
the deterministic, dependency-free plant.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass
import json
import math
import os
import re
import selectors
import subprocess
import sys
import time
from typing import Callable, Mapping, Optional, Protocol, Sequence

try:  # pragma: no cover - exercised through the package import
    from .control_loop import ControlIntent, ControlScheduler, SchedulerState
    from .mission import (
        ActionKind,
        HealthSnapshot,
        MissionConfig,
        MissionSupervisor,
        OperatorEvent,
    )
    from .stampfly import ALT_MODE_MANUAL, CONTROL_MODE_ANGLE
except ImportError:  # pragma: no cover - script entrypoint
    from control_loop import ControlIntent, ControlScheduler, SchedulerState
    from mission import (
        ActionKind,
        HealthSnapshot,
        MissionConfig,
        MissionSupervisor,
        OperatorEvent,
    )
    from stampfly import ALT_MODE_MANUAL, CONTROL_MODE_ANGLE


PROVIDER = "stampfly_ecosystem"
EVIDENCE_KIND = "simulation"
BACKEND = "sils-emu"

EMU_VEHICLE_RELATIVE: tuple[str, ...] = ("simulator", "sils", "build", "emu_vehicle")
MODEL_RELATIVE: tuple[str, ...] = ("simulator", "sils", "models", "stampfly.xml")
DEFAULT_ECOSYSTEM_ROOT = "~/src/stampfly_ecosystem"
ECOSYSTEM_ROOT_ENV = "STAMPFLY_ECOSYSTEM_ROOT"
SOURCE_SEAM_FILES: tuple[str, ...] = (
    "simulator/sils/devices/rc_stdin.cpp",
    "simulator/sils/emu/emu_main.cpp",
    "lib/sfcli/commands/sils.py",
)

DEFAULT_DURATION_SECONDS = 5.0
MIN_DURATION_SECONDS = 0.001
MAX_DURATION_SECONDS = 600.0

RC_MIN = 0
RC_MAX = 4095
RC_CENTER = 2048

DEFAULT_STALE_TIMEOUT_SECONDS = 1.0
MIN_STALE_TIMEOUT_SECONDS = 0.001
MAX_STALE_TIMEOUT_SECONDS = 60.0
DEFAULT_READ_TIMEOUT_SECONDS = 5.0
MIN_READ_TIMEOUT_SECONDS = 0.001
MAX_READ_TIMEOUT_SECONDS = 120.0
DEFAULT_CLOSE_GRACE_SECONDS = 2.0
MAX_CLOSE_GRACE_SECONDS = 30.0
DEFAULT_MAX_LINE_BYTES = 8192
DEFAULT_MAX_OUTPUT_LINES = 256
MAX_POLL_ITERATIONS = 1_000_000

_SAFE_MISSION_ACTIONS = frozenset(
    {
        ActionKind.NONE,
        ActionKind.BEGIN_PREFLIGHT,
        ActionKind.REQUEST_SAFE_RECOVERY,
        ActionKind.COMPLETE,
    }
)

_NUMBER = r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
_STATE_RE = re.compile(
    r"STATE t=(?P<t>{n}) alt=(?P<alt>{n}) roll=(?P<roll>{n}) "
    r"pitch=(?P<pitch>{n}) yaw=(?P<yaw>{n}) mode=(?P<mode>\S+) "
    r"vbatt=(?P<vbatt>{n})".format(n=_NUMBER)
)
_STATE_FIELDS = ("t", "alt", "roll", "pitch", "yaw", "vbatt")


class StampFlySilsError(RuntimeError):
    """Base class for fail-closed SILS transport errors."""


class UnsafeSilsInvocationError(StampFlySilsError):
    """Raised when an invocation/command shape is not allow-listed."""


class SilsUnsupportedBuild(StampFlySilsError):
    """Raised when the installed build does not expose the required emu seam."""

    def __init__(self, message: str, *, diagnostics: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = dict(diagnostics or {})


class SilsNotFound(SilsUnsupportedBuild):
    """Raised when the installed SILS root cannot be resolved."""


class SilsProcessStartError(StampFlySilsError):
    """Raised when the simulator process cannot be started or read."""


class SilsProcessExit(StampFlySilsError):
    """Raised when the simulator process exits unexpectedly."""

    def __init__(self, message: str, *, returncode: Optional[int] = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class SilsBrokenPipe(StampFlySilsError):
    """Raised when the simulator stdin can no longer be written."""


class SilsTimeout(StampFlySilsError):
    """Raised when no usable telemetry arrives before the bounded timeout."""


class SilsTelemetryError(StampFlySilsError):
    """Base class for malformed/missing/stale telemetry."""


class SilsProtocolError(SilsTelemetryError):
    """Raised when a telemetry line violates the STATE schema/monotonicity."""


class SilsStaleTelemetry(SilsTelemetryError):
    """Raised when the newest telemetry exceeded the staleness timeout."""


class UnsafeRcCommandError(StampFlySilsError):
    """Raised when a command would leave the safe simulation envelope."""


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    return float(value)


def _bounded_seconds(value: object, name: str, *, minimum: float, maximum: float) -> float:
    seconds = _finite(value, name)
    if seconds < minimum or seconds > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum} seconds")
    return seconds


def _redact(text: str) -> str:
    home = os.path.expanduser("~")
    if isinstance(home, str) and len(home) > 1 and home != "/":
        return text.replace(home, "~")
    return text


def _require_path_argument(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise UnsafeSilsInvocationError(f"{name} must be a non-empty string")
    if any(character in value for character in ("\x00", "\n", "\r")):
        raise UnsafeSilsInvocationError(f"{name} must not contain control characters")
    return value


def _bounded_duration(value: object) -> float:
    duration = _finite(value, "duration_seconds")
    if duration < MIN_DURATION_SECONDS or duration > MAX_DURATION_SECONDS:
        raise UnsafeSilsInvocationError(
            f"duration_seconds must be between {MIN_DURATION_SECONDS} and {MAX_DURATION_SECONDS}"
        )
    return duration


# Required read-only upstream seam markers.  The installed source must contain
# these before this transport will speak the protocol; otherwise an incompatible
# build fails closed with ``SilsUnsupportedBuild`` instead of guessing.
_SEAM_MARKER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("SILS_EMU_RC_STDIN", re.compile(r"SILS_EMU_RC_STDIN")),
    ("rc_command_format", re.compile(r"rc\s+\S")),
    ("state_format", re.compile(r"STATE t=")),
)
_SEAM_SCAN_SUFFIXES = (".cpp", ".cc", ".cxx", ".hpp", ".h", ".py")
_SEAM_SCAN_MAX_FILES = 200
_SEAM_SCAN_MAX_BYTES = 2_000_000


@dataclass(frozen=True)
class SilsRootResolution:
    """Resolution of the installed StampFly Ecosystem SILS root and artifacts."""

    found: bool
    root: Optional[str]
    source: str
    emu_path: Optional[str]
    model_path: Optional[str]
    diagnostics: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "found": self.found,
            "root": _redact(self.root) if self.root else self.root,
            "source": self.source,
            "emu_path": _redact(self.emu_path) if self.emu_path else self.emu_path,
            "model_path": _redact(self.model_path) if self.model_path else self.model_path,
            "diagnostics": {
                key: (_redact(value) if isinstance(value, str) and key.endswith(("path", "root")) else value)
                for key, value in self.diagnostics.items()
            },
        }


def resolve_sils_root(
    explicit_root: Optional[str] = None,
    *,
    environ: Mapping[str, str] | None = None,
    expanduser: Callable[[str], str] = os.path.expanduser,
    isdir: Callable[[str], bool] = os.path.isdir,
    isfile: Callable[[str], bool] = os.path.isfile,
    is_executable: Callable[[str], bool] = lambda path: os.access(path, os.X_OK),
) -> SilsRootResolution:
    """Resolve the installed SILS root, then check the built emu artifacts.

    Resolution order is explicit argument -> ``STAMPFLY_ECOSYSTEM_ROOT`` ->
    the narrow ``~/src/stampfly_ecosystem`` default.  The chosen source is always
    recorded in diagnostics; a default is never claimed to exist without a
    directory check.
    """

    env = dict(os.environ if environ is None else environ)
    if explicit_root is not None:
        source = "explicit"
        candidate: Optional[str] = explicit_root
    elif env.get(ECOSYSTEM_ROOT_ENV):
        source = "environment"
        candidate = env[ECOSYSTEM_ROOT_ENV]
    else:
        source = "default_home"
        try:
            candidate = expanduser(DEFAULT_ECOSYSTEM_ROOT)
        except (KeyError, OSError, RuntimeError):
            candidate = None

    diagnostics: dict[str, object] = {"source": source, "requested_root": candidate}
    if not isinstance(candidate, str) or not candidate:
        diagnostics["reason"] = "root_unresolved"
        return SilsRootResolution(False, None, source, None, None, diagnostics)
    if any(character in candidate for character in ("\x00", "\n", "\r")):
        diagnostics["reason"] = "root_control_characters"
        return SilsRootResolution(False, None, source, None, None, diagnostics)
    if not isdir(candidate):
        diagnostics["reason"] = "root_not_a_directory"
        return SilsRootResolution(False, None, source, None, None, diagnostics)

    root = candidate
    emu_path = os.path.join(root, *EMU_VEHICLE_RELATIVE)
    model_path = os.path.join(root, *MODEL_RELATIVE)
    emu_exists = isfile(emu_path)
    emu_executable = emu_exists and is_executable(emu_path)
    model_exists = isfile(model_path)

    diagnostics.update(
        {
            "emu_path": emu_path,
            "emu_exists": emu_exists,
            "emu_executable": emu_executable,
            "model_path": model_path,
            "model_exists": model_exists,
        }
    )
    if not emu_exists:
        diagnostics["reason"] = "emu_vehicle_missing"
    elif not emu_executable:
        diagnostics["reason"] = "emu_vehicle_not_executable"
    elif not model_exists:
        diagnostics["reason"] = "model_missing"
    else:
        diagnostics["reason"] = "artifacts_present"

    found = emu_exists and emu_executable and model_exists
    return SilsRootResolution(found, root, source, emu_path, model_path, diagnostics)


def _collect_seam_sources(root: str) -> list[tuple[str, str]]:
    """Read bounded read-only source text for seam-marker verification."""

    collected: list[tuple[str, str]] = []
    total_bytes = 0
    for relative in SOURCE_SEAM_FILES:
        path = os.path.join(root, relative)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read(_SEAM_SCAN_MAX_BYTES)
        except OSError:
            continue
        collected.append((path, text))
        total_bytes += len(text)
    if collected:
        return collected

    for base in ("simulator", "lib"):
        base_path = os.path.join(root, base)
        if not os.path.isdir(base_path):
            continue
        for dirpath, dirnames, filenames in os.walk(base_path):
            dirnames[:] = [name for name in sorted(dirnames) if not name.startswith(".")]
            for filename in sorted(filenames):
                if not filename.endswith(_SEAM_SCAN_SUFFIXES):
                    continue
                if len(collected) >= _SEAM_SCAN_MAX_FILES or total_bytes >= _SEAM_SCAN_MAX_BYTES:
                    return collected
                path = os.path.join(dirpath, filename)
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as handle:
                        text = handle.read(64_000)
                except OSError:
                    continue
                collected.append((path, text))
                total_bytes += len(text)
    return collected


def verify_sils_source_markers(
    root: str,
    *,
    markers: Optional[Sequence[tuple[str, re.Pattern[str]]]] = None,
) -> None:
    """Fail closed unless the installed source exposes the required emu seam."""

    if not isinstance(root, str) or not root:
        raise SilsUnsupportedBuild("SILS root must be a non-empty string")
    patterns = tuple(markers) if markers is not None else _SEAM_MARKER_PATTERNS
    sources = _collect_seam_sources(root)
    if not sources:
        raise SilsUnsupportedBuild(
            f"installed SILS source seam not found under {_redact(root)}; "
            "cannot verify the rc/STATE protocol"
        )
    combined = "\n".join(text for _path, text in sources)
    missing = [name for name, pattern in patterns if not pattern.search(combined)]
    if missing:
        raise SilsUnsupportedBuild(
            "installed SILS source is missing required seam markers: " + ", ".join(missing)
        )


def build_sils_emu_argv(emu_path: object, model_path: object, duration_seconds: object) -> tuple[str, ...]:
    """Build the only supported invocation of the built ``emu_vehicle``.

    The argv is ``[emu_vehicle, stampfly.xml, <duration_us>]``; upstream
    ``run_fly()`` launches exactly this shape on non-Windows platforms.
    """

    executable = _require_path_argument(emu_path, "emu_vehicle path")
    model = _require_path_argument(model_path, "model path")
    duration = _bounded_duration(duration_seconds)
    return (executable, model, str(int(duration * 1e6)))


def sils_emu_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Apply the upstream realtime/RC-stdin environment over the inherited env."""

    env = dict(os.environ if base is None else base)
    env["SILS_EMU_REALTIME"] = "1"
    env["SILS_EMU_RC_STDIN"] = "1"
    return env


@dataclass(frozen=True)
class RcCommand:
    """One ``rc`` stick frame in upstream ADC counts (0..4095, center 2048)."""

    roll: int
    pitch: int
    yaw: int
    throttle: int

    def __post_init__(self) -> None:
        for name, value in (
            ("roll", self.roll),
            ("pitch", self.pitch),
            ("yaw", self.yaw),
            ("throttle", self.throttle),
        ):
            if type(value) is not int:
                raise UnsafeRcCommandError(f"{name} must be an integer ADC value")
            if not RC_MIN <= value <= RC_MAX:
                raise UnsafeRcCommandError(f"{name} must be in [{RC_MIN}, {RC_MAX}]")

    def serialize(self) -> str:
        return f"rc {self.roll} {self.pitch} {self.yaw} {self.throttle}"

    def to_dict(self) -> dict[str, int]:
        return {
            "roll": self.roll,
            "pitch": self.pitch,
            "yaw": self.yaw,
            "throttle": self.throttle,
        }

    @classmethod
    def neutral(cls) -> "RcCommand":
        """All four axes centered at the upstream ADC neutral (2048)."""

        return cls(RC_CENTER, RC_CENTER, RC_CENTER, RC_CENTER)

    @classmethod
    def safe(cls) -> "RcCommand":
        """The non-arming safe frame; identical to upstream neutral.

        Upstream ``_fly_adc(0)`` maps neutral for *all four* axes, including
        throttle, to ADC ~2048 and the RC-stdin initial ``g_thr`` is
        ``kAdcCentre``.  This transport never arms, so the safe frame is the
        upstream neutral frame rather than a zero-throttle frame.
        """

        return cls.neutral()

    @classmethod
    def from_normalized(cls, roll: float, pitch: float, yaw: float, throttle: float) -> "RcCommand":
        """Map normalized sticks to ADC counts.

        Attitude axes span ``[-1, 1]`` -> ``[RC_MIN, RC_MAX]``.  Throttle spans
        ``[0, 1]`` where ``0.0`` maps to ``RC_CENTER`` (upstream neutral, not a
        minimum) and positive values scale from center to ``RC_MAX``.  The
        simulation adapter only ever passes throttle ``0.0`` and never arms.
        """

        for name, value in (("roll", roll), ("pitch", pitch), ("yaw", yaw)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise UnsafeRcCommandError(f"{name} must be finite")
            if not -1.0 <= float(value) <= 1.0:
                raise UnsafeRcCommandError(f"{name} must be in [-1, 1]")
        if isinstance(throttle, bool) or not isinstance(throttle, (int, float)) or not math.isfinite(float(throttle)):
            raise UnsafeRcCommandError("throttle must be finite")
        if not 0.0 <= float(throttle) <= 1.0:
            raise UnsafeRcCommandError("throttle must be in [0, 1]")

        def to_adc(value: float) -> int:
            return min(RC_MAX, max(RC_MIN, round((float(value) + 1.0) * 0.5 * RC_MAX)))

        def throttle_to_adc(value: float) -> int:
            # 0.0 is the upstream neutral center, not a minimum; positive
            # normalized values scale from center toward full-scale.  This
            # transport is non-arming, so callers use 0.0 (RC_CENTER) only.
            if float(value) == 0.0:
                return RC_CENTER
            return min(RC_MAX, max(RC_CENTER, RC_CENTER + round(float(value) * (RC_MAX - RC_CENTER))))

        return cls(
            to_adc(roll),
            to_adc(pitch),
            to_adc(yaw),
            throttle_to_adc(throttle),
        )


@dataclass(frozen=True)
class SilsTelemetry:
    """One strict ``STATE`` sample plus local transport receive metadata."""

    sim_time: float
    altitude_m: float
    roll_rad: float
    pitch_rad: float
    yaw_rad: float
    mode: str
    vbatt: float
    receive_sequence: int
    received_monotonic: float
    raw_line: str
    provider: str = PROVIDER
    evidence_kind: str = EVIDENCE_KIND
    simulation: bool = True
    flight_qualified: bool = False

    def age_seconds(self, now_monotonic: float) -> float:
        return max(0.0, float(now_monotonic) - self.received_monotonic)

    @property
    def mode_fields(self) -> tuple[str, ...]:
        return tuple(self.mode.split(":"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "provider": self.provider,
            "evidence_kind": self.evidence_kind,
            "simulation": self.simulation,
            "flight_qualified": self.flight_qualified,
            "sim_time": self.sim_time,
            "altitude_m": self.altitude_m,
            "roll_rad": self.roll_rad,
            "pitch_rad": self.pitch_rad,
            "yaw_rad": self.yaw_rad,
            "mode": self.mode,
            "vbatt": self.vbatt,
            "receive_sequence": self.receive_sequence,
            "received_monotonic": self.received_monotonic,
        }


def parse_state_line(
    line: str,
    *,
    receive_sequence: int = 0,
    received_monotonic: float = 0.0,
) -> SilsTelemetry:
    """Parse exactly one upstream ``STATE`` line, fail-closed on any deviation.

    Upstream ``emu_main.cpp`` publishes ``roll/pitch/yaw`` in **degrees** despite
    the field names lacking units, so the parsed radians are produced here at the
    boundary.  Downstream health thresholds are all in radians.
    """

    if not isinstance(line, str):
        raise SilsProtocolError("STATE line must be a string")
    if type(receive_sequence) is not int or receive_sequence < 0:
        raise ValueError("receive_sequence must be a non-negative integer")
    text = line.strip()
    match = _STATE_RE.fullmatch(text)
    if match is None:
        raise SilsProtocolError(f"malformed STATE line: {line!r}")

    values: dict[str, float] = {}
    for name in _STATE_FIELDS:
        raw = match.group(name)
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:  # pragma: no cover - regex already constrains
            raise SilsProtocolError(f"STATE has non-numeric {name}: {line!r}") from exc
        if not math.isfinite(value):
            raise SilsProtocolError(f"STATE has non-finite {name}: {line!r}")
        values[name] = value

    mode = match.group("mode")
    if mode.count(":") != 1:
        raise SilsProtocolError(f"STATE mode must contain exactly one ':': {line!r}")

    return SilsTelemetry(
        sim_time=values["t"],
        altitude_m=values["alt"],
        roll_rad=math.radians(values["roll"]),
        pitch_rad=math.radians(values["pitch"]),
        yaw_rad=math.radians(values["yaw"]),
        mode=mode,
        vbatt=values["vbatt"],
        receive_sequence=receive_sequence,
        received_monotonic=float(received_monotonic),
        raw_line=text,
    )


@dataclass(frozen=True)
class SilsInvocation:
    """A validated, allow-listed simulation invocation (argv array plus env)."""

    mode: str
    argv: tuple[str, ...]
    env: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "argv": tuple(_redact(part) for part in self.argv),
            "env_keys": sorted(self.env),
        }


class SilsProcess(Protocol):
    """Minimal process interface, injectable for deterministic fake tests."""

    def write_line(self, line: str) -> None: ...

    def read_line(self, timeout: float) -> Optional[str]: ...

    def poll(self) -> Optional[int]: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float) -> Optional[int]: ...

    def close_streams(self) -> None: ...


class SilsLauncher(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        cwd: Optional[str],
    ) -> SilsProcess: ...


class PopenSilsProcess:
    """Real ``subprocess.Popen`` process with non-blocking pipe reads."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Optional[str] = None,
        max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
        max_stderr_chunks: int = 64,
    ) -> None:
        if not argv or any(not isinstance(part, str) for part in argv):
            raise UnsafeSilsInvocationError("argv must be a non-empty sequence of strings")
        if type(max_line_bytes) is not int or max_line_bytes < 64:
            raise ValueError("max_line_bytes must be an integer >= 64")
        self._max_line_bytes = max_line_bytes
        try:
            self._process = subprocess.Popen(
                list(argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                bufsize=0,
                env=dict(env) if env is not None else None,
                cwd=cwd,
            )
        except FileNotFoundError as exc:
            raise SilsNotFound(f"simulator executable disappeared: {argv[0]}") from exc
        except OSError as exc:
            raise SilsProcessStartError(f"failed to start simulator: {exc}") from exc

        self._stdout = self._process.stdout
        self._stderr = self._process.stderr
        if self._stdout is None or self._stderr is None:  # pragma: no cover - PIPE guarantees
            raise SilsProcessStartError("simulator pipes are unavailable")
        self._stdout_buffer = bytearray()
        self._stderr_tail: deque[bytes] = deque(maxlen=max_stderr_chunks)
        self._stdout_eof = False
        self._stderr_eof = False
        self._selector = selectors.DefaultSelector()
        if hasattr(os, "set_blocking"):
            for stream in (self._stdout, self._stderr):
                try:
                    os.set_blocking(stream.fileno(), False)
                except (OSError, ValueError):  # pragma: no cover - platform dependent
                    pass
        self._selector.register(self._stdout, selectors.EVENT_READ, "stdout")
        self._selector.register(self._stderr, selectors.EVENT_READ, "stderr")

    def write_line(self, line: str) -> None:
        if "\x00" in line or "\n" in line or "\r" in line:
            raise UnsafeRcCommandError("command must be a single line without control characters")
        if self._process.poll() is not None:
            raise SilsProcessExit("simulator process has already exited", returncode=self._process.returncode)
        stdin = self._process.stdin
        if stdin is None:  # pragma: no cover - PIPE guarantees
            raise SilsBrokenPipe("simulator stdin is unavailable")
        payload = (line + "\n").encode("utf-8")
        try:
            stdin.write(payload)
            stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise SilsBrokenPipe("simulator stdin is closed") from exc
        except OSError as exc:
            raise SilsBrokenPipe(f"simulator stdin write failed: {exc}") from exc

    def read_line(self, timeout: float) -> Optional[str]:
        remaining = max(0.0, float(timeout))
        deadline = time.monotonic() + remaining
        while True:
            line = self._extract_stdout_line()
            if line is not None:
                return line
            if self._stdout_eof:
                return None
            budget = deadline - time.monotonic()
            if budget <= 0:
                return None
            self._pump(budget)

    def poll(self) -> Optional[int]:
        return self._process.poll()

    def terminate(self) -> None:
        if self._process.poll() is None:
            try:
                self._process.terminate()
            except OSError:  # pragma: no cover - race with exit
                pass

    def kill(self) -> None:
        if self._process.poll() is None:
            try:
                self._process.kill()
            except OSError:  # pragma: no cover - race with exit
                pass

    def wait(self, timeout: float) -> Optional[int]:
        try:
            return self._process.wait(timeout=max(0.0, float(timeout)))
        except subprocess.TimeoutExpired:
            return None

    def close_streams(self) -> None:
        try:
            self._selector.close()
        except Exception:  # pragma: no cover - defensive
            pass
        for stream in (self._stdout, self._stderr, self._process.stdin):
            if stream is None:
                continue
            try:
                stream.close()
            except OSError:  # pragma: no cover - defensive
                pass

    def stderr_tail(self) -> str:
        return b"".join(self._stderr_tail).decode("utf-8", errors="replace")

    def _extract_stdout_line(self) -> Optional[str]:
        newline = self._stdout_buffer.find(b"\n")
        if newline < 0:
            if len(self._stdout_buffer) > self._max_line_bytes:
                self._stdout_buffer.clear()
                raise SilsProcessStartError("simulator output line exceeded the bounded buffer")
            return None
        raw = bytes(self._stdout_buffer[:newline])
        del self._stdout_buffer[: newline + 1]
        return raw.decode("utf-8", errors="replace").rstrip("\r")

    def _pump(self, budget: float) -> None:
        if not self._selector.get_map():
            time.sleep(min(max(0.0, budget), 0.01))
            self._maybe_mark_eof()
            return
        try:
            events = self._selector.select(budget)
        except (OSError, ValueError) as exc:  # pragma: no cover - defensive
            raise SilsProcessStartError(f"simulator select failed: {exc}") from exc
        if not events:
            self._maybe_mark_eof()
            return
        for key, _ in events:
            stream = key.fileobj
            tag = key.data
            try:
                chunk = os.read(stream.fileno(), 4096)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                # EBADF/EOF-style errors on a closed pipe are treated as EOF.
                self._mark_eof(tag)
                continue
            if not chunk:
                self._mark_eof(tag)
                continue
            if tag == "stdout":
                self._stdout_buffer.extend(chunk)
            else:
                self._stderr_tail.append(bytes(chunk[-1024:]))

    def _mark_eof(self, tag: str) -> None:
        if tag == "stdout":
            self._stdout_eof = True
        else:
            self._stderr_eof = True
        for key in list(self._selector.get_map().values()):
            if key.data == tag:
                try:
                    self._selector.unregister(key.fileobj)
                except (KeyError, ValueError):  # pragma: no cover - double unregister
                    pass

    def _maybe_mark_eof(self) -> None:
        if self._stdout_eof or self._process.poll() is None:
            return
        try:
            chunk = os.read(self._stdout.fileno(), 4096)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:  # pragma: no cover - closed pipe
            chunk = b""
        if chunk:
            self._stdout_buffer.extend(chunk)
        else:
            self._mark_eof("stdout")


def launch_popen(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Optional[str],
) -> SilsProcess:
    return PopenSilsProcess(argv, env=env, cwd=cwd)


@dataclass(frozen=True)
class SilsProvenance:
    provider: str
    evidence_kind: str
    simulation: bool
    flight_qualified: bool
    backend: str
    mode: Optional[str]
    executable: Optional[str]
    argv: tuple[str, ...]
    command_count: int
    receive_sequence: int
    last_sim_time: Optional[float]
    stale_timeout_seconds: float
    read_timeout_seconds: float
    fault: Optional[str]
    faults: tuple[str, ...]
    build_verified: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class StampFlySimTransport:
    """Fail-closed simulation-only transport over the installed SILS seam."""

    def __init__(
        self,
        *,
        ecosystem_root: Optional[str] = None,
        root_resolver: Callable[..., SilsRootResolution] = resolve_sils_root,
        source_marker_probe: Optional[Callable[[str], None]] = None,
        launcher: Optional[SilsLauncher] = None,
        env: Mapping[str, str] | None = None,
        cwd: Optional[str] = None,
        clock: Callable[[], float] = time.monotonic,
        duration_seconds: float = DEFAULT_DURATION_SECONDS,
        stale_timeout_seconds: float = DEFAULT_STALE_TIMEOUT_SECONDS,
        read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS,
        close_grace_seconds: float = DEFAULT_CLOSE_GRACE_SECONDS,
        max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
        max_output_lines: int = DEFAULT_MAX_OUTPUT_LINES,
    ) -> None:
        if type(max_output_lines) is not int or max_output_lines <= 0:
            raise ValueError("max_output_lines must be a positive integer")
        if type(max_line_bytes) is not int or max_line_bytes < 64:
            raise ValueError("max_line_bytes must be an integer >= 64")
        if not callable(root_resolver):
            raise TypeError("root_resolver must be callable")
        if source_marker_probe is not None and not callable(source_marker_probe):
            raise TypeError("source_marker_probe must be callable or None")

        self.duration_seconds = _bounded_seconds(
            duration_seconds,
            "duration_seconds",
            minimum=MIN_DURATION_SECONDS,
            maximum=MAX_DURATION_SECONDS,
        )
        self.stale_timeout_seconds = _bounded_seconds(
            stale_timeout_seconds,
            "stale_timeout_seconds",
            minimum=MIN_STALE_TIMEOUT_SECONDS,
            maximum=MAX_STALE_TIMEOUT_SECONDS,
        )
        self.read_timeout_seconds = _bounded_seconds(
            read_timeout_seconds,
            "read_timeout_seconds",
            minimum=MIN_READ_TIMEOUT_SECONDS,
            maximum=MAX_READ_TIMEOUT_SECONDS,
        )
        self.close_grace_seconds = _bounded_seconds(
            close_grace_seconds,
            "close_grace_seconds",
            minimum=0.0,
            maximum=MAX_CLOSE_GRACE_SECONDS,
        )
        self.max_line_bytes = max_line_bytes
        self.max_output_lines = max_output_lines
        self.build_verified = False

        self._ecosystem_root = ecosystem_root
        self._root_resolver = root_resolver
        self._source_marker_probe = source_marker_probe or verify_sils_source_markers
        self._clock = clock
        self._launcher: SilsLauncher = launcher or launch_popen
        self._env = env
        self._cwd = cwd
        self.resolution: Optional[SilsRootResolution] = self._root_resolver(ecosystem_root)

        self._process: Optional[SilsProcess] = None
        self._invocation: Optional[SilsInvocation] = None
        self._started = False
        self._closed = False
        self._fault: Optional[str] = None
        self._faults: list[str] = []
        self._receive_sequence = 0
        self._command_count = 0
        self._last_telemetry: Optional[SilsTelemetry] = None
        self._last_receive_monotonic: Optional[float] = None
        self._output_tail: deque[str] = deque(maxlen=max_output_lines)

    # -- introspection -------------------------------------------------
    @property
    def clock(self) -> Callable[[], float]:
        return self._clock

    @property
    def started(self) -> bool:
        return self._started

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def fault_reason(self) -> Optional[str]:
        return self._fault

    @property
    def is_ready(self) -> bool:
        return self._started and not self._closed and self._fault is None and self._process is not None

    @property
    def telemetry(self) -> Optional[SilsTelemetry]:
        return self._last_telemetry

    @property
    def receive_sequence(self) -> int:
        return self._receive_sequence

    @property
    def command_count(self) -> int:
        return self._command_count

    @property
    def invocation(self) -> Optional[SilsInvocation]:
        return self._invocation

    def output_tail(self) -> tuple[str, ...]:
        return tuple(self._output_tail)

    def diagnostics(self) -> dict[str, object]:
        if self.resolution is None:
            return {"found": False, "reason": "not_resolved"}
        return self.resolution.to_dict()

    def provenance(self) -> SilsProvenance:
        resolution = self.resolution
        return SilsProvenance(
            provider=PROVIDER,
            evidence_kind=EVIDENCE_KIND,
            simulation=True,
            flight_qualified=False,
            backend=BACKEND,
            mode=self._invocation.mode if self._invocation is not None else None,
            executable=_redact(resolution.emu_path) if resolution and resolution.emu_path else None,
            argv=tuple(_redact(part) for part in (self._invocation.argv if self._invocation else ())),
            command_count=self._command_count,
            receive_sequence=self._receive_sequence,
            last_sim_time=self._last_telemetry.sim_time if self._last_telemetry else None,
            stale_timeout_seconds=self.stale_timeout_seconds,
            read_timeout_seconds=self.read_timeout_seconds,
            fault=self._fault,
            faults=tuple(self._faults),
            build_verified=self.build_verified,
        )

    def fault(self, reason: str) -> None:
        if not isinstance(reason, str) or not reason:
            raise ValueError("fault reason must be a non-empty string")
        if self._fault is None:
            self._fault = reason
        if reason not in self._faults:
            self._faults.append(reason)

    # -- lifecycle -----------------------------------------------------
    def start(self) -> SilsInvocation:
        if self._closed:
            raise StampFlySilsError("transport is closed")
        if self._started:
            raise StampFlySilsError("transport is already started")
        if self._fault is not None:
            raise StampFlySilsError(f"transport is fault-latched: {self._fault}")

        resolution = self.resolution
        if resolution is None or not resolution.root:
            raise SilsNotFound(
                "installed SILS root could not be resolved",
                diagnostics=resolution.to_dict() if resolution is not None else {},
            )
        if not resolution.found:
            raise SilsUnsupportedBuild(
                f"installed SILS build is not usable: {resolution.diagnostics.get('reason')}",
                diagnostics=resolution.to_dict(),
            )

        self._source_marker_probe(resolution.root)
        self.build_verified = True

        argv = build_sils_emu_argv(resolution.emu_path, resolution.model_path, self.duration_seconds)
        env = sils_emu_env(self._env)
        invocation = SilsInvocation(BACKEND, argv, env)
        self._invocation = invocation
        try:
            self._process = self._launcher(argv, env=env, cwd=self._cwd)
        except StampFlySilsError:
            raise
        except FileNotFoundError as exc:
            raise SilsUnsupportedBuild("emu_vehicle disappeared during launch") from exc
        except OSError as exc:
            raise SilsProcessStartError(f"failed to start emu_vehicle: {exc}") from exc

        self._started = True
        self._last_receive_monotonic = self._clock()
        try:
            # First frame is always the non-arming safe center; upstream
            # emu_vehicle neutral for all four axes is ADC 2048.
            self.send_rc(RcCommand.safe())
        except StampFlySilsError:
            self.fault("initial_rc_failed")
            self._terminate_process()
            raise
        return invocation

    def send_rc(self, command: RcCommand) -> int:
        process = self._require_running()
        if not isinstance(command, RcCommand):
            raise UnsafeRcCommandError("command must be an RcCommand")
        try:
            process.write_line(command.serialize())
        except StampFlySilsError as exc:
            self.fault(f"stdin:{type(exc).__name__}")
            raise
        except (OSError, ValueError) as exc:
            self.fault("stdin_failure")
            raise SilsBrokenPipe(f"failed to write rc command: {exc}") from exc
        self._command_count += 1
        return self._command_count

    def read_telemetry(self, *, timeout: Optional[float] = None) -> SilsTelemetry:
        process = self._require_running()
        read_timeout = (
            self.read_timeout_seconds
            if timeout is None
            else _bounded_seconds(
                timeout,
                "timeout",
                minimum=MIN_READ_TIMEOUT_SECONDS,
                maximum=MAX_READ_TIMEOUT_SECONDS,
            )
        )
        deadline = self._clock() + read_timeout

        for _ in range(MAX_POLL_ITERATIONS):
            self._check_stale()
            remaining = deadline - self._clock()
            if remaining <= 0:
                self.fault("telemetry_timeout")
                raise SilsTimeout(f"no STATE telemetry within {read_timeout:.3f}s")

            try:
                line = process.read_line(remaining)
            except StampFlySilsError as exc:
                self.fault(f"read:{type(exc).__name__}")
                raise
            except (OSError, ValueError) as exc:
                self.fault("read_failure")
                raise SilsProcessStartError(f"simulator read failed: {exc}") from exc

            if line is None:
                returncode = process.poll()
                if returncode is not None:
                    self.fault("process_exit")
                    raise SilsProcessExit(
                        f"simulator process exited with code {returncode}", returncode=returncode
                    )
                continue

            stripped = line.strip()
            if not stripped:
                continue
            if not stripped.startswith("STATE "):
                self._record_output(stripped)
                continue

            try:
                telemetry = parse_state_line(
                    stripped,
                    receive_sequence=self._receive_sequence + 1,
                    received_monotonic=self._clock(),
                )
            except (SilsProtocolError, SilsTelemetryError) as exc:
                self._record_output(stripped)
                self.fault("malformed_state")
                raise
            self._accept(telemetry)
            return telemetry

        self.fault("telemetry_poll_exhausted")
        raise SilsTimeout("telemetry polling exhausted without a usable STATE")

    def step(self, command: Optional[RcCommand] = None) -> SilsTelemetry:
        if command is not None:
            self.send_rc(command)
        return self.read_telemetry()

    def close(self, *, timeout: Optional[float] = None) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is None:
            return
        grace = (
            self.close_grace_seconds
            if timeout is None
            else _bounded_seconds(timeout, "timeout", minimum=0.0, maximum=MAX_CLOSE_GRACE_SECONDS)
        )
        if process.poll() is None:
            try:
                process.write_line("quit")
            except (StampFlySilsError, OSError, ValueError):
                pass
            if process.wait(grace) is None:
                process.terminate()
                if process.wait(grace) is None:
                    process.kill()
                    process.wait(grace)
        process.close_streams()

    # -- internals -----------------------------------------------------
    def _terminate_process(self) -> None:
        """Boundedly stop and reap the owned emu process (no reconnect)."""

        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:  # pragma: no cover - race with exit
                pass
            if process.wait(self.close_grace_seconds) is None:
                process.kill()
                process.wait(self.close_grace_seconds)
        process.close_streams()

    def _require_running(self) -> SilsProcess:
        if self._closed:
            raise StampFlySilsError("transport is closed")
        if not self._started or self._process is None:
            raise StampFlySilsError("transport is not started")
        if self._fault is not None:
            raise StampFlySilsError(f"transport is fault-latched: {self._fault}")
        return self._process

    def _check_stale(self) -> None:
        if self._last_receive_monotonic is None:
            return
        age = self._clock() - self._last_receive_monotonic
        if age > self.stale_timeout_seconds:
            self.fault("telemetry_stale")
            raise SilsStaleTelemetry(
                f"telemetry age {age:.3f}s exceeded {self.stale_timeout_seconds:.3f}s"
            )

    def _accept(self, telemetry: SilsTelemetry) -> None:
        if self._last_telemetry is not None:
            if not telemetry.sim_time > self._last_telemetry.sim_time:
                self.fault("sim_time_non_monotonic")
                raise SilsProtocolError(
                    "upstream simulator time must strictly increase: "
                    f"{self._last_telemetry.sim_time} -> {telemetry.sim_time}"
                )
        self._receive_sequence += 1
        self._last_telemetry = telemetry
        self._last_receive_monotonic = telemetry.received_monotonic

    def _record_output(self, line: str) -> None:
        self._output_tail.append(line)


@dataclass(frozen=True)
class SilsHealthConfig:
    """Translation thresholds from SILS telemetry into existing health fields."""

    observation_max_age_seconds: float = 0.25
    telemetry_max_age_seconds: float = 0.25
    min_battery_v: float = 3.3
    max_abs_roll_rad: float = 1.4
    max_abs_pitch_rad: float = 1.4
    ground_altitude_m: float = 0.05

    def __post_init__(self) -> None:
        for name in (
            "observation_max_age_seconds",
            "telemetry_max_age_seconds",
            "max_abs_roll_rad",
            "max_abs_pitch_rad",
            "ground_altitude_m",
        ):
            if _finite(getattr(self, name), name) <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if _finite(self.min_battery_v, "min_battery_v") <= 0:
            raise ValueError("min_battery_v must be finite and positive")


def telemetry_to_health(
    telemetry: SilsTelemetry,
    *,
    now_monotonic: float,
    transport_ready: bool,
    calibration_valid: bool = False,
    capabilities: frozenset[str] = frozenset(),
    armed: bool = False,
    config: Optional[SilsHealthConfig] = None,
) -> HealthSnapshot:
    """Translate SILS telemetry into the existing ``mission.HealthSnapshot``.

    Only freshness, battery, and attitude bounds are derived from telemetry.
    ``calibration_valid``, ``capabilities`` and ``armed`` remain explicit
    caller inputs: SILS does not provide a camera/perception calibration, and
    this transport never arms.  ``observation_valid`` here reflects the SILS
    state-estimate channel, *not* a camera/vision observation.
    """

    if armed:
        raise UnsafeRcCommandError("simulation health translation never reports an armed vehicle")
    if not isinstance(telemetry, SilsTelemetry):
        raise TypeError("telemetry must be a SilsTelemetry")
    resolved = config or SilsHealthConfig()
    if type(calibration_valid) is not bool or type(transport_ready) is not bool:
        raise TypeError("calibration_valid and transport_ready must be bool")
    if not isinstance(capabilities, frozenset):
        raise TypeError("capabilities must be a frozenset")

    age = telemetry.age_seconds(now_monotonic)
    observation_valid = transport_ready and age <= resolved.observation_max_age_seconds
    telemetry_valid = transport_ready and age <= resolved.telemetry_max_age_seconds
    battery_ok = telemetry.vbatt >= resolved.min_battery_v
    within_bounds = (
        abs(telemetry.roll_rad) <= resolved.max_abs_roll_rad
        and abs(telemetry.pitch_rad) <= resolved.max_abs_pitch_rad
    )
    return HealthSnapshot(
        calibration_valid=calibration_valid,
        observation_valid=observation_valid,
        observation_age_seconds=age,
        telemetry_valid=telemetry_valid,
        telemetry_age_seconds=age,
        transport_ready=transport_ready,
        battery_ok=battery_ok,
        within_bounds=within_bounds,
        capabilities=capabilities,
        armed=False,
        grounded=telemetry.altitude_m <= resolved.ground_altitude_m,
        altitude_m=telemetry.altitude_m,
        takeoff_reached=False,
    )


class SilsControlAdapter:
    """Bridge the existing ``control_loop.ControlTransport`` onto SILS ``rc``.

    This adapter is simulation-only: it never arms, it rejects any non-zero
    throttle, it bounds the attitude stick, and it rejects protocol mode values
    that do not correspond to angle/manual.  ``best_effort_disarm`` is a no-op
    that re-centers the simulated sticks; it can never arm anything.
    """

    def __init__(self, transport: StampFlySimTransport, *, max_stick: float = 0.25) -> None:
        if not isinstance(transport, StampFlySimTransport):
            raise TypeError("transport must be a StampFlySimTransport")
        if _finite(max_stick, "max_stick") <= 0 or float(max_stick) > 1.0:
            raise ValueError("max_stick must be in (0, 1]")
        self.transport = transport
        self.max_stick = float(max_stick)
        self.commands: list[dict[str, object]] = []
        self.arm_attempts = 0
        self._wire_sequence = 0

    @property
    def disarm_count(self) -> int:
        return 0

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
    ) -> int:
        del wait_ack
        for name, value in (("roll", roll), ("pitch", pitch), ("yaw", yaw)):
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise UnsafeRcCommandError(f"{name} must be finite")
            if abs(float(value)) > self.max_stick:
                raise UnsafeRcCommandError(f"{name} exceeds the simulation stick bound")
        if not isinstance(throttle, (int, float)) or not math.isfinite(float(throttle)):
            raise UnsafeRcCommandError("throttle must be finite")
        if float(throttle) != 0.0:
            raise UnsafeRcCommandError("simulation adapter permits only zero throttle")
        if control_mode != CONTROL_MODE_ANGLE or alt_mode != ALT_MODE_MANUAL:
            raise UnsafeRcCommandError("simulation adapter permits only ANGLE/MANUAL")

        command = RcCommand.from_normalized(roll, pitch, yaw, 0.0)
        self.transport.send_rc(command)
        self._wire_sequence += 1
        self.commands.append(
            {
                "wire_sequence": self._wire_sequence,
                "roll": float(roll),
                "pitch": float(pitch),
                "yaw": float(yaw),
                "throttle": 0.0,
                "rc": command.to_dict(),
            }
        )
        return self._wire_sequence

    def best_effort_disarm(self) -> None:
        try:
            self.transport.send_rc(RcCommand.safe())
        except StampFlySilsError:
            pass


class SilsLoopFault(StampFlySilsError):
    """Raised when the simulation closed loop faults and stops."""


@dataclass(frozen=True)
class SilsLoopIteration:
    iteration: int
    decision_reason: str
    normalized_roll: float
    command: RcCommand
    sim_time_before: float
    sim_time_after: float
    roll_before: float
    roll_after: float
    pitch_before: float
    pitch_after: float
    altitude_before: float
    altitude_after: float
    receive_sequence_before: int
    receive_sequence_after: int
    mission_state: str
    health_telemetry_valid: bool
    attitude_changed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "iteration": self.iteration,
            "decision_reason": self.decision_reason,
            "normalized_roll": self.normalized_roll,
            "command": self.command.to_dict(),
            "sim_time_before": self.sim_time_before,
            "sim_time_after": self.sim_time_after,
            "roll_before": self.roll_before,
            "roll_after": self.roll_after,
            "pitch_before": self.pitch_before,
            "pitch_after": self.pitch_after,
            "altitude_before": self.altitude_before,
            "altitude_after": self.altitude_after,
            "receive_sequence_before": self.receive_sequence_before,
            "receive_sequence_after": self.receive_sequence_after,
            "mission_state": self.mission_state,
            "health_telemetry_valid": self.health_telemetry_valid,
            "attitude_changed": self.attitude_changed,
        }


@dataclass(frozen=True)
class SilsLoopResult:
    schema_version: int
    iterations: tuple[SilsLoopIteration, ...]
    provenance: dict[str, object]
    faults: tuple[str, ...]
    milestone: str = "A"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "milestone": self.milestone,
            "provider": PROVIDER,
            "evidence_kind": EVIDENCE_KIND,
            "simulation": True,
            "flight_qualified": False,
            "iterations": [iteration.to_dict() for iteration in self.iterations],
            "provenance": self.provenance,
            "faults": list(self.faults),
        }


class SilsClosedLoopDriver:
    """Simulation-only iterative closed loop over SILS + control_loop + mission.

    Each iteration computes a normalized attitude intent from the *freshest*
    received STATE, publishes it to the existing ``ControlScheduler`` (which
    sends it through ``SilsControlAdapter``), then reads the next STATE.  The
    next decision therefore depends on the previous command's effect.  The
    driver never arms and only sends bounded, zero-throttle stick probes that
    cannot leave the ground under the documented disarmed-neutral policy.
    """

    def __init__(
        self,
        transport: StampFlySimTransport,
        *,
        max_iterations: int = 4,
        max_stick: float = 0.25,
        target_roll_rad: float = 0.05,
        roll_gain_per_rad: float = 5.0,
        intent_ttl_seconds: float = 0.2,
        local_watchdog_seconds: float = 0.2,
        health_config: Optional[SilsHealthConfig] = None,
        mission_config: Optional[MissionConfig] = None,
    ) -> None:
        if not isinstance(transport, StampFlySimTransport):
            raise TypeError("transport must be a StampFlySimTransport")
        if type(max_iterations) is not int or max_iterations <= 0:
            raise ValueError("max_iterations must be a positive integer")
        if _finite(target_roll_rad, "target_roll_rad") == 0.0:
            raise ValueError("target_roll_rad must be non-zero")
        if _finite(roll_gain_per_rad, "roll_gain_per_rad") <= 0:
            raise ValueError("roll_gain_per_rad must be positive")
        if _finite(intent_ttl_seconds, "intent_ttl_seconds") <= 0:
            raise ValueError("intent_ttl_seconds must be positive")
        self.transport = transport
        self.max_iterations = max_iterations
        self.max_stick = float(max_stick)
        if self.max_stick <= 0 or self.max_stick > 1.0:
            raise ValueError("max_stick must be in (0, 1]")
        self.target_roll_rad = float(target_roll_rad)
        self.roll_gain_per_rad = float(roll_gain_per_rad)
        self.intent_ttl_seconds = float(intent_ttl_seconds)
        self.local_watchdog_seconds = float(local_watchdog_seconds)
        if not 0.0 < self.local_watchdog_seconds < 0.25:
            raise ValueError("local_watchdog_seconds must be in (0, 0.25)")
        self.health_config = health_config or SilsHealthConfig()
        self.adapter = SilsControlAdapter(transport, max_stick=self.max_stick)
        self.scheduler = ControlScheduler(
            self.adapter,
            local_watchdog_seconds=self.local_watchdog_seconds,
            monotonic_clock=transport.clock,
        )
        self.mission = MissionSupervisor(config=mission_config or MissionConfig())

    def _decide(self, telemetry: SilsTelemetry) -> tuple[float, str]:
        error = self.target_roll_rad - telemetry.roll_rad
        normalized = min(self.max_stick, max(-self.max_stick, error * self.roll_gain_per_rad))
        return normalized, f"roll_error={error:.4f}"

    def run(self) -> SilsLoopResult:
        iterations: list[SilsLoopIteration] = []
        self.transport.start()
        try:
            # ``start()`` already sent the non-arming safe center frame.
            state = self.transport.read_telemetry()
            mission_event: OperatorEvent = OperatorEvent.START
            for index in range(self.max_iterations):
                now = self.transport.clock()
                health = telemetry_to_health(
                    state,
                    now_monotonic=now,
                    transport_ready=self.transport.is_ready,
                    calibration_valid=False,
                    capabilities=frozenset(),
                    armed=False,
                    config=self.health_config,
                )
                transition = self.mission.step(mission_event, health, now)
                mission_event = OperatorEvent.NONE
                if transition.action.kind not in _SAFE_MISSION_ACTIONS:
                    raise SilsLoopFault(
                        f"mission requested unsupported simulation action: {transition.action.kind}"
                    )

                normalized_roll, reason = self._decide(state)
                intent = ControlIntent(
                    sequence=index + 1,
                    generated_monotonic=now,
                    valid_until_monotonic=now + self.intent_ttl_seconds,
                    roll=normalized_roll,
                    pitch=0.0,
                    yaw=0.0,
                    throttle=0.0,
                    control_mode=CONTROL_MODE_ANGLE,
                    alt_mode=ALT_MODE_MANUAL,
                )
                self.scheduler.publish(intent)
                tick = self.scheduler.tick(now=now)
                if tick.state is SchedulerState.FAULT:
                    raise SilsLoopFault(tick.fault_reason or "control scheduler fault")

                new_state = self.transport.read_telemetry()
                iterations.append(
                    SilsLoopIteration(
                        iteration=index + 1,
                        decision_reason=reason,
                        normalized_roll=normalized_roll,
                        command=RcCommand.from_normalized(normalized_roll, 0.0, 0.0, 0.0),
                        sim_time_before=state.sim_time,
                        sim_time_after=new_state.sim_time,
                        roll_before=state.roll_rad,
                        roll_after=new_state.roll_rad,
                        pitch_before=state.pitch_rad,
                        pitch_after=new_state.pitch_rad,
                        altitude_before=state.altitude_m,
                        altitude_after=new_state.altitude_m,
                        receive_sequence_before=state.receive_sequence,
                        receive_sequence_after=new_state.receive_sequence,
                        mission_state=transition.context.state.value,
                        health_telemetry_valid=health.telemetry_valid,
                        attitude_changed=(
                            new_state.roll_rad != state.roll_rad
                            or new_state.pitch_rad != state.pitch_rad
                        ),
                    )
                )
                state = new_state
        finally:
            self.transport.close()
        return SilsLoopResult(
            schema_version=1,
            iterations=tuple(iterations),
            provenance=self.transport.provenance().to_dict(),
            faults=self.transport.provenance().faults,
        )


def _result_to_console(result: SilsLoopResult) -> str:
    return json.dumps(result.to_dict(), indent=2, sort_keys=True)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    launcher: Optional[SilsLauncher] = None,
    root_resolver: Callable[..., SilsRootResolution] = resolve_sils_root,
    source_marker_probe: Optional[Callable[[str], None]] = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="stampfly_sils",
        description="simulation-only interactive SILS transport over the built emu_vehicle",
    )
    parser.add_argument(
        "--root",
        "--ecosystem-root",
        dest="ecosystem_root",
        default=None,
        help=(
            "explicit installed stampfly_ecosystem root; defaults to "
            f"{ECOSYSTEM_ROOT_ENV} or {DEFAULT_ECOSYSTEM_ROOT}"
        ),
    )
    parser.add_argument("--json", action="store_true", help="print full JSON provenance")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "resolve", help="resolve the SILS root and emu artifacts only (read-only, default)"
    )

    smoke = subparsers.add_parser("smoke", help="run a bounded simulation-only closed loop")
    smoke.add_argument("--iterations", type=int, default=4)
    smoke.add_argument("--max-stick", type=float, default=0.25)
    smoke.add_argument("--duration", type=float, default=DEFAULT_DURATION_SECONDS)
    smoke.add_argument("--stale-timeout", type=float, default=DEFAULT_STALE_TIMEOUT_SECONDS)
    smoke.add_argument("--read-timeout", type=float, default=DEFAULT_READ_TIMEOUT_SECONDS)

    args = parser.parse_args(argv)
    transport = StampFlySimTransport(
        ecosystem_root=args.ecosystem_root,
        root_resolver=root_resolver,
        source_marker_probe=source_marker_probe,
        launcher=launcher,
        duration_seconds=getattr(args, "duration", DEFAULT_DURATION_SECONDS),
        stale_timeout_seconds=getattr(args, "stale_timeout", DEFAULT_STALE_TIMEOUT_SECONDS),
        read_timeout_seconds=getattr(args, "read_timeout", DEFAULT_READ_TIMEOUT_SECONDS),
    )

    if args.command in (None, "resolve"):
        diagnostics = transport.diagnostics()
        print(json.dumps(diagnostics, sort_keys=True))
        return 0 if diagnostics.get("found") else 2

    try:
        driver = SilsClosedLoopDriver(
            transport,
            max_iterations=args.iterations,
            max_stick=args.max_stick,
        )
        result = driver.run()
    except StampFlySilsError as exc:
        diagnostics = getattr(exc, "diagnostics", None)
        if diagnostics:
            print(json.dumps(diagnostics, sort_keys=True), file=sys.stderr)
        print(f"stampfly_sils error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(_result_to_console(result))
    else:
        print(
            json.dumps(
                {
                    "provider": PROVIDER,
                    "evidence_kind": EVIDENCE_KIND,
                    "simulation": True,
                    "flight_qualified": False,
                    "iterations": len(result.iterations),
                    "faults": list(result.faults),
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BACKEND",
    "DEFAULT_DURATION_SECONDS",
    "DEFAULT_ECOSYSTEM_ROOT",
    "DEFAULT_READ_TIMEOUT_SECONDS",
    "DEFAULT_STALE_TIMEOUT_SECONDS",
    "ECOSYSTEM_ROOT_ENV",
    "EMU_VEHICLE_RELATIVE",
    "EVIDENCE_KIND",
    "MODEL_RELATIVE",
    "PROVIDER",
    "PopenSilsProcess",
    "RC_CENTER",
    "RC_MAX",
    "RC_MIN",
    "RcCommand",
    "SilsClosedLoopDriver",
    "SilsControlAdapter",
    "SilsHealthConfig",
    "SilsInvocation",
    "SilsLoopFault",
    "SilsLoopIteration",
    "SilsLoopResult",
    "SilsNotFound",
    "SilsProcess",
    "SilsProcessExit",
    "SilsProcessStartError",
    "SilsProtocolError",
    "SilsRootResolution",
    "SilsStaleTelemetry",
    "SilsTelemetry",
    "SilsTimeout",
    "SilsUnsupportedBuild",
    "StampFlySilsError",
    "StampFlySimTransport",
    "UnsafeRcCommandError",
    "UnsafeSilsInvocationError",
    "build_sils_emu_argv",
    "launch_popen",
    "parse_state_line",
    "resolve_sils_root",
    "sils_emu_env",
    "telemetry_to_health",
    "verify_sils_source_markers",
]
