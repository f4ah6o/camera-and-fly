from __future__ import annotations

import unittest

from host.mission import (
    ActionKind,
    HealthSnapshot,
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

    def test_fault_is_latched_until_reset(self):
        mission = MissionSupervisor()
        mission.step(OperatorEvent.START, healthy(), 0)
        mission.step(OperatorEvent.NONE, healthy(observation_valid=False), 31)
        self.assertEqual(mission.context.state, MissionState.FAULT)
        self.assertEqual(mission.step(OperatorEvent.NONE, healthy(), 32).action.kind, ActionKind.NONE)
        self.assertEqual(mission.step(OperatorEvent.RESET, healthy(), 33).action.kind, ActionKind.RESET)
        self.assertEqual(mission.context.state, MissionState.IDLE)

    def test_no_start_means_no_arm_or_takeoff(self):
        mission = MissionSupervisor()
        for now in range(5):
            transition = mission.step(OperatorEvent.NONE, healthy(armed=True), now)
            self.assertNotIn(transition.action.kind, {ActionKind.ARM, ActionKind.TAKEOFF})


if __name__ == "__main__":
    unittest.main()
