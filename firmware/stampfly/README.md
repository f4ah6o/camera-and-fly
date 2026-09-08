# StampFly

## Framework

Platformio

## Base on project

[M5Fly-kanazawa/StampFly2024June (github.com)](https://github.com/M5Fly-kanazawa/StampFly2024June)

## Product introduction

[M5Stampfly](https://docs.m5stack.com/en/app/Stamp%20Fly)

## Third-party libraries

fastled/FastLED

tinyu-zhao/INA3221

mathertel/OneButton @ ^2.5.0

## CF1 safe protocol checks

The project-owned bridge requires exactly seven arguments after `CF1 SET`,
finite roll/pitch/yaw in `[-1, 1]`, finite throttle in `[0, 1]`, control mode
`0` or `1`, and altitude mode `4` or `5`. Only strictly increasing, non-zero
session sequences are accepted. `CLAIM` resets sequence and freshness state;
duplicate/reordered packets do not refresh the watchdog, and sequence
exhaustion requires a new claim. An overlong line is discarded through its
newline and each 400 Hz poll consumes at most a fixed number of USB bytes.
CF1 replies use a bounded `availableForWrite()` gate and never flush/block the
control loop; a dropped acknowledgement is handled as a host timeout/fault.

`CF1 STATUS` also carries the versioned `telemetry_validity_v1` capability and
machine-readable altitude, ToF range, and IMU validity/age/source fields.
Sensor age uses the firmware monotonic clock; an invalid update never makes a
retained numeric value fresh, and unknown age is represented explicitly.

Run the Arduino-independent parser/session tests before the firmware build:

```sh
bash firmware/stampfly/tests/run_native_tests.sh
```

The future ESP-NOW link has a separate pure protocol core in
`src/flight_link_protocol.{hpp,cpp}`. Its version-1 frame is little-endian:
`CF` magic, version, message kind, uint64 session ID, uint32 sequence, uint16
receiver TTL, uint16 capability bits, uint16 payload length, payload, and
CRC-16/CCITT. `SET` uses normalized int16/int16/int16/uint16 values plus
explicit modes. Application ACK and RF-delivery indication carry different
ACK classes; a delivery indication never refreshes the command watchdog.
This core is not connected to ESP-NOW or the legacy 25-byte RC receiver.
