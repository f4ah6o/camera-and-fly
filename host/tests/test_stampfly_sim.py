from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import host.stampfly_sim as stampfly_sim
from host.stampfly_sim import (
    MAX_HEADLESS_SECONDS,
    MAX_TIMEOUT_SECONDS,
    ProcessResult,
    StampFlySimBackend,
    StampFlySimExitError,
    StampFlySimNotFound,
    StampFlySimTimeout,
    UnsafeStampFlyCommandError,
    command_headless,
    command_list_backends,
    command_sils_scenario,
    CommandSpec,
    default_headless_output,
    main,
    resolve_executable,
    run_process,
    workspace_artifacts_dir,
)


class RecordingRunner:
    def __init__(self, result=None, error=None):
        self.calls: list[tuple[tuple[str, ...], float]] = []
        self.result = ProcessResult(returncode=0) if result is None else result
        self.error = error

    def __call__(self, argv, *, timeout):
        self.calls.append((tuple(argv), timeout))
        if self.error is not None:
            raise self.error
        if callable(self.result):
            return self.result(argv, timeout)
        return self.result


class CommandConstructionTests(unittest.TestCase):
    def test_list_command_is_exactly_the_allowed_shape(self):
        spec = command_list_backends("/opt/sf")
        self.assertEqual(spec.kind, "sim_list")
        self.assertEqual(spec.argv, ("/opt/sf", "sim", "list"))

    def test_headless_command_includes_backend_duration_and_output(self):
        spec = command_headless(
            "sf", 2.0, sim_backend="genesis", output_path="/tmp/run.sflog.zip"
        )
        self.assertEqual(
            spec.argv,
            ("sf", "sim", "headless", "genesis", "-d", "2", "-o", "/tmp/run.sflog.zip"),
        )
        self.assertEqual(command_headless("sf", 2.5).argv[3], "vpython")
        self.assertEqual(command_headless("sf", 2.5).argv[5], "2.5")
        self.assertEqual(command_headless("sf", 0.001).argv[5], "0.001")

    def test_headless_default_output_is_under_workspace_artifacts(self):
        spec = command_headless("sf", 1.0)
        default = str(workspace_artifacts_dir())
        self.assertTrue(spec.argv[7].startswith(default))
        self.assertEqual(spec.argv[7], default_headless_output("vpython"))
        self.assertTrue(spec.argv[7].endswith("stampfly-vpython-smoke.sflog.zip"))
        genesis = command_headless("sf", 1.0, sim_backend="genesis")
        self.assertEqual(genesis.argv[7], default_headless_output("genesis"))
        self.assertTrue(genesis.argv[7].endswith("stampfly-genesis-smoke.sflog.zip"))

    def test_headless_relative_output_is_resolved_from_workspace_root(self):
        spec = command_headless(
            "sf",
            1.0,
            output_path="artifacts/custom-smoke.sflog.zip",
        )
        self.assertEqual(
            spec.argv[7],
            str(workspace_artifacts_dir() / "custom-smoke.sflog.zip"),
        )

    def test_headless_rejects_bad_backend_or_invalid_duration(self):
        for value in (0, -1, float("nan"), float("inf"), MAX_HEADLESS_SECONDS + 1, True, "2"):
            with self.subTest(duration=value):
                with self.assertRaises(UnsafeStampFlyCommandError):
                    command_headless("sf", value)
        for backend in ("", "carla", "VPython", "vpython ", 1, True, ["vpython"]):
            with self.subTest(backend=backend):
                with self.assertRaises(UnsafeStampFlyCommandError):
                    command_headless("sf", 1.0, sim_backend=backend)

    def test_headless_rejects_option_like_control_or_wrong_suffix_output(self):
        for output in ("", "-x.sflog.zip", "run.sflog.zip\n", "run.sflog.zip\x00", "run.zip", 1):
            with self.subTest(output=output):
                with self.assertRaises(UnsafeStampFlyCommandError):
                    command_headless("sf", 1.0, output_path=output)

    def test_scenario_command_is_a_validated_scn_path(self):
        spec = command_sils_scenario("sf", "scenarios/hover-1.scn")
        self.assertEqual(spec.kind, "sils_scenario")
        self.assertEqual(spec.argv, ("sf", "sils", "scenario", "scenarios/hover-1.scn"))
        self.assertEqual(
            command_sils_scenario("sf", "my scenario.scn").argv[-1], "my scenario.scn"
        )
        self.assertEqual(command_sils_scenario("sf", "UPPER.SCN").argv[-1], "UPPER.SCN")

    def test_scenario_rejects_non_scn_or_option_like_values(self):
        for bad in ("", "-x.scn", "hover-1.txt", "hover-1", "a\nb.scn", "a\x00b.scn", 1, None):
            with self.subTest(bad=bad):
                with self.assertRaises(UnsafeStampFlyCommandError):
                    command_sils_scenario("sf", bad)

    def test_only_allow_listed_sf_argv_is_produced(self):
        allowed = {("sim", "list"), ("sim", "headless"), ("sils", "scenario")}
        specs = [
            command_list_backends("sf"),
            command_headless("sf", 1.0),
            command_headless("sf", 1.0, sim_backend="genesis", output_path="/tmp/a.sflog.zip"),
            command_sils_scenario("sf", "s1.scn"),
        ]
        for spec in specs:
            self.assertEqual(spec.argv[0], "sf")
            self.assertIn(spec.argv[1:3], allowed)

    def test_command_rejects_bad_executable_argument(self):
        for bad in ("", "sf\n", "sf\x00", 1, None):
            with self.subTest(bad=bad):
                with self.assertRaises(UnsafeStampFlyCommandError):
                    command_list_backends(bad)


