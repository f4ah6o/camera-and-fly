# Changes

## Unreleased

- Hardened Atom Cam JPEG acquisition with bounded responses, same-host
  redirects, freshness metadata, latest-frame workers, and a measurement CLI.
- Added an expiring host control scheduler, deterministic camera/StampFly
  replay, a zero-only dry-run gate, and a fake-input mission supervisor.
- Added strict CF1 parser/session validation in the StampFly safe firmware and
  native protocol tests; duplicate or stale sequences no longer refresh the
  watchdog.
- Added manifest verification for read-only SD artifacts and an explicit,
  reversible versioned runtime deployment CLI for the SD filesystem.
