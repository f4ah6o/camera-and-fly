from __future__ import annotations

from collections import deque
import contextlib
import inspect
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest

import host.stampfly_sils as stampfly_sils
from host.stampfly_sils import (
    ECOSYSTEM_ROOT_ENV,
    PopenSilsProcess,
    RC_CENTER,
    RC_MAX,
    RcCommand,
    SilsClosedLoopDriver,
    SilsControlAdapter,
    SilsHealthConfig,
    SilsProcessExit,
    SilsProtocolError,
    SilsRootResolution,
    SilsStaleTelemetry,
    SilsTimeout,
    SilsUnsupportedBuild,
    StampFlySilsError,
    StampFlySimTransport,
    UnsafeRcCommandError,
    UnsafeSilsInvocationError,
    build_sils_emu_argv,
    parse_state_line,
    resolve_sils_root,
    sils_emu_env,
    telemetry_to_health,
    verify_sils_source_markers,
)


STATE_LINE = (
    "STATE t=1.234 alt=0.100 roll=0.05 pitch=-0.02 yaw=0.00 "
    "mode=FLIGHT:ARMED vbatt=3.90"
)
STATE_LINE_DEGREES_90 = (
    "STATE t=1.234 alt=0.100 roll=90.0 pitch=0.0 yaw=0.0 "
    "mode=FLIGHT:ARMED vbatt=3.90"
)

FAKE_ROOT = "/fake/ecosystem"
FAKE_EMU = f"{FAKE_ROOT}/simulator/sils/build/emu_vehicle"
FAKE_MODEL = f"{FAKE_ROOT}/simulator/sils/models/stampfly.xml"


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.value = float(now)

    def __call__(self) -> float:
        return self.value

    def advance(self, delta: float) -> None:
        self.value += max(0.0, float(delta))


class FakeSilsProcess:
    """Deterministic in-memory stand-in for the SILS emu_vehicle process."""

    def __init__(
        self,
        *,
        clock: FakeClock | None = None,
        state_lines: list[str] | None = None,
        auto_state: bool = False,
        fail_write: Exception | None = None,
        exit_after_writes: int | None = None,
    ) -> None:
        self.written: list[str] = []
        self.returncode: int | None = None
        self.closed_streams = False
        self.terminated = 0
        self.killed = 0
        self._clock = clock
        self._queue: deque[str] = deque(state_lines or [])
        self._auto = auto_state
        self._fail_write = fail_write
        self._exit_after_writes = exit_after_writes
        self.sim_time = 0.0
        self.roll = 0.0
        self.pitch = 0.0
        self.yaw = 0.0
        self.altitude = 0.0
        self.vbatt = 3.9

    def state_line(self) -> str:
        return (
            f"STATE t={self.sim_time:.3f} alt={self.altitude:.3f} "
            f"roll={self.roll:.2f} pitch={self.pitch:.2f} yaw={self.yaw:.2f} "
            f"mode=FLIGHT:ARMED vbatt={self.vbatt:.2f}"
        )

    def write_line(self, line: str) -> None:
        if self._fail_write is not None:
            raise self._fail_write
        if self.returncode is not None:
            raise BrokenPipeError("process exited")
        if line in ("arm", "land", "disarm") or line.startswith(("arm ", "land ", "disarm ")):
            raise AssertionError(f"transport sent a forbidden command: {line!r}")
        self.written.append(line)
        if line == "quit":
            self.returncode = 0
            return
        if line.startswith("rc "):
            parts = line.split()
            if len(parts) != 5:
                raise AssertionError(f"malformed rc frame: {line!r}")
            roll_adc = int(parts[1])
            target = (roll_adc - RC_CENTER) / (RC_MAX - RC_CENTER) * 0.2
            self.roll = 0.5 * self.roll + 0.5 * target
            self.sim_time += 0.033
            if self._clock is not None:
                self._clock.advance(0.033)
            if self._auto:
                self._queue.append(self.state_line())
        if self._exit_after_writes is not None and len(self.written) >= self._exit_after_writes:
            self.returncode = 7

    def read_line(self, timeout: float) -> str | None:
        if self._queue:
            return self._queue.popleft()
        if self.returncode is not None:
            return None
        if self._clock is not None:
            self._clock.advance(timeout)
        return None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated += 1

    def kill(self) -> None:
        self.killed += 1

    def wait(self, timeout: float) -> int | None:
        return self.returncode

    def close_streams(self) -> None:
        self.closed_streams = True


