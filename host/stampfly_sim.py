#!/usr/bin/env python3
"""Fail-closed optional backend for the StampFly Ecosystem ``sf`` CLI.

The external ``sf`` package is an optional, documented macOS tool.  It is not a
dependency of this repository and this module never installs, downloads,
flashes, or opens a serial/USB device.  It only runs a small allow-listed subset
of the documented ``sf sim`` / ``sf sils`` surfaces with an argv array.

The supported real-CLI shapes are ``sf sim list``,
``sf sim headless [vpython|genesis] -d <seconds> -o <file.sflog.zip>``, and
``sf sils scenario <path.scn>``.  Headless runs always receive an explicit
``-o`` path so the external tool never falls back to writing inside its own
``stampfly_ecosystem/logs`` tree; the wrapper default is a deterministic file
under this workspace's ``artifacts/`` directory.

Simulation success is recorded as ``evidence_kind=simulation`` and never as
real-flight qualification.  The deterministic local plant in
``host/flight_sim.py`` remains the primary, dependency-free simulator.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Mapping, Optional, Protocol, Sequence


PROVIDER = "stampfly_ecosystem"
EVIDENCE_KIND = "simulation"
EXECUTABLE_NAME = "sf"

SUPPORTED_SIM_BACKENDS: tuple[str, ...] = ("vpython", "genesis")
DEFAULT_SIM_BACKEND = "vpython"
SCENARIO_SUFFIX = ".scn"
HEADLESS_OUTPUT_SUFFIX = ".sflog.zip"

DEFAULT_TIMEOUT_SECONDS = 30.0
MIN_TIMEOUT_SECONDS = 0.001
MAX_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_CAPTURE_BYTES = 4096
MIN_HEADLESS_SECONDS = 0.001
MAX_HEADLESS_SECONDS = 600.0

_ALLOWED_COMMANDS = {
    "sim_list": ("sim", "list"),
    "sim_headless": ("sim", "headless"),
    "sils_scenario": ("sils", "scenario"),
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_RE = re.compile(
    r"(?i)(token|secret|password|passwd|api[_-]?key|authorization)(\s*[=:]\s*)(\S+)"
)


class StampFlySimError(RuntimeError):
    """Base class for fail-closed StampFly Ecosystem backend errors."""


class UnsafeStampFlyCommandError(StampFlySimError):
    """Raised when a command shape is not in the allow list."""


class StampFlySimNotFound(StampFlySimError):
    """Raised when no usable ``sf`` executable can be resolved."""

    def __init__(self, message: str, *, diagnostics: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = dict(diagnostics or {})


class StampFlySimTimeout(StampFlySimError):
    """Raised when the external command exceeds its bounded timeout."""

    def __init__(self, message: str, *, argv: Sequence[str] = (), timeout: float = 0.0) -> None:
        super().__init__(message)
        self.argv = tuple(argv)
        self.timeout = timeout


class StampFlySimExitError(StampFlySimError):
    """Raised on a non-zero exit while preserving the provenance result."""

    def __init__(self, message: str, *, result: "StampFlySimResult") -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class ExecutableResolution:
    found: bool
    path: Optional[str]
    source: str
    diagnostics: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "found": self.found,
            "path": self.path,
            "source": self.source,
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class ProcessResult:
    """Result of an injected/default process runner."""

    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_total_bytes: Optional[int] = None
    stderr_total_bytes: Optional[int] = None


class ProcessRunner(Protocol):
    def __call__(self, argv: Sequence[str], *, timeout: float) -> ProcessResult:
        ...


@dataclass(frozen=True)
class OutputMetadata:
    """Bounded, redaction-safe view of one captured stream."""

    bytes_total: int
    bytes_captured: int
    truncated: bool
    text: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CommandSpec:
    kind: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class StampFlySimResult:
    """Redaction-safe provenance for one simulation/SILS invocation."""

    provider: str
    evidence_kind: str
    simulation: bool
    flight_qualified: bool
    executable: str
    command_kind: str
    backend: str
    sim_backend: Optional[str]
    output_path: Optional[str]
    argv: tuple[str, ...]
    exit_code: int
    timed_out: bool
    timeout_seconds: float
    duration_seconds: Optional[float]
    scenario: Optional[str]
    stdout: OutputMetadata
    stderr: OutputMetadata

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise UnsafeStampFlyCommandError(f"{name} must be a finite number")
    return float(value)


def _validate_timeout(value: object, default: float) -> float:
    if value is None:
        value = default
    timeout = _finite_number(value, "timeout")
    if timeout < MIN_TIMEOUT_SECONDS or timeout > MAX_TIMEOUT_SECONDS:
        raise UnsafeStampFlyCommandError(
            f"timeout must be between {MIN_TIMEOUT_SECONDS} and {MAX_TIMEOUT_SECONDS} seconds"
        )
    return timeout


def _validate_duration(value: object) -> float:
    duration = _finite_number(value, "duration_seconds")
    if duration < MIN_HEADLESS_SECONDS or duration > MAX_HEADLESS_SECONDS:
        raise UnsafeStampFlyCommandError(
            f"duration_seconds must be between {MIN_HEADLESS_SECONDS} and {MAX_HEADLESS_SECONDS}"
        )
    return duration


def _require_executable_argument(executable: object) -> str:
    if not isinstance(executable, str) or not executable:
        raise UnsafeStampFlyCommandError("executable must be a non-empty string")
    if any(character in executable for character in ("\x00", "\n", "\r")):
        raise UnsafeStampFlyCommandError("executable must not contain control characters")
    return executable


def _format_seconds(value: float) -> str:
    return f"{round(value, 6):g}"


def workspace_artifacts_dir() -> Path:
    """Deterministic per-repo directory for simulator evidence artifacts."""

    return Path(__file__).resolve().parent.parent / "artifacts"


def default_headless_output(sim_backend: str) -> str:
    return str(workspace_artifacts_dir() / f"stampfly-{sim_backend}-smoke.sflog.zip")


def _validate_sim_backend(value: object) -> str:
    if value is None:
        value = DEFAULT_SIM_BACKEND
    if not isinstance(value, str) or value not in SUPPORTED_SIM_BACKENDS:
        raise UnsafeStampFlyCommandError(
            "sim_backend must be one of: " + ", ".join(SUPPORTED_SIM_BACKENDS)
        )
    return value


def _validate_path_argument(value: object, name: str, *, suffix: str) -> str:
    if not isinstance(value, str) or not value:
        raise UnsafeStampFlyCommandError(f"{name} must be a non-empty string")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise UnsafeStampFlyCommandError(f"{name} must not contain control characters")
    if value.startswith("-"):
        raise UnsafeStampFlyCommandError(f"{name} must not start with '-' (option-like)")
    if not value.lower().endswith(suffix):
        raise UnsafeStampFlyCommandError(f"{name} must end with '{suffix}'")
    return value


def _resolve_headless_output(output_path: object, *, sim_backend: str) -> str:
    if output_path is None:
        output_path = default_headless_output(sim_backend)
    validated = _validate_path_argument(output_path, "output_path", suffix=HEADLESS_OUTPUT_SUFFIX)
    path = Path(validated)
    if not path.is_absolute():
        path = (workspace_artifacts_dir().parent / path).resolve()
    return str(path)


def command_list_backends(executable: str) -> CommandSpec:
    return CommandSpec("sim_list", (_require_executable_argument(executable), "sim", "list"))


def command_headless(
    executable: str,
    duration_seconds: object,
    *,
    sim_backend: object = None,
    output_path: object = None,
) -> CommandSpec:
    duration = _validate_duration(duration_seconds)
    backend = _validate_sim_backend(sim_backend)
    output = _resolve_headless_output(output_path, sim_backend=backend)
    return CommandSpec(
        "sim_headless",
        (
            _require_executable_argument(executable),
            "sim",
            "headless",
            backend,
            "-d",
            _format_seconds(duration),
            "-o",
            output,
        ),
    )


def command_sils_scenario(executable: str, scenario: object) -> CommandSpec:
    scenario_path = _validate_path_argument(scenario, "scenario", suffix=SCENARIO_SUFFIX)
    return CommandSpec(
        "sils_scenario",
        (_require_executable_argument(executable), "sils", "scenario", scenario_path),
    )


def _redact(text: str) -> str:
    text = _ANSI_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    home = os.path.expanduser("~")
    if isinstance(home, str) and len(home) > 1 and home != "/":
        text = text.replace(home, "~")
    text = _SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", text)
    return text


def _safe_argv(argv: Sequence[str]) -> tuple[str, ...]:
    return tuple(_redact(part) for part in argv)


def _validate_command_spec(spec: CommandSpec) -> None:
    """Reject any shape other than the three explicitly supported commands."""

    if spec.kind not in _ALLOWED_COMMANDS or not spec.argv:
        raise UnsafeStampFlyCommandError(f"refusing unexpected command shape: {spec.argv!r}")
    _require_executable_argument(spec.argv[0])

    if spec.kind == "sim_list":
        valid = len(spec.argv) == 3 and spec.argv[1:] == ("sim", "list")
    elif spec.kind == "sim_headless":
        valid = (
            len(spec.argv) == 8
            and spec.argv[1] == "sim"
            and spec.argv[2] == "headless"
            and spec.argv[3] in SUPPORTED_SIM_BACKENDS
            and spec.argv[4] == "-d"
            and spec.argv[6] == "-o"
        )
        if valid:
            try:
                duration = _validate_duration(float(spec.argv[5]))
            except (ValueError, UnsafeStampFlyCommandError):
                valid = False
            else:
                valid = _format_seconds(duration) == spec.argv[5]
        if valid:
            try:
                _validate_path_argument(spec.argv[7], "output_path", suffix=HEADLESS_OUTPUT_SUFFIX)
            except UnsafeStampFlyCommandError:
                valid = False
    else:
        valid = len(spec.argv) == 4 and spec.argv[1:3] == ("sils", "scenario")
        if valid:
            try:
                _validate_path_argument(spec.argv[3], "scenario", suffix=SCENARIO_SUFFIX)
            except UnsafeStampFlyCommandError:
                valid = False

    if not valid:
        raise UnsafeStampFlyCommandError(f"refusing unexpected command shape: {spec.argv!r}")


def _output_metadata(
    raw: bytes,
    *,
    declared_truncated: bool,
    declared_total: Optional[int],
    max_capture_bytes: int,
) -> OutputMetadata:
    original_length = len(raw)
    total = original_length if declared_total is None else int(declared_total)
    truncated = bool(declared_truncated)
    if original_length > max_capture_bytes:
        raw = raw[:max_capture_bytes]
        truncated = True
    if total > len(raw):
        truncated = True
    return OutputMetadata(
        bytes_total=max(total, original_length),
        bytes_captured=len(raw),
        truncated=truncated,
        text=_redact(raw.decode("utf-8", errors="replace")),
    )


def resolve_executable(
    explicit_path: Optional[str] = None,
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
    isfile: Callable[[str], bool] = os.path.isfile,
    is_executable: Callable[[str], bool] = lambda path: os.access(path, os.X_OK),
) -> ExecutableResolution:
    """Resolve ``sf`` deterministically from an explicit path or PATH.

    The returned diagnostics never claim availability without checking.  The
    caller decides whether a missing executable should abort the run.
    """

    if explicit_path is not None:
        if not isinstance(explicit_path, str) or not explicit_path:
            return ExecutableResolution(
                False,
                None,
                "explicit",
                {"reason": "explicit_path_invalid", "checked": explicit_path},
            )
        if any(character in explicit_path for character in ("\x00", "\n", "\r")):
            return ExecutableResolution(
                False,
                None,
                "explicit",
                {"reason": "explicit_path_control_characters", "checked": explicit_path},
            )
        if not isfile(explicit_path):
            return ExecutableResolution(
                False,
                None,
                "explicit",
                {"reason": "explicit_path_not_a_file", "checked": explicit_path},
            )
        if not is_executable(explicit_path):
            return ExecutableResolution(
                False,
                None,
                "explicit",
                {"reason": "explicit_path_not_executable", "checked": explicit_path},
            )
        return ExecutableResolution(
            True,
            explicit_path,
            "explicit",
            {"reason": "explicit_path_executable", "checked": explicit_path},
        )

    located = which(EXECUTABLE_NAME)
    if located:
        return ExecutableResolution(
            True,
            located,
            "path",
            {"reason": "found_on_path", "name": EXECUTABLE_NAME},
        )
    return ExecutableResolution(
        False,
        None,
        "path",
        {"reason": "not_found_on_path", "name": EXECUTABLE_NAME},
    )


def run_process(argv: Sequence[str], *, timeout: float) -> ProcessResult:
    """Default runner: argv array, no shell, bounded capture through temp files."""

    if any(not isinstance(item, str) for item in argv):
        raise UnsafeStampFlyCommandError("argv entries must be strings")
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        completed = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            timeout=timeout,
            shell=False,
            check=False,
        )
        stdout_total = stdout_file.seek(0, os.SEEK_END)
        stdout_file.seek(0)
        stdout = stdout_file.read(DEFAULT_MAX_CAPTURE_BYTES)
        stderr_total = stderr_file.seek(0, os.SEEK_END)
        stderr_file.seek(0)
        stderr = stderr_file.read(DEFAULT_MAX_CAPTURE_BYTES)
    return ProcessResult(
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=stdout_total > len(stdout),
        stderr_truncated=stderr_total > len(stderr),
        stdout_total_bytes=stdout_total,
        stderr_total_bytes=stderr_total,
    )


class StampFlySimBackend:
    """Optional, fail-closed interface to the external ``sf`` sim/SILS CLI."""

    def __init__(
        self,
        *,
        executable: Optional[str] = None,
        runner: Optional[ProcessRunner] = None,
        which: Callable[[str], Optional[str]] = shutil.which,
        max_capture_bytes: int = DEFAULT_MAX_CAPTURE_BYTES,
        default_timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if type(max_capture_bytes) is not int or max_capture_bytes <= 0:
            raise ValueError("max_capture_bytes must be a positive integer")
        self.max_capture_bytes = max_capture_bytes
        self.default_timeout = _validate_timeout(default_timeout, DEFAULT_TIMEOUT_SECONDS)
        self._runner: ProcessRunner = runner or run_process
        self.resolution = resolve_executable(executable, which=which)

    @property
    def available(self) -> bool:
        return self.resolution.found

    def diagnostics(self) -> dict[str, object]:
        return self.resolution.to_dict()

    def _require_executable(self) -> str:
        if not self.resolution.found or not self.resolution.path:
            raise StampFlySimNotFound(
                f"{EXECUTABLE_NAME} executable not found: {self.resolution.diagnostics.get('reason')}",
                diagnostics=self.resolution.to_dict(),
            )
        return self.resolution.path

    def list_backends(self, *, timeout: Optional[float] = None) -> StampFlySimResult:
        return self._run(command_list_backends(self._require_executable()), timeout=timeout, backend="list")

    def run_headless(
        self,
        duration_seconds: object,
        *,
        sim_backend: object = None,
        output_path: object = None,
        timeout: Optional[float] = None,
    ) -> StampFlySimResult:
        duration = _validate_duration(duration_seconds)
        backend = _validate_sim_backend(sim_backend)
        resolved_output = _resolve_headless_output(output_path, sim_backend=backend)
        spec = command_headless(
            self._require_executable(),
            duration,
            sim_backend=backend,
            output_path=resolved_output,
        )
        return self._run(
            spec,
            timeout=timeout,
            backend="headless",
            duration_seconds=duration,
            sim_backend=backend,
            output_path=resolved_output,
        )

    def run_sils_scenario(self, scenario: object, *, timeout: Optional[float] = None) -> StampFlySimResult:
        scenario_path = _validate_path_argument(scenario, "scenario", suffix=SCENARIO_SUFFIX)
        spec = command_sils_scenario(self._require_executable(), scenario_path)
        return self._run(
            spec,
            timeout=timeout,
            backend="sils",
            scenario=scenario_path,
        )

    def _run(
        self,
        spec: CommandSpec,
        *,
        timeout: Optional[float],
        backend: str,
        duration_seconds: Optional[float] = None,
        scenario: Optional[str] = None,
        sim_backend: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> StampFlySimResult:
        _validate_command_spec(spec)
        resolved_timeout = _validate_timeout(timeout, self.default_timeout)
        try:
            process = self._runner(spec.argv, timeout=resolved_timeout)
        except subprocess.TimeoutExpired as exc:
            raise StampFlySimTimeout(
                f"{spec.kind} exceeded {resolved_timeout} seconds",
                argv=spec.argv,
                timeout=resolved_timeout,
            ) from exc
        except StampFlySimTimeout:
            raise
        except FileNotFoundError as exc:
            raise StampFlySimNotFound(
                f"{EXECUTABLE_NAME} executable disappeared during {spec.kind}",
                diagnostics=self.resolution.to_dict(),
            ) from exc
        except OSError as exc:
            raise StampFlySimError(f"{spec.kind} failed to start: {exc}") from exc

        if not isinstance(process, ProcessResult):
            raise StampFlySimError("runner returned an unexpected result type")

        result = StampFlySimResult(
            provider=PROVIDER,
            evidence_kind=EVIDENCE_KIND,
            simulation=True,
            flight_qualified=False,
            executable=_redact(spec.argv[0]),
            command_kind=spec.kind,
            backend=backend,
            sim_backend=sim_backend,
            output_path=_redact(output_path) if output_path is not None else None,
            argv=_safe_argv(spec.argv),
            exit_code=int(process.returncode),
            timed_out=False,
            timeout_seconds=resolved_timeout,
            duration_seconds=duration_seconds,
            scenario=_redact(scenario) if scenario is not None else None,
            stdout=_output_metadata(
                process.stdout,
                declared_truncated=process.stdout_truncated,
                declared_total=process.stdout_total_bytes,
                max_capture_bytes=self.max_capture_bytes,
            ),
            stderr=_output_metadata(
                process.stderr,
                declared_truncated=process.stderr_truncated,
                declared_total=process.stderr_total_bytes,
                max_capture_bytes=self.max_capture_bytes,
            ),
        )
        if result.exit_code != 0:
            raise StampFlySimExitError(f"{spec.kind} exited with code {result.exit_code}", result=result)
        return result


def _result_to_console(result: StampFlySimResult) -> str:
    return json.dumps(result.to_dict(), indent=2, sort_keys=True)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    runner: Optional[ProcessRunner] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> int:
    parser = argparse.ArgumentParser(
        prog="stampfly_sim",
        description="optional read-only/list-first StampFly Ecosystem sim/SILS backend",
    )
    parser.add_argument("--sf", dest="executable", default=None, help="explicit sf executable path")
    parser.add_argument("--timeout", type=float, default=None, help=f"seconds, <= {MAX_TIMEOUT_SECONDS}")
    parser.add_argument("--json", action="store_true", help="print the full provenance JSON")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("list", help="list simulator backends (read-only, default)")
    headless = subparsers.add_parser("headless", help="run the headless simulator for a bounded duration")
    headless.add_argument("--duration", type=float, required=True)
    headless.add_argument(
        "--backend",
        dest="sim_backend",
        choices=SUPPORTED_SIM_BACKENDS,
        default=DEFAULT_SIM_BACKEND,
        help="simulator backend (default: vpython)",
    )
    headless.add_argument(
        "--output",
        dest="output_path",
        default=None,
        help=(
            "explicit .sflog.zip output path; defaults to "
            "<repo>/artifacts/stampfly-<backend>-smoke.sflog.zip"
        ),
    )
    scenario = subparsers.add_parser("sils-scenario", help="run a documented SILS .scn scenario path")
    scenario.add_argument("scenario", help="path to a .scn scenario file")
    args = parser.parse_args(argv)

    backend = StampFlySimBackend(executable=args.executable, runner=runner, which=which)
    try:
        if args.command in (None, "list"):
            result = backend.list_backends(timeout=args.timeout)
        elif args.command == "headless":
            result = backend.run_headless(
                args.duration,
                sim_backend=args.sim_backend,
                output_path=args.output_path,
                timeout=args.timeout,
            )
        else:
            result = backend.run_sils_scenario(args.scenario, timeout=args.timeout)
    except StampFlySimError as exc:
        if isinstance(exc, StampFlySimNotFound):
            print(json.dumps(exc.diagnostics, sort_keys=True), file=sys.stderr)
        print(f"stampfly_sim error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(_result_to_console(result))
    else:
        print(
            json.dumps(
                {
                    "provider": result.provider,
                    "evidence_kind": result.evidence_kind,
                    "simulation": result.simulation,
                    "flight_qualified": result.flight_qualified,
                    "command_kind": result.command_kind,
                    "backend": result.backend,
                    "exit_code": result.exit_code,
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
