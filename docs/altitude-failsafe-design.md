# Altitude, landing, and failsafe boundary

No altitude semantics were changed by the roadmap implementation. This record
prevents the current values from being mistaken for a new flight API.

## Source facts

The CF1 protocol accepts normalized throttle `[0, 1]` and altitude mode `4`
(`AUTO_ALT`) or `5` (`MANUAL_ALT`). In `flight_control.cpp`, AUTO_ALT updates
`Alt_ref` from `thlo * 0.001`; it is not a metre-valued host altitude API.
Telemetry exposes `Altitude`, `Altitude2`, `Alt_ref`, `Alt_flag`, and ToF range,
but no complete freshness/validity contract. `auto_landing()` is an existing
firmware mode and is not the same semantic action as a host LAND request.

## Safety table

| Condition | Current safe boundary | Future decision |
| --- | --- | --- |
| valid LAND request | no new adapter yet | define landing controller and ground confirmation |
| command timeout | 250 ms watchdog disarms and keeps USB claim | do not silently convert to landing |
| camera stale/pose invalid | host mission must latch fault | choose recovery only after altitude validity is known |
| ToF/IMU invalid | not reinterpreted by host | define sensor-specific fallback |
| low voltage/bounds/emergency | not changed here | explicit operator and machine actions |

Zero throttle is not treated as zero metres, and DISARM is not treated as
landing. Any future protocol must preserve those distinctions and include
units, range, rate limit, idempotency, timeout, and telemetry validity.