class StubbornSilsProcess(FakeSilsProcess):
    """A fake that never exits, to exercise bounded terminate/kill on close."""

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float) -> int | None:
        return None


class RecordingLauncher:
    def __init__(self, process: FakeSilsProcess) -> None:
        self.process = process
        self.calls: list[tuple[tuple[str, ...], dict[str, str], str | None]] = []

    def __call__(self, argv, *, env, cwd):
        self.calls.append((tuple(argv), dict(env), cwd))
        return self.process


def make_fake_root(tmp: str) -> Path:
    """Build a temporary installed-root fixture (no external writes)."""

    root = Path(tmp)
    (root / "simulator/sils/build").mkdir(parents=True, exist_ok=True)
    (root / "simulator/sils/models").mkdir(parents=True, exist_ok=True)
    (root / "simulator/sils/devices").mkdir(parents=True, exist_ok=True)
    (root / "simulator/sils/emu").mkdir(parents=True, exist_ok=True)
    emu = root / "simulator/sils/build/emu_vehicle"
    emu.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(emu, 0o755)
    (root / "simulator/sils/models/stampfly.xml").write_text("<xml/>", encoding="utf-8")
    (root / "simulator/sils/devices/rc_stdin.cpp").write_text(
        'const char* mode = getenv("SILS_EMU_RC_STDIN");\n'
        'if (line.rfind("rc ", 0) == 0) { parse_rc(line); }\n',
        encoding="utf-8",
    )
    (root / "simulator/sils/emu/emu_main.cpp").write_text(
        'printf("STATE t=%.3f alt=%.3f roll=%.2f pitch=%.2f yaw=%.2f '
        'mode=%s:%s%s vbatt=%.2f\\n", ...);\n',
        encoding="utf-8",
    )
    return root


def fake_resolution(root: str = FAKE_ROOT) -> SilsRootResolution:
    joined_emu = os.path.join(root, "simulator", "sils", "build", "emu_vehicle")
    joined_model = os.path.join(root, "simulator", "sils", "models", "stampfly.xml")
    return SilsRootResolution(
        found=True,
        root=root,
        source="explicit",
        emu_path=joined_emu,
        model_path=joined_model,
        diagnostics={"reason": "artifacts_present", "source": "explicit"},
    )


def missing_resolution() -> SilsRootResolution:
    return SilsRootResolution(
        found=False,
        root=None,
        source="default_home",
        emu_path=None,
        model_path=None,
        diagnostics={"reason": "root_not_a_directory", "source": "default_home"},
    )


def make_transport(process, *, clock=None, launcher=None, resolution=None, **kwargs):
    launcher = launcher or RecordingLauncher(process)
    transport = StampFlySimTransport(
        root_resolver=lambda root=None: resolution or fake_resolution(),
        source_marker_probe=lambda root: None,
        launcher=launcher,
        clock=clock or (lambda: 0.0),
        **kwargs,
    )
    return transport, launcher


class StateParserTests(unittest.TestCase):
    def test_valid_state_line_parses_all_fields(self):
        telemetry = parse_state_line(STATE_LINE, receive_sequence=3, received_monotonic=9.5)
        self.assertEqual(telemetry.sim_time, 1.234)
        self.assertEqual(telemetry.altitude_m, 0.1)
        self.assertAlmostEqual(telemetry.roll_rad, math.radians(0.05))
        self.assertAlmostEqual(telemetry.pitch_rad, math.radians(-0.02))
        self.assertEqual(telemetry.yaw_rad, 0.0)
        self.assertEqual(telemetry.mode, "FLIGHT:ARMED")
        self.assertEqual(telemetry.mode_fields, ("FLIGHT", "ARMED"))
        self.assertEqual(telemetry.vbatt, 3.9)
        self.assertEqual(telemetry.receive_sequence, 3)
        self.assertEqual(telemetry.age_seconds(10.5), 1.0)
        self.assertTrue(telemetry.simulation)
        self.assertFalse(telemetry.flight_qualified)

    def test_upstream_degrees_are_converted_to_radians(self):
        telemetry = parse_state_line(STATE_LINE_DEGREES_90)
        self.assertAlmostEqual(telemetry.roll_rad, math.pi / 2)
        self.assertAlmostEqual(telemetry.pitch_rad, 0.0)
        self.assertAlmostEqual(telemetry.roll_rad, math.radians(90.0))

    def test_malformed_state_lines_are_rejected(self):
        bad_lines = [
            "STATE",
            "STATE t=1 alt=2 roll=3 pitch=4 yaw=5 mode=A:B",  # missing vbatt
            "STATE t=1 alt=2 roll=3 pitch=4 yaw=5 mode=AB vbatt=3.9",  # no colon
            "STATE t=1 alt=2 roll=3 pitch=4 yaw=5 mode=A:B:C vbatt=3.9",  # two colons
            "STATE t=nan alt=2 roll=3 pitch=4 yaw=5 mode=A:B vbatt=3.9",
            "STATE t=1 alt=2 roll=3 pitch=4 yaw=5 mode=A:B vbatt=inf",
            "STATE t=1 alt=2 roll=3 pitch=4 mode=A:B vbatt=3.9",  # wrong order
            "not a state line",
        ]
        for line in bad_lines:
            with self.subTest(line=line):
                with self.assertRaises(SilsProtocolError):
                    parse_state_line(line)

    def test_non_string_and_bad_sequence_are_rejected(self):
        with self.assertRaises(SilsProtocolError):
            parse_state_line(None)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            parse_state_line(STATE_LINE, receive_sequence=-1)