class ExecutableResolutionTests(unittest.TestCase):
    def test_missing_on_path_is_diagnostics_not_guessing(self):
        resolution = resolve_executable(which=lambda name: None)
        self.assertFalse(resolution.found)
        self.assertEqual(resolution.source, "path")
        self.assertEqual(resolution.diagnostics["reason"], "not_found_on_path")

    def test_found_on_path_uses_which(self):
        resolution = resolve_executable(which=lambda name: "/opt/homebrew/bin/sf")
        self.assertTrue(resolution.found)
        self.assertEqual(resolution.path, "/opt/homebrew/bin/sf")
        self.assertEqual(resolution.source, "path")

    def test_explicit_path_must_be_a_file_and_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sf"
            path.write_text("#!/bin/sh\n")
            self.assertFalse(resolve_executable(str(path)).found)
            os.chmod(path, 0o755)
            resolution = resolve_executable(str(path))
            self.assertTrue(resolution.found)
            self.assertEqual(resolution.source, "explicit")
        self.assertFalse(resolve_executable("/definitely/missing/sf").found)

    def test_backend_missing_executable_reports_deterministic_diagnostics(self):
        backend = StampFlySimBackend(which=lambda name: None)
        self.assertFalse(backend.available)
        with self.assertRaises(StampFlySimNotFound) as caught:
            backend.list_backends()
        self.assertEqual(caught.exception.diagnostics["diagnostics"]["reason"], "not_found_on_path")


