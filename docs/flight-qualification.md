# Flight qualification boundary

Qualification is staged so software/replay evidence cannot be mistaken for
physical-flight evidence. No flight build, wireless flight link, calibrated
observer, altitude/LAND adapter, or physical flight log exists yet.

## Current evidence budget — 2026-09-08

| Input / path | Measured or implemented evidence | Qualification consequence |
| --- | --- | --- |
| Atom Cam JPEG snapshot | 64.108 s, 13 frames, 0.203 fps; request latency p95 5.145 s, max 5.147 s | rejected for closed-loop localization; RTSP/WebRTC remains unmeasured |
| visual pose contract | `body_frd`/`world_frd`, sequence/freshness/quality/bounds fail-closed gate implemented | no calibrated detector, pose-error distribution, or sign-test evidence yet |
| USB CF1 safe hardware | 600.011 s, 11,169 zero SET, ARM=0, faults=0; tick p95 55.05 ms, max 79.55 ms | usable bench integration evidence only; USB tether is not free-flight transport |
| free-flight link | ESP-NOW gateway design issue exists | no gateway measurement or implementation evidence |
| altitude / landing | source contract and semantic API design recorded | telemetry validity, metre-valued altitude command, LAND adapter, and flight behavior are not implemented |
| mission FSM | explicit START, capability gates, feedback-required takeoff/landing completion, latched fault | semantic/replay evidence only; real action adapter is absent |

The safe-hardware run is recorded locally in
`artifacts/20260908-camfly-safe-hardware-10m.jsonl` with its summary beside it.
Device-specific identities are intentionally not included in this document.
The run observed many camera gaps because the measured JPEG endpoint is far
slower than the control tick; the scheduler nevertheless remained bounded.
This is evidence for thread/I/O separation, not evidence that the camera path
is suitable for flight.

## Stage gates

| Stage | Input | Pass condition | Current status |
| --- | --- | --- | --- |
| G0 | pure FSM/replay | deterministic transitions; no implicit START/ARM; faults latch | implemented and tested |
| G1 | delayed/lossy dynamics fixture | explicit plant model, delay/loss injection, bounded commands, deterministic pass/fail | not implemented |
| G2 | `camfly-safe` hardware | at least 10 minutes, ARM=0, every SET zero, no control fault, bounded scheduler log | passed for zero-output integration only; 600.011 s log recorded |
| G3 | propellers removed | explicit flight build, real adapter, preflight capability/identity checks, operator stop path | not implemented |
| G4 | managed low-height test | explicit START, bounded test area, qualified observation/link/altitude, abort + landing path | not authorized / prerequisites incomplete |
| G5 | repeated hover/landing | pre-agreed quantitative criteria and reporting that includes every failed/aborted run | not authorized / prerequisites incomplete |

Passing G2 does not advance the project automatically to G3. The current
camera transport fails the freshness requirement, and the free-flight link,
calibration, altitude semantics, and LAND adapter remain blockers.

## Candidate performance targets

The initial proposal remains a hypothesis for planning only:

- height: 0.3–0.5 m;
- hold duration: 30 s;
- horizontal error: p95 <= 0.20 m and max <= 0.40 m;
- altitude error: p95 <= 0.10 m;
- completion: five consecutive completed runs.

These values are **not** firmware limits or safety limits. They must be
reviewed after low-latency camera, calibrated pose, flight-link, altitude, and
landing measurements exist. If the observation error itself consumes a large
fraction of a target, the test setup or target must be redesigned rather than
claiming a controller failure.

## Qualification log evaluator

`host/qualification.py` is a hardware-free evaluator for schema-v1 JSONL. It
opens no camera, serial, or radio device. A `sample` record contains:

- `run_id` and schema/kind;
- target and observed `x/y/z` positions in metres;
- strict observation/telemetry validity booleans;
- observation age, intent age, and command period in seconds;
- saturation and optional fault state.

Each run has exactly one `outcome`: `completed`, `aborted`, or `failed`.
Missing outcomes remain visible. Invalid observation/telemetry samples are
counted and are not silently removed to obtain a passing decision.

The evaluator reports horizontal and altitude p95/max error, maximum ages and
command period, saturation fraction, fault samples, total/completed/aborted/
failed/missing-outcome counts, success rate, and longest consecutive completed
run streak. Aborted and failed runs remain in the success-rate denominator.

No default criteria are embedded. Without an explicit criteria JSON,
`qualified` is `null`; with explicit criteria the evaluator returns individual
checks and a combined decision. This prevents the planning targets above from
becoming accidental flight authorization.

Example test command:

```sh
.venv/bin/python -m unittest host.tests.test_qualification -v
```

## Controller contract before G1

The outer controller implementation must document and test, before any flight
adapter is enabled:

1. calibrated `world_frd` position error as input;
2. horizontal control in the world frame, then yaw-dependent transform to
   `body_frd`;
3. explicit units/signs at every boundary;
4. observation update rate separately from command send rate;
5. immutable observation/intent timestamps and zero-order-hold expiry;
6. finite angle/altitude commands, saturation, slew-rate limits, anti-windup,
   and integral reset on invalid observation/fault/state transition;
7. no invented PID gains: initial tuning values must come from G1 model/replay
   work and remain bounded by the later physical preflight profile.

## Physical-test record requirements

G4/G5 logs must retain target and independently evaluated observed position and
altitude, observation/intent age, command rate/period, saturation, validity,
faults, landing time, operator aborts, failed runs, battery state used by the
profile, and the exact qualification criteria. The estimator under test must
not be its own only ground truth when measuring pose accuracy.

A physical procedure must identify the aircraft, power source, test area,
observation-valid region, propeller state, explicit START action, operator stop
path, camera-loss response, low-battery stop condition, and impact-mitigation
setup. Unknown values remain blockers. USB tethered tests are never counted as
free-flight stability evidence.

## Follow-up implementation issues

The remaining work is split into:

- `issues/open/20260908-outer-position-controller.md`;
- `issues/open/20260908-flight-dynamics-simulator.md`;
- `issues/open/20260908-flight-adapter-integration.md`;
- `issues/open/20260908-flight-build-preflight.md`;
- `issues/open/20260908-staged-flight-test.md`.

Those issues preserve the G0->G5 ordering. Hardware-dependent acceptance stays
unchecked until actual evidence exists.