class RcCommandTests(unittest.TestCase):
    def test_serialization_is_exact(self):
        self.assertEqual(RcCommand(2048, 2048, 2048, 0).serialize(), "rc 2048 2048 2048 0")
        self.assertEqual(RcCommand(0, 4095, 1, 2).serialize(), "rc 0 4095 1 2")

    def test_safe_equals_upstream_neutral_with_centered_throttle(self):
        self.assertEqual(RcCommand.neutral().to_dict(),
                         {"roll": 2048, "pitch": 2048, "yaw": 2048, "throttle": 2048})
        self.assertEqual(RcCommand.safe().to_dict(),
                         {"roll": 2048, "pitch": 2048, "yaw": 2048, "throttle": 2048})
        self.assertEqual(RcCommand.safe(), RcCommand.neutral())
        self.assertEqual(RcCommand.safe().serialize(), "rc 2048 2048 2048 2048")

    def test_range_validation(self):
        for values in ((-1, 0, 0, 0), (4096, 0, 0, 0), (0, 0, 0, 4096), (1.5, 0, 0, 0), (True, 0, 0, 0)):
            with self.subTest(values=values):
                with self.assertRaises(UnsafeRcCommandError):
                    RcCommand(*values)

    def test_normalized_zero_throttle_maps_to_center(self):
        self.assertEqual(RcCommand.from_normalized(-1.0, -1.0, -1.0, 0.0).to_dict(),
                         {"roll": 0, "pitch": 0, "yaw": 0, "throttle": RC_CENTER})
        self.assertEqual(RcCommand.from_normalized(1.0, 1.0, 1.0, 1.0).to_dict(),
                         {"roll": 4095, "pitch": 4095, "yaw": 4095, "throttle": 4095})
        self.assertEqual(RcCommand.from_normalized(0.0, 0.0, 0.0, 0.0).to_dict(),
                         {"roll": 2048, "pitch": 2048, "yaw": 2048, "throttle": 2048})
        self.assertEqual(RcCommand.from_normalized(0.0, 0.0, 0.0, 0.0), RcCommand.safe())

    def test_normalized_range_validation(self):
        for args in ((1.5, 0, 0, 0), (0, 0, 0, 1.5), (0, 0, 0, -0.1), (float("nan"), 0, 0, 0)):
            with self.subTest(args=args):
                with self.assertRaises(UnsafeRcCommandError):
                    RcCommand.from_normalized(*args)


