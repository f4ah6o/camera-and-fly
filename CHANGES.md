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
  tests exercise that feedback path; the real upstream loop is not yet claimed
  live. The upstream `sf sils fly` entrypoint
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
  `flight_qualified=false`. Live smoke is BLOCKED / NOT RUN because the
  installed `simulator/sils/build/emu_vehicle` artifact is absent; no live PASS
  is claimed and Milestone B is not claimed
  ([issue](issues/open/20260914-stampfly-sils-true-closed-loop.md)).
