# Altitude, landing, and failsafe design

This record separates source facts from proposed flight semantics. No altitude
semantics, landing behavior, or watchdog behavior are enabled by this design.
The current `camfly-safe` build remains the only approved custom firmware for
hardware tests and keeps motor PWM at zero.

## Source-derived control contract

The following values are traced to the pinned StampFly source and current CF1
bridge.

| Quantity | Source contract | Unit / sign / validity |
| --- | --- | --- |
| CF1 roll | `SET` `[-1,1]` -> `Stick[AILERON]` | normalized; finite required |
| CF1 pitch | `SET` `[-1,1]` -> `Stick[ELEVATOR]` | normalized; finite required |
| CF1 yaw | `SET` `[-1,1]` -> `Stick[RUDDER]` | normalized; becomes yaw-rate command, not absolute heading |
| CF1 throttle | `SET` `[0,1]` | normalized; finite required; cannot represent a negative altitude-rate request |
| control mode | 0 angle / 1 rate | exact enum |
| altitude mode | 4 AUTO_ALT / 5 MANUAL_ALT | exact enum |
| angle roll/pitch command | `0.4 * stick`, then `0.5*pi*(command-center)` | rad internally, finally clamped to ±30 deg |
| rate roll/pitch command | `get_rate_ref(stick)` | rad/s internally |
| yaw command | `2*pi*(stick-Rudder_center)` | rad/s-like rate reference |
| `Range` | accepted bottom ToF measurement | mm in CF1 STATUS; last retained value is not a freshness proof |
| `Altitude` | filtered `Range / 1000.0` | metres; no explicit validity/freshness bit in CF1 STATUS |
| `Altitude2` | altitude Kalman estimate | metres in firmware; not currently exposed by CF1 STATUS |
| `Alt_ref` | altitude reference | metres in firmware; initial 0.5 m, clamped 0.05–1.8 m |
| `Range0flag` | repeated invalid/out-of-range indication | source-derived counter; not exposed by current CF1 STATUS |

In AUTO_ALT, the current firmware applies a dead band to `thlo` and then runs
`Alt_ref += thlo * 0.001` on each command update. With the current CF1 throttle
range `[0,1]`, a CF1 host can increase or hold `Alt_ref` but cannot request a
negative decrement through this field. Therefore CF1 throttle is **not** a
metre-valued altitude target and the old field cannot be reinterpreted as one.

`usb_bridge.cpp` reports roll/pitch/yaw in degrees in STATUS while the internal
controller uses radians. A future protocol must not mix those units.

## Existing landing behavior is not a host LAND API

`auto_landing()` is an existing firmware mode. It changes thrust based on
`Altitude2` and contains source thresholds around 0.15 m and 0.10 m. Those
thresholds are implementation facts, not validated flight limits for this
project. Entering AUTO_LANDING through the existing mode machinery is not
semantically equivalent to receiving an idempotent host `LAND` request.

Likewise, `DISARM` immediately removes the armed state. It is an emergency or
grounded-stop primitive, not a normal airborne landing command.

## Proposed v2 semantic actions

The implementation issues linked below must preserve the existing CF1 SET
meaning and add versioned capabilities instead of silently changing fields.
A proposed command vocabulary is:

- `TAKEOFF <action-id> <target-altitude-m>`: explicit idempotent semantic action;
- `ALT <sequence> <target-altitude-m>`: explicit metre target after TAKEOFF;
- `LAND <action-id>`: explicit landing action with progress/ground feedback;
- `DISARM`: existing immediate stop primitive, never aliased to LAND;
- telemetry validity fields for altitude/ToF/IMU and their age/source state.

Exact wire spelling remains a child-issue decision, but the units and
idempotency requirements are fixed here.

## Failure and transition table

| Condition | Host can still communicate? | Required semantic response | Automatic re-arm? |
| --- | --- | --- | --- |
| operator LAND, sensors valid | yes | request controlled LAND; require grounded confirmation before completion | no |
| camera stale / pose invalid | yes | mission latches fault; request safe recovery only if landing adapter declares prerequisites valid | no |
| command intent expires | maybe | host local fault + best-effort stop/recovery; firmware freshness remains independent | no |
| host process stops | no | current 250 ms firmware watchdog behavior remains unchanged until separately qualified | no |
| radio/link loss | no | aircraft-side fallback must be independently implemented and enabled only after safe tests | no |
| ToF invalid | maybe | do not infer ground/height from retained `Range`; landing capability becomes invalid unless another qualified source exists | no |
| IMU invalid | maybe | do not start or continue an unqualified landing controller | no |
| low voltage | maybe | report explicit health failure; actual landing/emergency policy requires measured battery thresholds | no |
| bounds violation | yes | latch mission fault; recovery action depends on valid pose/altitude | no |
| operator emergency | yes/no | emergency-stop path, distinct from normal LAND | no |

A communication reconnect, fresh camera frame, or process restart never
restores ARM or resumes a previous semantic action automatically.

## Implementation / enablement split

1. Implement protocol parsing, state machines, telemetry validity, and adapters
   behind capabilities while `camfly-safe` still forces all PWM to zero.
2. Exercise pure/native tests and fake telemetry, including duplicate action
   IDs, stale sequences, sensor invalidation, and reconnect.
3. Exercise the exact code path on `camfly-safe` hardware and verify ARM/nonzero
   output remains impossible unless the specific test explicitly requires a
   safe no-propeller phase.
4. Only a separate flight-build issue may enable motor output, and only after
   flight qualification defines the physical test gate.

The existing 250 ms watchdog is not changed by these design tasks.

## Follow-up implementation issues

- `issues/open/20260908-altitude-command-api.md`
- `issues/open/20260908-telemetry-validity.md`
- `issues/open/20260908-landing-failsafe-adapter.md`

These child issues contain protocol examples and abnormal-path acceptance
criteria. Hardware thresholds that require measurement remain explicitly
unfixed.