class InvocationTests(unittest.TestCase):
    def test_direct_emu_argv_exact_shape_without_sf_sils_fly(self):
        argv = build_sils_emu_argv(FAKE_EMU, FAKE_MODEL, 1.0)
        self.assertEqual(argv, (FAKE_EMU, FAKE_MODEL, "1000000"))
        self.assertNotIn("fly", argv)
        self.assertNotIn("sils", argv)
        self.assertNotIn("sf", argv)

    def test_argv_duration_is_bounded_microseconds(self):
        self.assertEqual(build_sils_emu_argv(FAKE_EMU, FAKE_MODEL, 0.5)[2], "500000")
        for bad in ("", "emu\n", None, 1):
            with self.subTest(bad=bad):
                with self.assertRaises(UnsafeSilsInvocationError):
                    build_sils_emu_argv(bad, FAKE_MODEL, 1.0)
        for bad_duration in (0.0, -1.0, 10_000.0, float("nan")):
            with self.subTest(duration=bad_duration):
                with self.assertRaises((UnsafeSilsInvocationError, ValueError)):
                    build_sils_emu_argv(FAKE_EMU, FAKE_MODEL, bad_duration)

    def test_env_sets_realtime_and_rc_stdin(self):
        env = sils_emu_env({"PATH": "/usr/bin"})
        self.assertEqual(env["SILS_EMU_REALTIME"], "1")
        self.assertEqual(env["SILS_EMU_RC_STDIN"], "1")
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_missing_root_fails_closed_before_launch(self):
        launcher = RecordingLauncher(FakeSilsProcess())
        transport = StampFlySimTransport(
            root_resolver=lambda root=None: missing_resolution(),
            launcher=launcher,
        )
        self.assertFalse(transport.resolution.found)
        with self.assertRaises(SilsUnsupportedBuild):
            transport.start()
        self.assertEqual(launcher.calls, [])

    def test_required_source_marker_without_probe_fails_closed_before_launch(self):
        launcher = RecordingLauncher(FakeSilsProcess())
        transport = StampFlySimTransport(
            root_resolver=lambda root=None: fake_resolution(),
            source_marker_probe=lambda root: (_ for _ in ()).throw(
                SilsUnsupportedBuild("incompatible source")
            ),
            launcher=launcher,
        )
        with self.assertRaises(SilsUnsupportedBuild):
            transport.start()
        self.assertEqual(launcher.calls, [])
        self.assertFalse(transport.build_verified)


class RootResolutionAndSeamTests(unittest.TestCase):
    def test_resolve_explicit_root_detects_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_fake_root(tmp)
            resolution = resolve_sils_root(str(root))
            self.assertTrue(resolution.found)
            self.assertEqual(resolution.source, "explicit")
            self.assertEqual(resolution.diagnostics["reason"], "artifacts_present")
            self.assertTrue(resolution.emu_path.endswith("emu_vehicle"))
            self.assertTrue(resolution.model_path.endswith("stampfly.xml"))

    def test_non_executable_emu_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_fake_root(tmp)
            os.chmod(root / "simulator/sils/build/emu_vehicle", 0o644)
            resolution = resolve_sils_root(str(root))
            self.assertFalse(resolution.found)
            self.assertEqual(resolution.diagnostics["reason"], "emu_vehicle_not_executable")

    def test_missing_emu_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_fake_root(tmp)
            os.remove(root / "simulator/sils/build/emu_vehicle")
            resolution = resolve_sils_root(str(root))
            self.assertFalse(resolution.found)
            self.assertEqual(resolution.diagnostics["reason"], "emu_vehicle_missing")

    def test_missing_model_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_fake_root(tmp)
            os.remove(root / "simulator/sils/models/stampfly.xml")
            resolution = resolve_sils_root(str(root))
            self.assertFalse(resolution.found)
            self.assertEqual(resolution.diagnostics["reason"], "model_missing")

    def test_default_root_is_never_claimed_without_directory(self):
        resolution = resolve_sils_root(
            environ={},
            expanduser=lambda pattern: "/definitely/missing/ecosystem",
        )
        self.assertFalse(resolution.found)
        self.assertEqual(resolution.source, "default_home")
        self.assertEqual(resolution.diagnostics["reason"], "root_not_a_directory")

    def test_environment_root_source_is_recorded(self):
        resolution = resolve_sils_root(
            environ={ECOSYSTEM_ROOT_ENV: "/definitely/missing/ecosystem"},
        )
        self.assertFalse(resolution.found)
        self.assertEqual(resolution.source, "environment")

    def test_source_marker_validation_passes_for_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            verify_sils_source_markers(str(make_fake_root(tmp)))

    def test_source_marker_validation_fails_closed_for_incompatible_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_fake_root(tmp)
            (root / "simulator/sils/devices/rc_stdin.cpp").write_text("nothing useful", encoding="utf-8")
            (root / "simulator/sils/emu/emu_main.cpp").write_text("nothing useful", encoding="utf-8")
            with self.assertRaises(SilsUnsupportedBuild):
                verify_sils_source_markers(str(root))

    def test_source_marker_validation_fails_closed_without_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SilsUnsupportedBuild):
                verify_sils_source_markers(tmp)

    def test_transport_start_fails_closed_when_emu_missing_before_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_fake_root(tmp)
            os.remove(root / "simulator/sils/build/emu_vehicle")
            launcher = RecordingLauncher(FakeSilsProcess())
            transport = StampFlySimTransport(ecosystem_root=str(root), launcher=launcher)
            with self.assertRaises(SilsUnsupportedBuild):
                transport.start()
            self.assertEqual(launcher.calls, [])

    def test_transport_start_fails_closed_for_incompatible_source_before_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_fake_root(tmp)
            (root / "simulator/sils/devices/rc_stdin.cpp").write_text("no seam here", encoding="utf-8")
            launcher = RecordingLauncher(FakeSilsProcess())
            transport = StampFlySimTransport(ecosystem_root=str(root), launcher=launcher)
            with self.assertRaises(SilsUnsupportedBuild):
                transport.start()
            self.assertEqual(launcher.calls, [])

    def test_transport_start_uses_direct_emu_argv_and_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_fake_root(tmp)
            process = FakeSilsProcess(auto_state=True)
            launcher = RecordingLauncher(process)
            transport = StampFlySimTransport(
                ecosystem_root=str(root),
                launcher=launcher,
                duration_seconds=2.0,
            )
            transport.start()
            argv, env, _cwd = launcher.calls[0]
            self.assertEqual(
                argv,
                (
                    str(root / "simulator/sils/build/emu_vehicle"),
                    str(root / "simulator/sils/models/stampfly.xml"),
                    "2000000",
                ),
            )
            self.assertNotIn("fly", argv)
            self.assertNotIn("sf", argv)
            self.assertEqual(env["SILS_EMU_REALTIME"], "1")
            self.assertEqual(env["SILS_EMU_RC_STDIN"], "1")
            self.assertTrue(transport.build_verified)
            transport.close()


