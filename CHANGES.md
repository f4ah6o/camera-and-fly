# Changes

## Unreleased

- Hardened Atom Cam JPEG acquisition with bounded responses, same-host
  redirects, freshness metadata, latest-frame workers, and a measurement CLI.
- Added an expiring host control scheduler, deterministic camera/StampFly
  replay, a zero-only dry-run gate, and a fake-input mission supervisor.
- Added strict CF1 parser/session validation in the StampFly safe firmware and
  native protocol tests; duplicate or stale sequences no longer refresh the
  watchdog.
- Added explicit altitude, ToF range, and IMU validity/age/source telemetry to
  CF1 STATUS with fail-closed host parsing.
- Added a pure version-1 flight-link packet/session core in C++ and Python,
  including CRC, TTL, sequence, ACK-class, and re-session semantics.
- Added an explicit-port host flight-link adapter with bounded frame/ACK
  handling and reconnect/no-rearm fake-serial coverage
  ([issue](issues/open/20260908-espnow-host-transport.md)).
- Hardened unsafe mission emergency-stop coverage, owner-thread control
  shutdown/disarm, reordered flight-link ACK matching, and fail-closed
  reconnect state reset.
- Added a bounded decoded-frame adapter and FFmpeg/synthetic probe, plus a
  deterministic delayed/lossy flight-dynamics fixture for G1 evidence.
- Added manifest verification for read-only SD artifacts and an explicit,
  reversible versioned runtime deployment CLI for the SD filesystem, with
  failure-injection coverage for SD builds and remote deployment.
- Added an optional, fail-closed StampFly Ecosystem `sf sim`/`sf sils`
  backend with allow-listed argv, explicit `vpython`/`genesis` backend and
  `-o` output selection under the workspace `artifacts/` directory, safe
  `.scn` scenario paths, bounded/redacted output, and simulation-only
  provenance; `host/flight_sim.py` remains the deterministic local simulator
  and simulation success is not flight qualification.
- Added a simulation-only interactive SILS transport (`host/stampfly_sils.py`)
  for the installed built `emu_vehicle` process seam directly
  (`[<root>/simulator/sils/build/emu_vehicle,
  <root>/simulator/sils/models/stampfly.xml, <duration_us>]`, `shell=False`,
  `SILS_EMU_REALTIME=1`/`SILS_EMU_RC_STDIN=1`) with a command ->
  plant/firmware -> telemetry -> next-command interface. Deterministic fake
  tests exercise that feedback path. On 2026-09-15 the official
  `sf sils build --target vehicle` artifact was also verified live: a bounded
  4-iteration run advanced simulator time from 0.000 to 0.132 s and local
  receive sequence from 1 to 5, with fresh STATE after each bounded roll
  command feeding the next host decision; an extended 20-iteration run reached
  0.660 s / receive sequence 21 with zero faults. The upstream `sf sils fly` entrypoint
  refuses a non-TTY stdin and is only the evidence for how upstream launches the
  emulator, not itself the pipe seam. An explicit `--root`/`STAMPFLY_ECOSYSTEM_ROOT`
  or narrow `~/src/stampfly_ecosystem` resolver verifies the built executable,
  model, and read-only source seam markers (`SILS_EMU_RC_STDIN`, `rc` format,
  unique `STATE t=` format) and fails closed with `SilsUnsupportedBuild`
  otherwise. It emits a non-arming safe center frame first (`rc 2048 2048 2048
  2048`; normalized throttle `0.0` maps to ADC center), converts upstream
  degree angles to radians at the parse boundary, enforces strict STATE
  schema/finite checks, strict upstream `t` monotonicity, a local receive
  sequence, host receive timestamp/staleness and timeout faults,
  process-exit/broken-stdin faults, bounded output tail, and a `quit`-first
  bounded close. Telemetry is translated into the existing
  `mission.HealthSnapshot` and scheduled with the existing `control_loop`
  structures; it never opens serial/USB, never arms, and every record remains
  `provider=stampfly_ecosystem`, `evidence_kind=simulation`, `simulation=true`,
  `flight_qualified=false`. Milestone A live smoke is PASS. The non-arming run
  stayed PREFLIGHT with centered throttle, so no physical attitude change was
  observed or claimed. Milestone A remains independently scoped
  ([issue](issues/open/20260914-stampfly-sils-true-closed-loop.md)).
- Added the simulation-only SILS camera/perception Milestone B vertical slice
  (`host/sim_camera_perception.py`): fresh real SILS `STATE` is rendered into
  deterministic grayscale pixel frames, image-moment perception emits the
  production `PoseObservation` contract, the existing vision gate feeds a
  bounded/expiring simulation-only outer controller, and existing
  `ControlScheduler` / `SilsControlAdapter` send zero-throttle commands to the
  installed `emu_vehicle` before reading the next fresh `STATE`. The bounded
  live smoke measured `S0 -> F0 -> P0 -> C0 -> fresh S1 -> F1 -> P1 -> C1`
  with simulator time `0.000 -> 0.132 s`, receive sequence `1 -> 5`, four
  distinct frame fingerprints, and zero faults. Evidence is written as bounded
  JSON/JSONL with simulator/frame/perception/command linkage and simulation-only
  provenance. The run stayed non-arming PREFLIGHT and showed no physical
  attitude response; real Atom Cam/calibration/flight qualification are not
  claimed ([issue](issues/open/20260915-stampfly-sils-camera-perception-closed-loop.md)).
