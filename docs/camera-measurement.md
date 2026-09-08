# Camera measurement contract

This document records the software measurement boundary implemented by the
roadmap. It does not claim an end-to-end sensor latency measurement: the Atom
Cam JPEG endpoint does not expose an exposure timestamp.

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

## Required hardware measurement

The following remain unmeasured and must be recorded before using the stream
for control:

- camera model/MAC and the exact SD release ID;
- resolution, endpoint, illumination, and test duration (at least 60 s per
  condition);
- request latency p50/p95/p99/max, receive FPS, interarrival gaps, timeout
  rate, and JPEG byte range;
- exposure-to-receive latency using a visible timer or LED with a separately
  documented observation error;
- static scene, moving target, dark scene, and network-load conditions.

Identical JPEG hashes are not sufficient evidence of a frozen camera because a
static scene can legitimately produce identical frames.