class TransportLifecycleTests(unittest.TestCase):
    def test_start_sends_safe_center_rc_first(self):
        process = FakeSilsProcess(auto_state=True)
        transport, launcher = make_transport(process, duration_seconds=1.0)
        invocation = transport.start()
        self.assertEqual(invocation.mode, "sils-emu")
        self.assertEqual(launcher.calls[0][0], (FAKE_EMU, FAKE_MODEL, "1000000"))
        self.assertEqual(launcher.calls[0][1]["SILS_EMU_REALTIME"], "1")
        self.assertEqual(launcher.calls[0][1]["SILS_EMU_RC_STDIN"], "1")
        self.assertEqual(process.written[0], "rc 2048 2048 2048 2048")
        self.assertNotIn("arm", process.written)
        self.assertTrue(transport.is_ready)
        transport.close()

    def test_send_read_receive_sequence_and_monotonic_time(self):
        process = FakeSilsProcess(auto_state=True)
        transport, _launcher = make_transport(process)
        transport.start()
        transport.send_rc(RcCommand.safe())
        first = transport.read_telemetry()
        transport.send_rc(RcCommand.safe())
        second = transport.read_telemetry()
        self.assertEqual(first.receive_sequence, 1)
        self.assertEqual(second.receive_sequence, 2)
        self.assertGreater(second.sim_time, first.sim_time)
        self.assertTrue(all(part.startswith("rc ") for part in process.written))
        transport.close()

    def test_duplicate_or_non_advancing_time_faults(self):
        for second_time in ("1.234", "1.000", "0.500"):
            with self.subTest(second_time=second_time):
                first = STATE_LINE
                second = (
                    f"STATE t={second_time} alt=0.100 roll=0.05 pitch=-0.02 yaw=0.00 "
                    "mode=FLIGHT:ARMED vbatt=3.90"
                )
                process = FakeSilsProcess(state_lines=[first, second])
                transport, _launcher = make_transport(process)
                transport.start()
                transport.read_telemetry()
                with self.assertRaises(SilsProtocolError):
                    transport.read_telemetry()
                self.assertEqual(transport.fault_reason, "sim_time_non_monotonic")
                transport.close()

    def test_malformed_state_faults(self):
        process = FakeSilsProcess(state_lines=["STATE t=oops alt=0 roll=0 pitch=0 yaw=0 mode=A:B vbatt=3.9"])
        transport, _launcher = make_transport(process)
        transport.start()
        with self.assertRaises(SilsProtocolError):
            transport.read_telemetry()
        self.assertEqual(transport.fault_reason, "malformed_state")
        self.assertIn("STATE t=oops", transport.output_tail()[0])

    def test_non_state_lines_are_bounded_output_not_telemetry(self):
        process = FakeSilsProcess(state_lines=["log line one", STATE_LINE])
        transport, _launcher = make_transport(process)
        transport.start()
        telemetry = transport.read_telemetry()
        self.assertEqual(telemetry.sim_time, 1.234)
        self.assertEqual(transport.output_tail(), ("log line one",))
        transport.close()

    def test_stale_telemetry_faults(self):
        clock = FakeClock()
        process = FakeSilsProcess(clock=clock)
        transport, _launcher = make_transport(
            process, clock=clock, stale_timeout_seconds=0.5, read_timeout_seconds=5.0
        )
        transport.start()
        with self.assertRaises(SilsStaleTelemetry):
            transport.read_telemetry()
        self.assertEqual(transport.fault_reason, "telemetry_stale")
        transport.close()

    def test_read_timeout_faults(self):
        clock = FakeClock()
        process = FakeSilsProcess(clock=clock)
        transport, _launcher = make_transport(
            process, clock=clock, stale_timeout_seconds=30.0, read_timeout_seconds=1.0
        )
        transport.start()
        with self.assertRaises(SilsTimeout):
            transport.read_telemetry()
        self.assertEqual(transport.fault_reason, "telemetry_timeout")
        transport.close()

    def test_process_exit_faults(self):
        process = FakeSilsProcess(exit_after_writes=1)
        transport, _launcher = make_transport(process, stale_timeout_seconds=30.0)
        transport.start()
        with self.assertRaises(SilsProcessExit) as caught:
            transport.read_telemetry()
        self.assertEqual(caught.exception.returncode, 7)
        self.assertEqual(transport.fault_reason, "process_exit")
        transport.close()

    def test_broken_stdin_faults(self):
        for error, reason in (
            (stampfly_sils.SilsBrokenPipe("broken"), "stdin:SilsBrokenPipe"),
            (BrokenPipeError("broken"), "stdin_failure"),
        ):
            with self.subTest(error=type(error).__name__):
                process = FakeSilsProcess(fail_write=error)
                transport, _launcher = make_transport(process)
                with self.assertRaises(StampFlySilsError):
                    transport.start()
                self.assertEqual(transport.fault_reason, reason)
                self.assertIn("initial_rc_failed", transport.provenance().faults)
                transport.close()

    def test_fault_latch_blocks_further_sends(self):
        process = FakeSilsProcess(auto_state=True, state_lines=[
            "STATE t=1.000 alt=0 roll=0 pitch=0 yaw=0 mode=A:B vbatt=3.9",
            "STATE t=1.000 alt=0 roll=0 pitch=0 yaw=0 mode=A:B vbatt=3.9",
        ])
        transport, _launcher = make_transport(process)
        transport.start()
        transport.read_telemetry()
        with self.assertRaises(StampFlySilsError):
            transport.read_telemetry()
        with self.assertRaises(StampFlySilsError):
            transport.send_rc(RcCommand.safe())

    def test_close_sends_quit_and_marks_closed(self):
        process = FakeSilsProcess(auto_state=True)
        transport, _launcher = make_transport(process)
        transport.start()
        transport.close()
        self.assertIn("quit", process.written)
        self.assertTrue(transport.closed)
        self.assertTrue(process.closed_streams)

    def test_close_terminates_then_kills_a_stuck_process(self):
        process = StubbornSilsProcess()
        transport, _launcher = make_transport(process)
        transport.start()
        transport.close(timeout=0.0)
        self.assertGreaterEqual(process.terminated, 1)
        self.assertGreaterEqual(process.killed, 1)

    def test_provenance_is_simulation_only(self):
        process = FakeSilsProcess(auto_state=True)
        transport, _launcher = make_transport(process)
        transport.start()
        transport.send_rc(RcCommand.safe())
        transport.read_telemetry()
        provenance = transport.provenance()
        self.assertEqual(provenance.provider, "stampfly_ecosystem")
        self.assertEqual(provenance.evidence_kind, "simulation")
        self.assertTrue(provenance.simulation)
        self.assertFalse(provenance.flight_qualified)
        self.assertEqual(provenance.backend, "sils-emu")
        self.assertTrue(provenance.build_verified)
        self.assertEqual(provenance.command_count, 2)
        self.assertEqual(provenance.receive_sequence, 1)
        transport.close()


