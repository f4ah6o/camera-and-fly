from __future__ import annotations

import unittest

from host.mission import (
    ActionKind,
    HealthSnapshot,
    MissionConfig,
    MissionState,
    MissionSupervisor,
    OperatorEvent,
)


def healthy(**changes):
    values = dict(
        calibration_valid=True,
        observation_valid=True,
        observation_age_seconds=0.01,
        telemetry_valid=True,
        telemetry_age_seconds=0.01,
        transport_ready=True,
        battery_ok=True,
        within_bounds=True,
        capabilities=frozenset({"arm", "takeoff", "land"}),
        grounded=True,
    )
    values.update(changes)
    return HealthSnapshot(**values)


class MissionTests(unittest.TestCase):
    def test_normal_sequence_requires_explicit_events_and_feedback(self):
        mission = MissionSupervisor()
        self.assertEqual(mission.step(OperatorEvent.NONE, healthy(), 0).action.kind, ActionKind.NONE)
        self.assertEqual(mission.step(OperatorEvent.START, healthy(), 1).action.kind, ActionKind.BEGIN_PREFLIGHT)
        self.assertEqual(mission.context.state, MissionState.PREFLIGHT)
        self.assertEqual(mission.step(OperatorEvent.NONE, healthy(), 2).context.state, MissionState.READY)
        self.assertEqual(mission.step(OperatorEvent.START, healthy(), 3).action.kind, ActionKind.ARM)
        armed = healthy(armed=True)
        self.assertEqual(mission.step(OperatorEvent.NONE, armed, 4).action.kind, ActionKind.TAKEOFF)
        reached = healthy(armed=True, altitude_m=0.3, grounded=False)
        self.assertEqual(mission.step(OperatorEvent.NONE, reached, 5).action.kind, ActionKind.HOLD)
        self.assertEqual(mission.step(OperatorEvent.LAND, reached, 6).action.kind, ActionKind.LAND)
        landed = healthy(armed=False, grounded=True)
        self.assertEqual(mission.step(OperatorEvent.NONE, landed, 7).action.kind, ActionKind.COMPLETE)
        self.assertEqual(mission.context.state, MissionState.COMPLETE)

    def test_fault_is_latched_until_reset_and_requires_new_start(self):
        mission = MissionSupervisor()
        mission.step(OperatorEvent.START, healthy(), 0)
        mission.step(OperatorEvent.NONE, healthy(observation_valid=False), 30)
        self.assertEqual(mission.context.state, MissionState.FAULT)
        self.assertEqual(mission.step(OperatorEvent.NONE, healthy(), 31).action.kind, ActionKind.NONE)
        self.assertEqual(mission.context.state, MissionState.FAULT)
        self.assertEqual(mission.step(OperatorEvent.RESET, healthy(), 32).action.kind, ActionKind.RESET)
        self.assertEqual(mission.context.state, MissionState.IDLE)
        self.assertEqual(mission.step(OperatorEvent.NONE, healthy(), 33).context.state, MissionState.IDLE)

    def test_no_start_means_no_arm_or_takeoff(self):
        mission = MissionSupervisor()
        for now in range(5):
            transition = mission.step(OperatorEvent.NONE, healthy(armed=True), now)
            self.assertNotIn(transition.action.kind, {ActionKind.ARM, ActionKind.TAKEOFF})

    def test_preflight_rejects_unknown_stale_bounds_and_missing_capability(self):
        cases = [
            (healthy(calibration_valid=False), "uncalibrated"),
            (healthy(telemetry_valid=False, telemetry_age_seconds=None), "telemetry_unknown"),
            (healthy(observation_age_seconds=0.26), "observation_stale"),
            (healthy(within_bounds=False), "outside_bounds"),
            (healthy(capabilities=frozenset({"arm", "land"})), "capability_missing:takeoff"),
        ]
        for snapshot, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                mission = MissionSupervisor()
                mission.step(OperatorEvent.START, healthy(), 0)
                transition = mission.step(OperatorEvent.NONE, snapshot, 30)
                self.assertEqual(transition.context.state, MissionState.FAULT)
                self.assertEqual(transition.action.kind, ActionKind.REQUEST_SAFE_RECOVERY)
                self.assertEqual(transition.action.reason, expected_reason)

    def test_timeouts_fire_at_deadline_for_each_active_phase(self):
        config = MissionConfig(
            preflight_timeout_seconds=1,
            arm_timeout_seconds=1,
            takeoff_timeout_seconds=1,
            hold_timeout_seconds=1,
            landing_timeout_seconds=1,
        )

        preflight = MissionSupervisor(config=config)
        preflight.step(OperatorEvent.START, healthy(), 0)
        self.assertEqual(
            preflight.step(OperatorEvent.NONE, healthy(observation_valid=False), 1).context.state,
            MissionState.FAULT,
        )

        arming = MissionSupervisor(config=config)
        arming.step(OperatorEvent.START, healthy(), 0)
        arming.step(OperatorEvent.NONE, healthy(), 0.1)
        arming.step(OperatorEvent.START, healthy(), 0.2)
        self.assertEqual(
            arming.step(OperatorEvent.NONE, healthy(armed=False), 1.2).action.reason,
            "arm_timeout",
        )

        takeoff = MissionSupervisor(config=config)
        takeoff.step(OperatorEvent.START, healthy(), 0)
        takeoff.step(OperatorEvent.NONE, healthy(), 0.1)
        takeoff.step(OperatorEvent.START, healthy(), 0.2)
        takeoff.step(OperatorEvent.NONE, healthy(armed=True), 0.3)
        transition = takeoff.step(
            OperatorEvent.NONE,
            healthy(armed=True, grounded=False, altitude_m=0.0),
            1.3,
        )
        self.assertEqual(transition.action.reason, "takeoff_timeout")

        holding = MissionSupervisor(config=config)
        holding.step(OperatorEvent.START, healthy(), 0)
        holding.step(OperatorEvent.NONE, healthy(), 0.1)
        holding.step(OperatorEvent.START, healthy(), 0.2)
        holding.step(OperatorEvent.NONE, healthy(armed=True), 0.3)
        holding.step(
            OperatorEvent.NONE,
            healthy(armed=True, grounded=False, altitude_m=0.3),
            0.4,
        )
        self.assertEqual(
            holding.step(
                OperatorEvent.NONE,
                healthy(armed=True, grounded=False, altitude_m=0.3),
                1.400001,
            ).action.reason,
            "hold_timeout",
        )

        landing = MissionSupervisor(config=config)
        landing.step(OperatorEvent.START, healthy(), 0)
        landing.step(OperatorEvent.NONE, healthy(), 0.1)
        landing.step(OperatorEvent.START, healthy(), 0.2)
        landing.step(OperatorEvent.NONE, healthy(armed=True), 0.3)
        airborne = healthy(armed=True, grounded=False, altitude_m=0.3)
        landing.step(OperatorEvent.NONE, airborne, 0.4)
        landing.step(OperatorEvent.LAND, airborne, 0.5)
        self.assertEqual(
            landing.step(OperatorEvent.NONE, airborne, 1.500001).action.reason,
            "landing_timeout",
        )

    def test_takeoff_and_complete_require_physical_feedback(self):
        mission = MissionSupervisor()
        mission.step(OperatorEvent.START, healthy(), 0)
        mission.step(OperatorEvent.NONE, healthy(), 1)
        mission.step(OperatorEvent.START, healthy(), 2)
        mission.step(OperatorEvent.NONE, healthy(armed=True), 3)
        false_grounded_altitude = healthy(armed=True, grounded=True, altitude_m=0.3)
        self.assertEqual(
            mission.step(OperatorEvent.NONE, false_grounded_altitude, 4).context.state,
            MissionState.TAKING_OFF,
        )
        airborne = healthy(armed=True, grounded=False, altitude_m=0.3)
        mission.step(OperatorEvent.NONE, airborne, 5)
        mission.step(OperatorEvent.LAND, airborne, 6)
        still_armed_grounded = healthy(armed=True, grounded=True)
        self.assertEqual(
            mission.step(OperatorEvent.NONE, still_armed_grounded, 7).context.state,
            MissionState.LANDING,
        )


if __name__ == "__main__":
    unittest.main()
