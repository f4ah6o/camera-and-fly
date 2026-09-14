from __future__ import annotations

import inspect
import itertools
import math
from pathlib import Path
import unittest

from host.camera_stream import DecodedFrame
from host.control_loop import ControlScheduler
from host.sim_camera_perception import (
    CameraStalled,
    DetectorNoResult,
    MalformedFrameError,
    OuterControllerConfig,
    OuterPositionController,
    SimCameraModel,
    SimVisionControlRuntime,
    detect_pose,
    render_marker_frame,
    run_sils_camera_smoke,
)
from host.stampfly_sils import (
    SilsProcessExit,
    SilsTelemetry,
    SilsTimeout,
    StampFlySimTransport,
)
from host.tests.test_stampfly_sils import (
    FakeClock,
    FakeSilsProcess,
    RecordingLauncher,
    fake_resolution,
)
from host.vision import PoseObservation


def telemetry(
    *,
    sim_time: float = 0.0,
    altitude_m: float = 0.0,
    roll_rad: float = 0.0,
    pitch_rad: float = 0.0,
    yaw_rad: float = 0.0,
    receive_sequence: int = 1,
    received_monotonic: float = 0.0,
) -> SilsTelemetry:
    return SilsTelemetry(
        sim_time=sim_time,
        altitude_m=altitude_m,
        roll_rad=roll_rad,
        pitch_rad=pitch_rad,
        yaw_rad=yaw_rad,
        mode="PREFLIGHT:GROUND",
        vbatt=3.9,
        receive_sequence=receive_sequence,
        received_monotonic=received_monotonic,
        raw_line="",
    )


def observation(
    *,
    x_m: float = 0.0,
    y_m: float = 0.0,
    z_m: float = -0.5,
    yaw_rad: float = 0.0,
    sequence: int = 1,
    received_monotonic: float = 0.0,
    source_timestamp_monotonic: float = 0.0,
) -> PoseObservation:
    return PoseObservation(
        sequence=sequence,
        received_monotonic=received_monotonic,
        source_timestamp_monotonic=source_timestamp_monotonic,
        x_m=x_m,
        y_m=y_m,
        z_m=z_m,
        yaw_rad=yaw_rad,
        marker_count=1,
        reprojection_error_px=0.5,
        confidence=0.9,
    )


class FakeControlTransport:
    """Wire-log ControlTransport used by deterministic runtime tests."""

    def __init__(self) -> None:
        self.commands: list[dict[str, float | int]] = []
        self.disarm_count = 0
        self._sequence = 0

    def set_control(self, roll, pitch, yaw, throttle, *, control_mode, alt_mode, wait_ack) -> int:
        self._sequence += 1
        self.commands.append(
            {
                "sequence": self._sequence,
                "roll": float(roll),
                "pitch": float(pitch),
                "yaw": float(yaw),
                "throttle": float(throttle),
                "control_mode": int(control_mode),
                "alt_mode": int(alt_mode),
            }
        )
        return self._sequence

    def best_effort_disarm(self) -> None:
        self.disarm_count += 1