class HealthTranslationTests(unittest.TestCase):
    def test_telemetry_translates_freshness_battery_and_bounds(self):
        telemetry = parse_state_line(STATE_LINE, receive_sequence=1, received_monotonic=10.0)
        health = telemetry_to_health(
            telemetry,
            now_monotonic=10.1,
            transport_ready=True,
            calibration_valid=False,
            capabilities=frozenset(),
        )
        self.assertTrue(health.transport_ready)
        self.assertTrue(health.telemetry_valid)
        self.assertTrue(health.observation_valid)
        self.assertAlmostEqual(health.observation_age_seconds, 0.1)
        self.assertTrue(health.battery_ok)
        self.assertTrue(health.within_bounds)
        self.assertFalse(health.armed)
        self.assertFalse(health.calibration_valid)
        self.assertEqual(health.capabilities, frozenset())

    def test_degrees_telemetry_uses_radian_thresholds(self):
        within = parse_state_line(
            STATE_LINE_DEGREES_90.replace("roll=90.0", "roll=20.0"),
            received_monotonic=10.0,
        )
        within_health = telemetry_to_health(within, now_monotonic=10.0, transport_ready=True)
        self.assertTrue(within_health.within_bounds)  # 20 deg ~ 0.35 rad <= 1.4 rad
        over = parse_state_line(
            STATE_LINE_DEGREES_90.replace("roll=90.0", "roll=100.0"),
            received_monotonic=10.0,
        )
        over_health = telemetry_to_health(over, now_monotonic=10.0, transport_ready=True)
        self.assertFalse(over_health.within_bounds)  # 100 deg ~ 1.75 rad > 1.4 rad

    def test_stale_or_low_battery_fails_health(self):
        telemetry = parse_state_line(STATE_LINE, receive_sequence=1, received_monotonic=10.0)
        stale = telemetry_to_health(telemetry, now_monotonic=10.9, transport_ready=True)
        self.assertFalse(stale.telemetry_valid)
        low = parse_state_line(
            STATE_LINE.replace("vbatt=3.90", "vbatt=3.00"), received_monotonic=10.0
        )
        low_health = telemetry_to_health(low, now_monotonic=10.0, transport_ready=True)
        self.assertFalse(low_health.battery_ok)

    def test_translation_never_reports_armed(self):
        telemetry = parse_state_line(STATE_LINE)
        with self.assertRaises(UnsafeRcCommandError):
            telemetry_to_health(telemetry, now_monotonic=0.0, transport_ready=True, armed=True)

    def test_config_requires_positive_values(self):
        with self.assertRaises(ValueError):
            SilsHealthConfig(min_battery_v=0.0)
        with self.assertRaises(ValueError):
            SilsHealthConfig(observation_max_age_seconds=-1.0)


