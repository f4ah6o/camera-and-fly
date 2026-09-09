# Flight-link design and current decision

The only qualified control transport remains USB CF1. Free-flight transport is
not enabled. Source inspection and host inventory narrow the preferred future
architecture to **Mac -> USB serial -> external ESP32 gateway -> ESP-NOW ->
StampFly**, but final adoption is gated on a motor-stopped latency/loss
measurement with an actual gateway.

A Mac Wi-Fi adapter is not assumed to support raw ESP-NOW transmission. Direct
UDP/TCP to the aircraft would require a new Wi-Fi receiver and coexistence
policy on the same ESP32-S3 radio used by the current ESP-NOW code. Reusing the
existing ESP-NOW receiver through a small external ESP32 gateway has less
firmware/radio coexistence change and preserves the camera's ordinary Wi-Fi
path on the Mac.

At the 2026-09-08 host inventory only the StampFly USB device was enumerated;
no second USB ESP32 gateway was identified. This is an observation of the
current test setup, not a claim about all hardware the operator owns.

## Existing receiver facts

`firmware/stampfly/src/rc.cpp` uses ESP-NOW on a fixed firmware channel. While
USB CF1 is claimed, `OnDataRecv` immediately returns, so ESP-NOW cannot update
`Stick`. The legacy peer is unencrypted (`peerInfo.encrypt=false`) and the
25-byte command packet has target-tail bytes, four native floats, buttons,
control/altitude modes, AHRS reset, and an additive checksum. It has no session
ID, monotonic sequence, expiry, authenticated controller identity, RELEASE, or
idempotent semantic action.

The legacy receive path is now hardened before any future wireless work:

- exact 25-byte packet length is required before fixed-offset access;
- target bytes and checksum are validated before receiver freshness changes;
- non-finite float payloads and unknown control/altitude modes are rejected;
- invalid packets cannot teach the telemetry peer;
- USB claim fencing remains first and authoritative.

This hardening does **not** make the legacy packet suitable as the autonomous
flight protocol.

## Candidate comparison

| Candidate | Camera Wi-Fi coexistence | Extra hardware | Firmware change | Ownership/auth | Current decision |
| --- | --- | --- | --- | --- | --- |
| Mac UDP/TCP -> aircraft Wi-Fi | shares aircraft Wi-Fi/radio policy; must be measured | none | new network receiver, lifecycle, channel/reconnect policy | must be added | not selected yet |
| Mac USB -> ESP32 gateway -> ESP-NOW | Mac camera Wi-Fi remains ordinary Wi-Fi; ESP-NOW stays aircraft-side | one USB-capable ESP32 gateway | versioned ESP-NOW receiver + gateway | must be added | preferred measurement candidate |
| current Mac USB -> StampFly CF1 | no RF control dependency | none | implemented | claim + sequence, no wireless auth | bench only; not free flight |

## Required wireless session contract

A future transport must not reuse the legacy 25-byte packet as-is. The
wire-format implementation issue must include at least:

- protocol version and capability bits;
- random/non-repeating session ID created only after explicit host claim;
- strictly increasing uint32 command sequence; exhaustion requires a new
  disarmed session rather than ambiguous wrap;
- sender monotonic expiry/TTL semantics mapped to receiver-local freshness;
- normalized roll/pitch/yaw/throttle and explicit modes with the same finite
  validation as CF1;
- ACK containing session ID + accepted sequence, distinct from RF delivery
  acknowledgment;
- explicit CLAIM/RELEASE/DISARM and emergency-stop messages;
- pairing/authentication design; a checksum is not authentication;
- receiver ownership state that rejects legacy RC and network sessions from
  simultaneously updating `Stick`;
- reconnect/new session always starts disarmed and with no remembered non-zero
  setpoint.

USB claim remains higher priority until an explicit handoff protocol is
implemented and motor-stopped tests prove there is no dual ownership window.

## Measurement gate

The preferred ESP32 gateway path cannot be selected as the flight transport
until an actual gateway is connected and a `camfly-safe` test records:

- >=20 Hz zero-control commands for at least 60 seconds;
- host-send -> gateway-receive -> ESP-NOW delivery -> application ACK latency
  p50/p95/p99/max;
- loss, duplicate, reordered, stale, malformed, gateway-disconnect, receiver
  reset, and reconnect cases;
- 400 Hz firmware loop timing/overrun evidence while traffic is present;
- watchdog behavior at 249/250/251 ms boundaries and after complete link loss;
- proof that reconnect does not ARM or restore a prior command.

If p99/max behavior cannot provide sufficient margin to the current 250 ms
command watchdog, the design is rejected or changed; the watchdog is not
simply lengthened to make the test pass.

## Follow-up

`issues/open/20260908-espnow-gateway-flight-link.md` specifies the gateway,
versioned receiver wire format, and safe measurement fixture. Hardware-dependent
acceptance remains open until a gateway is explicitly identified.

## Version-1 pure protocol core

The transport-independent core is implemented in
`firmware/stampfly/src/flight_link_protocol.{hpp,cpp}` and mirrored by
`host/flight_link_protocol.py`. It is not connected to the legacy receiver.

The frame is little-endian and has the following fixed header:

| Bytes | Field |
| ---: | --- |
| 0..1 | `CF` magic |
| 2 | protocol version (`1`) |
| 3 | message kind (`CLAIM`, `SET`, `ACK`, `RELEASE`, `DISARM`, `EMERGENCY_STOP`) |
| 4..11 | uint64 session ID, non-zero |
| 12..15 | uint32 command sequence, non-zero and strictly increasing |
| 16..17 | uint16 receiver TTL in milliseconds |
| 18..19 | uint16 capability bits |
| 20..21 | uint16 payload length |
| after payload | CRC-16/CCITT |

Required capabilities are versioned session ownership, application ACK, and
emergency stop. `SET` payloads contain normalized roll/pitch/yaw int16 values,
throttle uint16, and explicit control/altitude modes. ACK payloads identify the
acknowledged kind and sequence plus an `APPLICATION` or `RF_DELIVERY` class.
Only application ACK is an application acceptance; RF delivery is a radio
indication and never refreshes the command freshness timer.

The receiver-side state machine rejects malformed, duplicate, reordered,
stale, wrong-session, and post-exhaustion packets before changing state. A new
CLAIM replaces the old session with a disarmed zero setpoint. The TTL is
enforced using the receiver-local monotonic clock after acceptance. The legacy
25-byte RC format and ESP-NOW API remain outside this implementation.

The software-only Mac adapter is implemented in `host/flight_link.py`. It
requires an explicit serial port, uses the shared Python codec, and bounds the
partial-frame and pending-packet buffers. It waits only for a matching
application ACK; RF-delivery indications are recorded separately. A failed
endpoint must be explicitly reconnected, which creates a new session and does
not restore SET or ARM state. The adapter has no ARM method and is not a
gateway or aircraft receiver.