class ExecutionResultTests(unittest.TestCase):
    def test_successful_result_is_simulation_not_flight_qualification(self):
        runner = RecordingRunner(ProcessResult(returncode=0, stdout=b"backends: vpython\n"))
        backend = StampFlySimBackend(runner=runner, which=lambda name: "/opt/sf")
        result = backend.list_backends(timeout=5.0)
        self.assertTrue(result.success)
        self.assertEqual(result.provider, "stampfly_ecosystem")
        self.assertEqual(result.evidence_kind, "simulation")
        self.assertTrue(result.simulation)
        self.assertFalse(result.flight_qualified)
        self.assertEqual(result.command_kind, "sim_list")
        self.assertEqual(result.backend, "list")
        self.assertIsNone(result.sim_backend)
        self.assertIsNone(result.output_path)
        self.assertIn("vpython", result.stdout.text)
        self.assertFalse(result.stdout.truncated)
        self.assertEqual(runner.calls[0], (("/opt/sf", "sim", "list"), 5.0))

    def test_headless_result_records_sim_backend_and_output(self):
        runner = RecordingRunner(ProcessResult(returncode=0, stdout=b"ok"))
        backend = StampFlySimBackend(runner=runner, which=lambda name: "sf")
        result = backend.run_headless(
            2.0, sim_backend="genesis", output_path="/tmp/run.sflog.zip"
        )
        self.assertEqual(result.command_kind, "sim_headless")
        self.assertEqual(result.backend, "headless")
        self.assertEqual(result.sim_backend, "genesis")
        self.assertEqual(result.output_path, "/tmp/run.sflog.zip")
        self.assertEqual(result.duration_seconds, 2.0)
        self.assertEqual(
            runner.calls[0][0],
            ("sf", "sim", "headless", "genesis", "-d", "2", "-o", "/tmp/run.sflog.zip"),
        )

    def test_headless_default_output_is_recorded(self):
        runner = RecordingRunner(ProcessResult(returncode=0))
        backend = StampFlySimBackend(runner=runner, which=lambda name: "sf")
        result = backend.run_headless(1.0)
        self.assertEqual(result.sim_backend, "vpython")
        self.assertEqual(result.output_path, default_headless_output("vpython"))
        self.assertEqual(runner.calls[0][0][-2:], ("-o", default_headless_output("vpython")))

    def test_sils_scenario_success_keeps_scenario_provenance(self):
        runner = RecordingRunner(ProcessResult(returncode=0, stdout=b"ok"))
        backend = StampFlySimBackend(runner=runner, which=lambda name: "sf")
        result = backend.run_sils_scenario("scenarios/hover-1.scn")
        self.assertEqual(result.scenario, "scenarios/hover-1.scn")
        self.assertEqual(result.backend, "sils")
        self.assertEqual(result.command_kind, "sils_scenario")
        self.assertIsNone(result.sim_backend)
        self.assertIsNone(result.output_path)
        self.assertEqual(runner.calls[0][0], ("sf", "sils", "scenario", "scenarios/hover-1.scn"))

    def test_nonzero_exit_is_explicit_error_with_provenance(self):
        runner = RecordingRunner(ProcessResult(returncode=3, stdout=b"partial", stderr=b"bad"))
        backend = StampFlySimBackend(runner=runner, which=lambda name: "/fake/sf")
        with self.assertRaises(StampFlySimExitError) as caught:
            backend.run_headless(1.0, sim_backend="genesis", output_path="/tmp/r.sflog.zip")
        result = caught.exception.result
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.provider, "stampfly_ecosystem")
        self.assertEqual(result.evidence_kind, "simulation")
        self.assertTrue(result.simulation)
        self.assertFalse(result.flight_qualified)
        self.assertEqual(result.command_kind, "sim_headless")
        self.assertEqual(result.backend, "headless")
        self.assertEqual(result.sim_backend, "genesis")
        self.assertEqual(result.output_path, "/tmp/r.sflog.zip")
        self.assertEqual(result.duration_seconds, 1.0)
        self.assertEqual(
            runner.calls[0][0],
            ("/fake/sf", "sim", "headless", "genesis", "-d", "1", "-o", "/tmp/r.sflog.zip"),
        )

    def test_timeout_is_explicit_error(self):
        runner = RecordingRunner(error=subprocess.TimeoutExpired(cmd=["sf"], timeout=1.0))
        backend = StampFlySimBackend(runner=runner, which=lambda name: "sf")
        with self.assertRaises(StampFlySimTimeout) as caught:
            backend.list_backends(timeout=1.0)
        self.assertEqual(caught.exception.timeout, 1.0)
        self.assertEqual(caught.exception.argv, ("sf", "sim", "list"))

    def test_backend_rejects_unsafe_timeout_before_running(self):
        runner = RecordingRunner()
        backend = StampFlySimBackend(runner=runner, which=lambda name: "sf")
        for timeout in (0, -1, MAX_TIMEOUT_SECONDS + 1, float("nan")):
            with self.subTest(timeout=timeout):
                with self.assertRaises(UnsafeStampFlyCommandError):
                    backend.list_backends(timeout=timeout)
        self.assertEqual(runner.calls, [])

    def test_private_run_rejects_extra_or_malformed_argv(self):
        runner = RecordingRunner()
        backend = StampFlySimBackend(runner=runner, which=lambda name: "sf")
        bad_specs = (
            CommandSpec("sim_list", ("sf", "sim", "list", "--extra")),
            CommandSpec("sim_headless", ("sf", "sim", "headless", "vpython", "-d", "1")),
            CommandSpec("sim_headless", ("sf", "sim", "headless", "--evil", "1")),
            CommandSpec("sim_headless", ("sf", "sim", "headless", "carla", "-d", "1", "-o", "x.sflog.zip")),
            CommandSpec("sim_headless", ("sf", "sim", "headless", "vpython", "-d", "1", "-o", "-x.sflog.zip")),
            CommandSpec("sim_headless", ("sf", "sim", "headless", "vpython", "-d", "1", "-o", "x.zip")),
            CommandSpec("sim_headless", ("sf", "sim", "headless", "vpython", "-d", "1", "-o", "x.sflog.zip", "--extra")),
            CommandSpec("sils_scenario", ("sf", "sils", "scenario", "-x.scn")),
            CommandSpec("sils_scenario", ("sf", "sils", "scenario", "safe")),
            CommandSpec("sils_scenario", ("sf", "sils", "scenario", "safe.scn", "--extra")),
        )
        for spec in bad_specs:
            with self.subTest(spec=spec):
                with self.assertRaises(UnsafeStampFlyCommandError):
                    backend._run(spec, timeout=1.0, backend="test")
        self.assertEqual(runner.calls, [])

    def test_result_provenance_redacts_home_from_executable_and_argv(self):
        home = os.path.expanduser("~")
        executable = home + "/.stampfly/bin/sf"
        runner = RecordingRunner(ProcessResult(returncode=0))
        backend = StampFlySimBackend(runner=runner, which=lambda name: executable)
        result = backend.list_backends()
        self.assertNotIn(home, result.executable)
        self.assertEqual(result.executable, "~/.stampfly/bin/sf")
        self.assertNotIn(home, " ".join(result.argv))
        self.assertEqual(runner.calls[0][0][0], executable)

    def test_output_path_and_scenario_are_redacted(self):
        home = os.path.expanduser("~")
        runner = RecordingRunner(ProcessResult(returncode=0))
        backend = StampFlySimBackend(runner=runner, which=lambda name: home + "/bin/sf")
        headless = backend.run_headless(1.0, output_path=home + "/run.sflog.zip")
        self.assertNotIn(home, headless.output_path)
        scenario = backend.run_sils_scenario(home + "/scenario.scn")
        self.assertNotIn(home, scenario.scenario)

    def test_output_is_bounded_and_redacted(self):
        home = os.path.expanduser("~")
        payload = b"TOKEN=supersecretvalue\n" + home.encode() + b"/artifacts\n" + (b"x" * 200)
        runner = RecordingRunner(
            ProcessResult(
                returncode=0,
                stdout=payload,
                stdout_total_bytes=len(payload) + 5000,
                stdout_truncated=True,
            )
        )
        backend = StampFlySimBackend(runner=runner, which=lambda name: "sf", max_capture_bytes=128)
        result = backend.list_backends()
        self.assertTrue(result.stdout.truncated)
        self.assertLessEqual(result.stdout.bytes_captured, 128)
        self.assertGreater(result.stdout.bytes_total, 128)
        self.assertNotIn("supersecretvalue", result.stdout.text)
        self.assertIn("<redacted>", result.stdout.text)
        self.assertNotIn(home, result.stdout.text)
        self.assertIn("~", result.stdout.text)


