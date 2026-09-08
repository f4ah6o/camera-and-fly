# Camera measurement contract

This document records the software measurement boundary and the first live
Atom Cam 1 JPEG measurement. It does not claim an exposure-to-receive latency
measurement: the JPEG endpoint does not expose an exposure timestamp.

## Recorded clocks

`AtomCamFrame` records `request_started_monotonic` and
`received_monotonic`. The legacy `captured_at` field is the local wall-clock
receive time kept for compatibility; it is explicitly not capture time.
`capture_monotonic` is `None` for this endpoint. `camera_probe.py` reports
request/receive latency and interarrival statistics, not exposure-to-Mac
latency.

## Input limits

The JPEG reader requires a positive finite timeout and a positive maximum
response size. It checks a declared `Content-Length` before reading and reads
at most the configured limit plus one bounded chunk. Broken JPEG markers,
invalid content, and oversized responses fail in the camera worker. Redirects
must remain on the configured scheme, host, and port; userinfo, query, and
fragment components are rejected. Raw frames are never written by the probe.

`CameraWorker` owns blocking requests and publishes one latest frame. Retry
backoff is bounded. Consumers inspect frame age and never wait for an HTTP
request, so a stopped camera cannot block the control scheduler.

## Live JPEG measurement — 2026-09-08

The explicitly configured original Atom Cam 1 was measured for more than
60 seconds using the local JPEG snapshot endpoint. Device-specific network and
identity values are intentionally not recorded here. The local raw report is
`artifacts/20260908-camera-jpeg-60s-v2.json` and is excluded from the public
repository.

| Item | Result |
| --- | ---: |
| requested rate | 5.0 fps |
| measured duration | 64.108 s |
| received frames | 13 |
| failed HTTP attempts | 0 |
| HTTP-attempt loss fraction | 0.0 |
| achieved receive rate | 0.203 fps |
| JPEG resolution | 1920×1080 |
| JPEG size | 117,070–119,626 bytes |
| request latency p50 | 5.135 s |
| request latency p95 | 5.145 s |
| request latency p99 | 5.146 s |
| request latency max | 5.147 s |
| interarrival p95 | 5.146 s |
| lighting | current room, not quantitatively measured |
| scene | static fixture/current room |

The capture timestamp remains unknown, so the approximately 5.15-second
request/receive latency is only a lower-bound warning about end-to-end visual
freshness. Even that lower bound is orders of magnitude above the 250 ms CF1
command watchdog and the intended control cadence. Therefore the JPEG snapshot
endpoint is **rejected as the closed-loop localization input**. It remains
useful for setup, diagnostics, and low-rate visual confirmation.

This does not reject the camera itself. RTSP and WebRTC are separate local
streaming paths and require a decoder/timestamp measurement before selecting a
flight-observation transport. See the linked low-latency stream issue.

## Remaining measurement

Before a stream is used for control, record:

- camera identity verification and exact SD release privately;
- endpoint/codec, resolution, illumination, and at least 60 seconds per test
  condition;
- decoded-frame FPS, receive/decode p50/p95/p99/max, dropped/late frames, and
  queue depth;
- exposure-to-receive latency using a visible timer or LED with a separately
  documented observation error;
- static scene, moving target, dark scene, and network-load conditions.

Identical image hashes are not sufficient evidence of a frozen camera because
a static scene can legitimately produce identical frames.