class FakeStateSource:
    """Fresh increasing STATE every read; optional scheduled failure."""

    def __init__(
        self,
        clock: FakeClock,
        *,
        step: float = 0.03,
        roll_rad: float = 0.04,
        pitch_rad: float = -0.02,
        yaw_rad: float = 0.1,
        altitude_m: float = 0.0,
        fail_after: int | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._clock = clock
        self._step = step
        self._roll = roll_rad
        self._pitch = pitch_rad
        self._yaw = yaw_rad
        self._altitude = altitude_m
        self._reads = 0
        self._fail_after = fail_after
        self._error = error
        self.sim_time = 0.0

    @property
    def is_ready(self) -> bool:
        return True

    @property
    def clock(self):
        return self._clock

    def read_telemetry(self, *, timeout=None) -> SilsTelemetry:
        del timeout
        if self._fail_after is not None and self._reads >= self._fail_after:
            assert self._error is not None
            raise self._error
        self._clock.advance(self._step)
        self._reads += 1
        value = telemetry(
            sim_time=self.sim_time,
            altitude_m=self._altitude,
            roll_rad=self._roll,
            pitch_rad=self._pitch,
            yaw_rad=self._yaw,
            receive_sequence=self._reads,
            received_monotonic=self._clock(),
        )
        self.sim_time += self._step
        return value


class StaticStateSource:
    def __init__(self, fixed: SilsTelemetry) -> None:
        self.fixed = fixed

    @property
    def is_ready(self) -> bool:
        return True

    @property
    def clock(self):
        return lambda: 0.0

    def read_telemetry(self, *, timeout=None) -> SilsTelemetry:
        del timeout
        return self.fixed


class AutoClock:
    """Monotonic clock that advances on every read."""

    def __init__(self, step: float = 0.1) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


def build_runtime(
    *,
    clock: FakeClock | None = None,
    source=None,
    transport=None,
    scheduler=None,
    controller=None,
    frame_source=None,
    gate_config=None,
    max_iterations: int = 3,
):
    clock = clock or FakeClock()
    transport = transport or FakeControlTransport()
    scheduler = scheduler or ControlScheduler(
        transport, local_watchdog_seconds=0.2, monotonic_clock=clock
    )
    source = source or FakeStateSource(clock)
    runtime = SimVisionControlRuntime(
        state_source=source,
        scheduler=scheduler,
        controller=controller,
        frame_source=frame_source,
        gate_config=gate_config,
        clock=clock,
        max_iterations=max_iterations,
    )
    return runtime, transport, source


class CameraRoundTripTests(unittest.TestCase):
    def test_render_and_detect_recovers_pose(self):
        model = SimCameraModel()
        state = telemetry(roll_rad=0.05, pitch_rad=-0.02, yaw_rad=0.3, altitude_m=0.1)
        frame = render_marker_frame(
            state, model, frame_id=1, received_monotonic=1.0, decode_complete_monotonic=1.0
        )
        pose = detect_pose(frame, model, sequence=1)
        expected_y_m = model.lever_arm_m * math.sin(0.05)
        expected_x_m = model.lever_arm_m * math.sin(-0.02)
        expected_height = 0.1 + model.marker_altitude_offset_m
        self.assertAlmostEqual(pose.x_m, expected_x_m, delta=0.02)
        self.assertAlmostEqual(pose.y_m, expected_y_m, delta=0.02)
        self.assertAlmostEqual(-pose.z_m, expected_height, delta=0.04)
        self.assertAlmostEqual(pose.yaw_rad, 0.3, delta=0.05)
        self.assertEqual(pose.marker_count, 1)
        self.assertGreater(pose.confidence, 0.5)

    def test_successive_frames_differ_but_pose_is_stable(self):
        model = SimCameraModel()
        state = telemetry(roll_rad=0.05, pitch_rad=-0.02, yaw_rad=0.3, altitude_m=0.1)
        first = render_marker_frame(state, model, frame_id=1, received_monotonic=1.0, decode_complete_monotonic=1.0)
        second = render_marker_frame(state, model, frame_id=2, received_monotonic=1.0, decode_complete_monotonic=1.0)
        self.assertNotEqual(first.data, second.data)
        first_pose = detect_pose(first, model, sequence=1)
        second_pose = detect_pose(second, model, sequence=2)
        self.assertAlmostEqual(first_pose.x_m, second_pose.x_m, delta=0.005)
        self.assertAlmostEqual(first_pose.y_m, second_pose.y_m, delta=0.005)
        self.assertAlmostEqual(first_pose.yaw_rad, second_pose.yaw_rad, delta=0.02)

    def test_watermark_pixels_are_sub_threshold(self):
        model = SimCameraModel()
        frame = render_marker_frame(telemetry(), model, frame_id=3, received_monotonic=0.0, decode_complete_monotonic=0.0)
        self.assertTrue(all(value <= model.marker_threshold for value in frame.data[:32]))

    def test_malformed_frames_are_rejected(self):
        model = SimCameraModel()
        good = render_marker_frame(telemetry(), model, frame_id=1, received_monotonic=0.0, decode_complete_monotonic=0.0)
        bad_format = DecodedFrame(
            frame_id=1,
            data=good.data,
            width=good.width,
            height=good.height,
            pixel_format="rgb24",
            received_monotonic=0.0,
            decode_complete_monotonic=0.0,
        )
        bad_dims = DecodedFrame(
            frame_id=1,
            data=bytes(64 * 64),
            width=64,
            height=64,
            pixel_format="gray",
            received_monotonic=0.0,
            decode_complete_monotonic=0.0,
        )
        bad_length = DecodedFrame(
            frame_id=1,
            data=b"\x00" * 10,
            width=good.width,
            height=good.height,
            pixel_format="gray",
            received_monotonic=0.0,
            decode_complete_monotonic=0.0,
        )
        for frame in (bad_format, bad_dims, bad_length):
            with self.subTest(frame=frame.pixel_format):
                with self.assertRaises(MalformedFrameError):
                    detect_pose(frame, model, sequence=1)

    def test_empty_marker_is_no_result(self):
        model = SimCameraModel()
        blank = DecodedFrame(
            frame_id=1,
            data=bytes(model.frame_bytes),
            width=model.width,
            height=model.height,
            pixel_format="gray",
            received_monotonic=0.0,
            decode_complete_monotonic=0.0,
        )
        with self.assertRaises(DetectorNoResult):
            detect_pose(blank, model, sequence=1)

    def test_model_rejects_invalid_parameters(self):
        with self.assertRaises(ValueError):
            SimCameraModel(focal_px=0.0)
        with self.assertRaises(ValueError):
            SimCameraModel(camera_height_m=0.0)
        with self.assertRaises(ValueError):
            SimCameraModel(marker_altitude_offset_m=2.0)

    def test_pose_contract_rejects_nonfinite_malformed_pose(self):
        with self.assertRaises(ValueError):
            observation(x_m=float("nan"))


class OuterControllerTests(unittest.TestCase):
    def _controller(self, **changes) -> OuterPositionController:
        values = dict(
            kp_xy=1.0,
            kp_yaw=0.5,
            max_output=1.0,
            slew_rate_per_second=100.0,
            target_x_m=0.0,
            target_y_m=0.0,
        )
        values.update(changes)
        return OuterPositionController(OuterControllerConfig(**values))

    def test_world_to_body_yaw_transform_known_vector(self):
        controller = self._controller(target_x_m=0.0, target_y_m=0.0)
        intent = controller.step(
            observation(x_m=0.1, y_m=0.0, yaw_rad=math.pi / 2.0),
            now=0.0,
            sequence=1,
        )
        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertAlmostEqual(intent.roll, 0.1, places=6)
        self.assertAlmostEqual(intent.pitch, 0.0, places=6)

    def test_saturation_is_bounded_and_reported(self):
        controller = self._controller(target_x_m=5.0, max_output=0.25)
        intent = controller.step(observation(x_m=0.0), now=0.0, sequence=1)
        assert intent is not None
        self.assertEqual(intent.pitch, 0.25)
        self.assertTrue(controller.last_saturated)

    def test_slew_rate_limits_change(self):
        controller = self._controller(slew_rate_per_second=1.0)
        first = controller.step(observation(x_m=-0.2), now=0.0, sequence=1)
        second = controller.step(observation(x_m=0.2), now=0.1, sequence=2)
        assert first is not None and second is not None
        self.assertAlmostEqual(first.pitch, 0.2, places=6)
        self.assertAlmostEqual(second.pitch, 0.1, places=6)

    def test_out_of_bounds_pose_is_rejected(self):
        controller = self._controller()
        self.assertIsNone(controller.step(observation(x_m=5.0), now=0.0, sequence=1))
        self.assertIsNone(controller.step(observation(z_m=0.5), now=0.0, sequence=1))

    def test_intent_carries_fixed_validity(self):
        controller = self._controller(intent_ttl_seconds=0.2)
        first = controller.step(observation(), now=10.0, sequence=1)
        assert first is not None
        self.assertEqual(first.generated_monotonic, 10.0)
        self.assertAlmostEqual(first.valid_until_monotonic, 10.2, places=6)
        second = controller.step(observation(), now=10.05, sequence=2)
        assert second is not None
        # A new intent is generated; the old immutable intent is unchanged.
        self.assertAlmostEqual(first.valid_until_monotonic, 10.2, places=6)
        self.assertEqual(second.generated_monotonic, 10.05)
        self.assertAlmostEqual(second.valid_until_monotonic, 10.25, places=6)


class RuntimeChainTests(unittest.TestCase):
    def test_full_chain_state_frame_pose_command_state(self):
        runtime, transport, _source = build_runtime(max_iterations=3)
        result = runtime.run()
        self.assertEqual(result.final_state, "STOPPED")
        self.assertEqual(result.faults, ())
        self.assertEqual(len(result.iterations), 3)
        self.assertFalse(result.flight_qualified)
        self.assertTrue(result.simulation)
        self.assertEqual(result.milestone, "B")

        fingerprints = set()
        previous_after: float | None = None
        for index, item in enumerate(result.iterations, start=1):
            self.assertEqual(item.iteration, index)
            self.assertEqual(item.frame_sequence, index)
            self.assertEqual(item.perception_sequence, index)
            self.assertEqual(item.command_sequence, index)
            self.assertIsNotNone(item.frame_fingerprint)
            assert item.frame_fingerprint is not None
            fingerprints.add(item.frame_fingerprint)
            # pose ties to the exact frame times
            self.assertEqual(item.pose_received_monotonic, item.frame_decode_complete_monotonic)
            self.assertEqual(item.pose_source_monotonic, item.frame_received_monotonic)
            # S1 is strictly after C0
            self.assertGreater(item.simulator_time_after, item.simulator_time_before)
            self.assertGreater(item.receive_sequence_after, item.receive_sequence_before)
            # next iteration starts from the previous post-command S1
            if previous_after is not None:
                self.assertEqual(item.simulator_time_before, previous_after)
            previous_after = item.simulator_time_after
            # command ties to pose and is bounded, zero-throttle
            self.assertTrue(item.observation_valid)
            self.assertFalse(item.safe_zero)
            self.assertEqual(item.intent_throttle, 0.0)
            self.assertLessEqual(abs(item.intent_roll), 0.25)
            self.assertLessEqual(abs(item.intent_pitch), 0.25)
            self.assertLessEqual(abs(item.intent_yaw), 0.25)
            self.assertGreater(item.intent_valid_until_monotonic, item.intent_generated_monotonic)
            self.assertEqual(item.mission_state, "PREFLIGHT")
            self.assertTrue(item.health_telemetry_valid)
            self.assertTrue(item.health_observation_valid)
        self.assertEqual(len(fingerprints), 3)

        non_zero = any(
            abs(c["roll"]) > 1e-9 or abs(c["pitch"]) > 1e-9
            for c in transport.commands
        )
        self.assertTrue(non_zero)
        self.assertEqual(transport.disarm_count, 0)

    def test_evidence_links_fresh_state_to_next_camera_decision(self):
        runtime, _transport, _source = build_runtime(max_iterations=3)
        result = runtime.run()
        rows = result.to_dict()["iterations"]
        self.assertEqual(len(rows), 3)
        first, second, third = rows
        self.assertEqual(first["frame_generated_from_sim_t"], first["simulator_t"])
        self.assertEqual(first["next_simulator_t"], second["simulator_t"])
        self.assertEqual(first["next_frame_sequence"], second["frame_sequence"])
        self.assertEqual(first["next_perception_sequence"], second["perception_sequence"])
        self.assertEqual(first["command"]["wire_sequence"], 1)
        self.assertEqual(first["command"]["intent_sequence"], first["command_sequence"])
        self.assertTrue(first["pose_valid"])
        self.assertIsNotNone(first["pose_age"])
        self.assertEqual(first["controller_decision"], "control_intent")
        self.assertEqual(first["provenance"]["evidence_kind"], "simulation")
        self.assertFalse(first["provenance"]["flight_qualified"])
        self.assertIsNone(third["next_frame_sequence"])

    def test_stale_pose_produces_explicit_zero_command(self):
        model = SimCameraModel()
        counter = itertools.count()

        def frame_source(state, now):
            index = next(counter)
            if index == 0:
                return render_marker_frame(
                    state, model, frame_id=1, received_monotonic=now, decode_complete_monotonic=now
                )
            stale = now - 1.0
            return render_marker_frame(
                state, model, frame_id=index + 1, received_monotonic=stale, decode_complete_monotonic=stale
            )

        runtime, transport, _source = build_runtime(frame_source=frame_source, max_iterations=2)
        result = runtime.run()
        self.assertEqual(len(result.iterations), 2)
        first, second = result.iterations
        self.assertTrue(first.observation_valid)
        self.assertFalse(first.safe_zero)
        self.assertTrue(second.safe_zero)
        self.assertIn("vision:", second.safe_reason or "")
        self.assertEqual(second.intent_roll, 0.0)
        self.assertEqual(second.intent_pitch, 0.0)
        self.assertEqual(second.intent_yaw, 0.0)
        self.assertEqual(second.intent_throttle, 0.0)
        # no old non-zero intent was heartbeated after the stale frame
        self.assertEqual(transport.commands[-1]["roll"], 0.0)
        self.assertEqual(transport.commands[-1]["pitch"], 0.0)
        self.assertEqual(transport.commands[-1]["throttle"], 0.0)

    def test_detector_no_result_produces_zero_command(self):
        model = SimCameraModel()
        blank = DecodedFrame(
            frame_id=1,
            data=bytes(model.frame_bytes),
            width=model.width,
            height=model.height,
            pixel_format="gray",
            received_monotonic=0.0,
            decode_complete_monotonic=0.0,
        )
        runtime, transport, _source = build_runtime(frame_source=lambda state, now: blank, max_iterations=1)
        result = runtime.run()
        self.assertEqual(len(result.iterations), 1)
        self.assertTrue(result.iterations[0].safe_zero)
        self.assertIn("perception:DetectorNoResult", result.iterations[0].safe_reason or "")
        self.assertEqual(transport.commands[0]["throttle"], 0.0)

    def test_frame_missing_produces_zero_command(self):
        runtime, transport, _source = build_runtime(frame_source=lambda state, now: None, max_iterations=1)
        result = runtime.run()
        self.assertTrue(result.iterations[0].safe_zero)
        self.assertEqual(result.iterations[0].safe_reason, "frame_missing")
        self.assertEqual(transport.commands[0]["throttle"], 0.0)

    def test_camera_producer_stall_produces_zero_command(self):
        def frame_source(state, now):
            raise CameraStalled("no new frame")

        runtime, _transport, _source = build_runtime(frame_source=frame_source, max_iterations=1)
        result = runtime.run()
        self.assertTrue(result.iterations[0].safe_zero)
        self.assertIn("camera_stall", result.iterations[0].safe_reason or "")

    def test_malformed_frame_produces_zero_command(self):
        model = SimCameraModel()
        broken = DecodedFrame(
            frame_id=1,
            data=b"\x00" * 10,
            width=model.width,
            height=model.height,
            pixel_format="gray",
            received_monotonic=0.0,
            decode_complete_monotonic=0.0,
        )
        runtime, _transport, _source = build_runtime(frame_source=lambda state, now: broken, max_iterations=1)
        result = runtime.run()
        self.assertTrue(result.iterations[0].safe_zero)
        self.assertIn("MalformedFrameError", result.iterations[0].safe_reason or "")

    def test_frame_sequence_rollback_latches_and_stops(self):
        model = SimCameraModel()

        def frame_source(state, now):
            return render_marker_frame(
                state, model, frame_id=1, received_monotonic=now, decode_complete_monotonic=now
            )

        runtime, transport, _source = build_runtime(frame_source=frame_source, max_iterations=3)
        result = runtime.run()
        self.assertEqual(result.final_state, "FAULT")
        self.assertIn("frame_sequence_rollback", result.faults[0])
        self.assertGreaterEqual(transport.disarm_count, 1)
        # no further command was sent after the fault latch
        self.assertEqual(len(transport.commands), 1)

    def test_frame_sequence_strict_rollback_latches_and_stops(self):
        model = SimCameraModel()
        frame_ids = iter((2, 1))

        def frame_source(state, now):
            return render_marker_frame(
                state,
                model,
                frame_id=next(frame_ids),
                received_monotonic=now,
                decode_complete_monotonic=now,
            )

        runtime, transport, _source = build_runtime(frame_source=frame_source, max_iterations=2)
        result = runtime.run()
        self.assertEqual(result.final_state, "FAULT")
        self.assertIn("frame_sequence_rollback:1<=2", result.faults[0])
        self.assertGreaterEqual(transport.disarm_count, 1)
        self.assertEqual(len(transport.commands), 1)

    def test_frame_generation_change_latches(self):
        model = SimCameraModel()
        generations = itertools.count(1)

        def frame_source(state, now):
            generation = next(generations)
            return render_marker_frame(
                state,
                model,
                frame_id=generation,
                generation=generation,
                received_monotonic=now,
                decode_complete_monotonic=now,
            )

        runtime, transport, _source = build_runtime(frame_source=frame_source, max_iterations=2)
        result = runtime.run()
        self.assertEqual(result.final_state, "FAULT")
        self.assertIn("frame_generation_changed", result.faults[0])
        self.assertGreaterEqual(transport.disarm_count, 1)
        self.assertEqual(len(transport.commands), 1)

    def test_simulator_state_timeout_latches(self):
        clock = FakeClock()
        source = FakeStateSource(clock, fail_after=1, error=SilsTimeout("no STATE"))
        runtime, _transport, _source = build_runtime(clock=clock, source=source, max_iterations=2)
        result = runtime.run()
        self.assertEqual(result.final_state, "FAULT")
        self.assertIn("state_next:SilsTimeout", result.faults[0])

    def test_simulator_process_exit_latches(self):
        clock = FakeClock()
        source = FakeStateSource(clock, fail_after=1, error=SilsProcessExit("gone", returncode=7))
        runtime, _transport, _source = build_runtime(clock=clock, source=source, max_iterations=2)
        result = runtime.run()
        self.assertEqual(result.final_state, "FAULT")
        self.assertIn("state_next:SilsProcessExit", result.faults[0])

    def test_initial_state_failure_latches(self):
        clock = FakeClock()
        source = FakeStateSource(clock, fail_after=0, error=SilsTimeout("no STATE"))
        runtime, _transport, _source = build_runtime(clock=clock, source=source, max_iterations=2)
        result = runtime.run()
        self.assertEqual(result.final_state, "FAULT")
        self.assertIn("state_initial:SilsTimeout", result.faults[0])

    def test_intent_expiry_latches(self):
        clock = AutoClock(step=0.1)
        fixed = telemetry(roll_rad=0.05, pitch_rad=-0.02, yaw_rad=0.1, received_monotonic=0.0)
        source = StaticStateSource(fixed)
        controller = OuterPositionController(
            OuterControllerConfig(max_output=0.25, intent_ttl_seconds=0.01, zero_ttl_seconds=0.01)
        )
        transport = FakeControlTransport()
        scheduler = ControlScheduler(transport, local_watchdog_seconds=0.2, monotonic_clock=clock)
        runtime = SimVisionControlRuntime(
            state_source=source,
            scheduler=scheduler,
            controller=controller,
            clock=clock,
            max_iterations=1,
        )
        result = runtime.run()
        self.assertEqual(result.final_state, "FAULT")
        self.assertTrue(result.faults[0].startswith("intent_expired"))

    def test_controller_saturation_is_recorded_and_bounded(self):
        controller = OuterPositionController(
            OuterControllerConfig(target_x_m=0.6, kp_xy=1.0, max_output=0.1)
        )
        runtime, transport, _source = build_runtime(controller=controller, max_iterations=1)
        result = runtime.run()
        self.assertTrue(result.iterations[0].controller_saturated)
        for command in transport.commands:
            self.assertLessEqual(abs(command["roll"]), 0.1)
            self.assertLessEqual(abs(command["pitch"]), 0.1)

    def test_restart_does_not_restore_previous_command_sequence(self):
        model = SimCameraModel()

        def rollback_source(state, now):
            return render_marker_frame(state, model, frame_id=1, received_monotonic=now, decode_complete_monotonic=now)

        first_runtime, _transport, _source = build_runtime(
            frame_source=rollback_source, max_iterations=2
        )
        first = first_runtime.run()
        self.assertEqual(first.final_state, "FAULT")

        second_runtime, second_transport, _source2 = build_runtime(max_iterations=1)
        second = second_runtime.run()
        self.assertEqual(len(second.iterations), 1)
        # A restart starts from a fresh sequence and does not resume old control.
        self.assertEqual(second.iterations[0].command_sequence, 1)
        self.assertEqual(second.iterations[0].mission_state, "PREFLIGHT")
        self.assertNotEqual(second_transport.commands[0]["throttle"], 1.0)


class SilsIntegrationTests(unittest.TestCase):
    def test_camera_loop_over_fake_emu_sends_safe_center_first(self):
        clock = FakeClock()
        process = FakeSilsProcess(clock=clock, auto_state=True)
        launcher = RecordingLauncher(process)
        transport = StampFlySimTransport(
            root_resolver=lambda root=None: fake_resolution(),
            source_marker_probe=lambda root: None,
            launcher=launcher,
            clock=clock,
            duration_seconds=5.0,
        )
        result = run_sils_camera_smoke(transport, max_iterations=2)
        self.assertEqual(result.final_state, "STOPPED")
        self.assertEqual(result.faults, ())
        self.assertGreaterEqual(len(result.iterations), 1)
        self.assertEqual(process.written[0], "rc 2048 2048 2048 2048")
        self.assertTrue(all(line.startswith("rc ") or line == "quit" for line in process.written))
        self.assertNotIn("arm", process.written)
        self.assertNotIn("land", process.written)
        self.assertNotIn("disarm", process.written)
        item = result.iterations[0]
        self.assertTrue(item.observation_valid)
        self.assertEqual(item.intent_throttle, 0.0)
        self.assertGreater(item.simulator_time_after, item.simulator_time_before)

    def test_camera_loop_closes_when_state_faults(self):
        clock = FakeClock()
        process = FakeSilsProcess(clock=clock, auto_state=False)
        transport = StampFlySimTransport(
            root_resolver=lambda root=None: fake_resolution(),
            source_marker_probe=lambda root: None,
            launcher=RecordingLauncher(process),
            clock=clock,
            duration_seconds=5.0,
            read_timeout_seconds=1.0,
            stale_timeout_seconds=30.0,
        )
        result = run_sils_camera_smoke(transport, max_iterations=1)
        self.assertEqual(result.final_state, "FAULT")
        self.assertTrue(transport.closed)


class ModuleSourceTests(unittest.TestCase):
    def test_module_has_no_hardware_or_shell_surface(self):
        import host.sim_camera_perception as module

        source = Path(inspect.getfile(module)).read_text(encoding="utf-8")
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
        )
        for needle in banned:
            self.assertNotIn(needle, source, f"unexpected banned surface: {needle}")


if __name__ == "__main__":
    unittest.main()
