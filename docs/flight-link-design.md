# Flight-link decision boundary

The only implemented transport is the USB CF1 bridge. The repository does
not identify an available ESP-NOW gateway, and a Mac Wi-Fi adapter cannot be
assumed to transmit ESP-NOW. Therefore no free-flight transport is selected
and no network ARM path exists.

## Invariants for a future transport

Any USB-gateway or network adapter must preserve these invariants:

- one controller owns a session; a new session starts disarmed;
- every command has a session identifier, strictly increasing sequence, and
  expiry; duplicates and reordered packets do not refresh freshness;
- ACK receipt and command acceptance/freshness are separate facts;
- RELEASE and emergency stop are explicit;
- reconnect never restores ARM or a non-zero command;
- USB claim fencing remains authoritative until an explicit safe handoff;
- latency p95/p99/max and loss/duplicate/reorder behavior are measured against
  the existing 250 ms firmware watchdog.

## Decision required before implementation

Record the owned gateway hardware, camera Wi-Fi channel, pairing/auth method,
and a motor-stopped 20 Hz test. Compare Mac UDP/TCP directly with
Mac-USB-to-ESP32-to-ESP-NOW using the same fixture and 400 Hz firmware load.
Until those facts exist, a USB cable is a bench test and not a free-flight
qualification.