class ProcessSafetyTests(unittest.TestCase):
    def test_injected_runner_receives_argv_array_not_string(self):
        runner = RecordingRunner(ProcessResult(returncode=0))
        backend = StampFlySimBackend(runner=runner, which=lambda name: "sf")
        backend.run_headless(2.0)
        argv, _timeout = runner.calls[0]
        self.assertIsInstance(argv, tuple)
        self.assertNotIsInstance(argv, str)
        self.assertTrue(all(isinstance(part, str) for part in argv))

    def test_default_runner_uses_shell_false_and_array(self):
        with mock.patch("host.stampfly_sim.subprocess.run") as patched:
            patched.return_value = mock.Mock(returncode=0)
            result = run_process(("sf", "sim", "list"), timeout=1.0)
        call = patched.call_args
        self.assertEqual(call.args[0], ["sf", "sim", "list"])
        self.assertIs(call.kwargs["shell"], False)
        self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0)

    def test_module_source_has_no_shell_serial_or_flight_action_surface(self):
        source = Path(inspect.getfile(stampfly_sim)).read_text(encoding="utf-8")
        banned = (
            "shell=True",
            "os.system",
            "os.popen",
            "import serial",
            "from serial",
            "import pyserial",
            "serial.Serial",
            ".arm(",
            "disarm",
            "takeoff",
            "motor",
        )
        for needle in banned:
            self.assertNotIn(needle, source, f"unexpected banned surface: {needle}")


