# Flight qualification boundary

The implemented stages are G0 replay and safe-build verification. No flight
build, wireless link, calibrated observer, or physical flight log exists.

## Stage gates

| Stage | Input | Pass condition | Current status |
| --- | --- | --- | --- |
| G0 | pure FSM/replay | deterministic transitions; zero ARM/non-zero SET | implemented and tested |
| G1 | delayed/lossy dynamics fixture | explicit error/timeout behavior | not implemented |
| G2 | `camfly-safe` hardware | 10-minute bounded integration log | not measured |
| G3 | propellers removed | flight adapter/preflight checks | not implemented |
| G4 | managed low-height test | explicit START, abort, landing, all limits | not authorized |
| G5 | repeated hover/landing | pre-agreed statistics including failures | not authorized |

The proposed starting targets of 0.3–0.5 m, 30 s, horizontal error p95 ≤
0.20 m/max ≤ 0.40 m, altitude error p95 ≤ 0.10 m, and five consecutive
completions remain hypotheses. They must be revised after camera/pose/link/
altitude measurements; they are not safety limits.

Qualification logs must retain target and observed position/altitude,
observation and intent age, command rate/period, saturation, validity,
faults, landing time, aborted runs, and failed runs. USB tethered tests are
not free-flight evidence.
