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