class ControlAdapterTests(unittest.TestCase):
    def _started(self):
        process = FakeSilsProcess(auto_state=True)
        transport, launcher = make_transport(process)
        transport.start()
        return transport, process, launcher

    def test_adapter_sends_centered_rc_and_returns_wire_sequence(self):
        transport, process, _launcher = self._started()
        adapter = SilsControlAdapter(transport, max_stick=0.25)
        sequence = adapter.set_control(0.0, 0.0, 0.0, 0.0, control_mode=0, alt_mode=5, wait_ack=False)
        self.assertEqual(sequence, 1)
        self.assertEqual(process.written[-1], "rc 2048 2048 2048 2048")
        transport.close()

    def test_adapter_rejects_nonzero_throttle_and_large_stick(self):
        transport, _process, _launcher = self._started()
        adapter = SilsControlAdapter(transport, max_stick=0.25)
        with self.assertRaises(UnsafeRcCommandError):
            adapter.set_control(0.0, 0.0, 0.0, 0.1, control_mode=0, alt_mode=5, wait_ack=False)
        with self.assertRaises(UnsafeRcCommandError):
            adapter.set_control(0.9, 0.0, 0.0, 0.0, control_mode=0, alt_mode=5, wait_ack=False)
        self.assertEqual(adapter.commands, [])
        transport.close()

    def test_adapter_best_effort_disarm_only_recenters(self):
        transport, process, _launcher = self._started()
        adapter = SilsControlAdapter(transport)
        adapter.best_effort_disarm()
        self.assertEqual(process.written[-1], "rc 2048 2048 2048 2048")
        self.assertEqual(adapter.disarm_count, 0)
        self.assertNotIn("arm", process.written)
        transport.close()


