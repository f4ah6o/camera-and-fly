# Vision localization design

This design defines the coordinate, freshness, and quality contract before a
marker detector is selected. It does not claim that live localization is ready.
The measured JPEG snapshot path is rejected for closed-loop use; the low-latency
RTSP/WebRTC issue must provide a fresher decoded-frame source first.

## Coordinate frames

The pinned StampFly source explicitly uses an aerospace body frame:

- body `+X`: forward;
- body `+Y`: right;
- body `+Z`: down;
- positive roll: left shoulder/left wing rises;
- positive pitch: nose rises;
- positive yaw: clockwise/right turn viewed from above.

The host contract names this `body_frd`. The room-fixed test frame is
`world_frd` with the same right-handed axis directions at the calibration
origin. If the world origin is on the floor, an aircraft 0.30 m above the
origin has `z_m=-0.30`; the convenience height is `height_m=-z_m`.

This choice keeps the body/control signs aligned. UI code may display
positive-up height, but it must not silently change the controller coordinate
frame.

## Observation contract

`host/vision.py` defines a pure `PoseObservation` independent of decoder and
marker library. Every observation carries:

- strict monotonically increasing observation sequence;
- receive monotonic time;
- source/capture monotonic time when the selected stream can establish one;
- `x_m`, `y_m`, `z_m`, `yaw_rad` in the documented frames;
- marker count, reprojection error, confidence;
- fixed frame identifiers.

The quality gate is fail closed. Unknown source time, stale receive/source
age, too few markers, excessive reprojection error, low confidence, or bounds
violation all make an observation invalid. A reconnect or later valid frame
does not by itself clear a mission fault.

The default numeric gate values in `host/vision.py` are software/test defaults,
not qualified flight thresholds. Flight profiles must supply measured values.

## Calibration artifact contract

A future detector implementation must write a versioned calibration artifact,
not embed calibration values in source. At minimum record:

- camera model/stream/decoder version and decoded resolution;
- camera intrinsics and distortion model with units/convention;
- marker family, physical marker size in metres, marker IDs, and room-fixed
  marker poses in `world_frd`;
- transform convention (`T_destination_source`) and quaternion/matrix ordering;
- calibration capture set identifier/hash and reprojection statistics;
- creation timestamp and tool version;
- explicit invalidation if resolution/crop/orientation changes.

A runtime calibration loader must reject missing/duplicate marker IDs,
non-finite matrices, reflection/non-rigid transforms, unsupported schema, and
stream resolution mismatch.

## Required sign test

Before live position control, place the aircraft/marker fixture at a known
origin and physically move it one axis at a time:

1. forward -> estimated `x_m` increases;
2. right -> estimated `y_m` increases;
3. upward -> estimated `z_m` decreases and positive-up height increases;
4. clockwise yaw viewed from above -> estimated `yaw_rad` increases.

Each movement is repeated in both directions and recorded. A sign mismatch is
a calibration failure, not something the controller compensates for ad hoc.

## Accuracy and dropout measurement

At least five known planar locations and at least three heights must be used.
Record per-axis median/p95/max absolute error, yaw error, marker count,
reprojection error, decoded-frame age, and rejected-frame fraction. Repeat
under normal lighting, degraded lighting, partial occlusion, motion blur, and
network load.

Thresholds for mission use are selected only after these measurements. The
qualification log must include rejected/failed runs; reporting only successful
frames is insufficient.

## Current blockers and follow-ups

- JPEG snapshot: measured 2026-09-08 at ~0.203 fps and ~5.145 s p95 request
  latency; rejected for control (`docs/camera-measurement.md`).
- Low-latency decoded input: `issues/open/20260908-camera-low-latency-stream.md`.
- Detector/calibration implementation: remains after a stream is selected;
  current `.venv` does not contain OpenCV or AprilTag bindings, so no library is
  silently assumed or pinned by this design.

The pure observation contract can already be exercised by replay/fake pose data
without opening camera or flight hardware.