class CliTests(unittest.TestCase):
    def test_cli_defaults_to_read_only_list(self):
        runner = RecordingRunner(ProcessResult(returncode=0, stdout=b"ok"))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main([], runner=runner, which=lambda name: "/opt/sf")
        self.assertEqual(code, 0)
        self.assertEqual(runner.calls[0][0], ("/opt/sf", "sim", "list"))
        self.assertEqual(json.loads(buffer.getvalue())["command_kind"], "sim_list")

    def test_cli_headless_defaults_output_under_workspace_artifacts(self):
        runner = RecordingRunner(ProcessResult(returncode=0))
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["headless", "--duration", "2"], runner=runner, which=lambda name: "sf")
        self.assertEqual(code, 0)
        self.assertEqual(
            runner.calls[0][0],
            ("sf", "sim", "headless", "vpython", "-d", "2", "-o", default_headless_output("vpython")),
        )

    def test_cli_headless_accepts_backend_and_explicit_output(self):
        runner = RecordingRunner(ProcessResult(returncode=0))
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(
                [
                    "headless",
                    "--duration",
                    "2",
                    "--backend",
                    "genesis",
                    "--output",
                    "/tmp/o.sflog.zip",
                ],
                runner=runner,
                which=lambda name: "sf",
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            runner.calls[0][0],
            ("sf", "sim", "headless", "genesis", "-d", "2", "-o", "/tmp/o.sflog.zip"),
        )

    def test_cli_sils_scenario_passes_scn_path(self):
        runner = RecordingRunner(ProcessResult(returncode=0))
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(
                ["sils-scenario", "scenarios/hover-1.scn"],
                runner=runner,
                which=lambda name: "sf",
            )
        self.assertEqual(code, 0)
        self.assertEqual(runner.calls[0][0], ("sf", "sils", "scenario", "scenarios/hover-1.scn"))

    def test_cli_missing_executable_returns_nonzero_with_diagnostics(self):
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = main(["list"], runner=RecordingRunner(), which=lambda name: None)
        self.assertEqual(code, 2)
        self.assertIn("not_found_on_path", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