class ClosedLoopDriverTests(unittest.TestCase):
    def _run_driver(self):
        clock = FakeClock()
        process = FakeSilsProcess(clock=clock, auto_state=True)
        transport, launcher = make_transport(process, clock=clock, stale_timeout_seconds=5.0)
        driver = SilsClosedLoopDriver(transport, max_iterations=3)
        result = driver.run()
        return result, process, launcher

    def test_driver_produces_iterative_feedback_without_arm(self):
        result, process, launcher = self._run_driver()
        self.assertGreaterEqual(len(result.iterations), 2)
        self.assertEqual(launcher.calls[0][0], (FAKE_EMU, FAKE_MODEL, "5000000"))
        self.assertEqual(process.written[0], "rc 2048 2048 2048 2048")
        self.assertEqual([line for line in process.written if line != "quit"], [
            line for line in process.written if line.startswith("rc ")
        ])
        self.assertNotIn("arm", process.written)
        self.assertTrue(result.to_dict()["simulation"])
        self.assertFalse(result.to_dict()["flight_qualified"])
        self.assertEqual(result.to_dict()["milestone"], "A")

    def test_second_decision_uses_fresh_state(self):
        result, _process, _launcher = self._run_driver()
        first, second = result.iterations[0], result.iterations[1]
        self.assertEqual(first.sim_time_after, second.sim_time_before)
        self.assertEqual(first.roll_after, second.roll_before)
        self.assertGreater(first.sim_time_after, first.sim_time_before)
        self.assertGreater(first.receive_sequence_after, first.receive_sequence_before)
        self.assertTrue(first.attitude_changed)
        self.assertNotEqual(first.normalized_roll, second.normalized_roll)
        self.assertIn("roll_error", second.decision_reason)
        self.assertIn(first.mission_state, ("PREFLIGHT", "TAKEOFF"))

    def test_driver_closes_transport_on_fault(self):
        clock = FakeClock()
        process = FakeSilsProcess(clock=clock, state_lines=[
            "STATE t=1 alt=0 roll=0 pitch=0 yaw=0 mode=A:B vbatt=3.9",
            "STATE t=1 alt=0 roll=0 pitch=0 yaw=0 mode=A:B vbatt=3.9",
        ])
        transport, _launcher = make_transport(process, clock=clock)
        driver = SilsClosedLoopDriver(transport, max_iterations=3)
        with self.assertRaises(SilsProtocolError):
            driver.run()
        self.assertTrue(transport.closed)


class PopenProcessTests(unittest.TestCase):
    def test_real_pipe_round_trip_and_quit(self):
        script = (
            "import sys\n"
            "sys.stdout.write('STATE t=1.000 alt=0.000 roll=0.00 pitch=0.00 yaw=0.00 "
            "mode=A:B vbatt=3.90\\n')\n"
            "sys.stdout.flush()\n"
            "sys.stdin.readline()\n"
        )
        process = PopenSilsProcess(
            [sys.executable, "-u", "-c", script],
            env=dict(os.environ),
        )
        try:
            line = process.read_line(5.0)
            self.assertIsNotNone(line)
            self.assertTrue(line.startswith("STATE "))
            self.assertIsNone(process.poll())
            process.write_line("quit")
            returncode = process.wait(5.0)
            self.assertIsNotNone(returncode)
        finally:
            process.kill()
            process.close_streams()


class CliAndSourceTests(unittest.TestCase):
    def test_resolve_cli_reports_found(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = stampfly_sils.main(
                ["resolve"], root_resolver=lambda root=None: fake_resolution()
            )
        self.assertEqual(code, 0)
        self.assertIn("emu_vehicle", buffer.getvalue())

    def test_resolve_cli_reports_missing(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = stampfly_sils.main(
                ["resolve"], root_resolver=lambda root=None: missing_resolution()
            )
        self.assertEqual(code, 2)

    def test_smoke_cli_runs_a_bounded_loop(self):
        clock = FakeClock()
        process = FakeSilsProcess(clock=clock, auto_state=True)
        launcher = RecordingLauncher(process)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = stampfly_sils.main(
                ["smoke", "--iterations", "2"],
                launcher=launcher,
                root_resolver=lambda root=None: fake_resolution(),
                source_marker_probe=lambda root: None,
            )
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["simulation"])
        self.assertFalse(payload["flight_qualified"])
        self.assertNotIn("arm", process.written)

    def test_module_source_has_no_hardware_or_shell_surface(self):
        source = Path(inspect.getfile(stampfly_sils)).read_text(encoding="utf-8")
        banned = (
            "shell=True",
            "os.system",
            "os.popen",
            "import serial",
            "from serial",
            "serial.Serial",
            "pyserial",
            '"arm"',
            '"land"',
            '"disarm"',
            '"arm\\n"',
            '"land\\n"',
            '"disarm\\n"',
            "CF1 ",
            'build_sf_sils_fly_argv',
            'SF_SILS_FLY_SUBCOMMAND',
        )
        for needle in banned:
            self.assertNotIn(needle, source, f"unexpected banned surface: {needle}")


if __name__ == "__main__":
    unittest.main()
