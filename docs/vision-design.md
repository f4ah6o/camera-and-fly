# Fixed-camera localization design boundary

The current implementation stops at timestamped JPEG input. No marker
detector, calibration result, or position controller is enabled.

## Proposed coordinate contract

The future observer should report
`Observation(frame_id, received_monotonic, capture_monotonic_or_none,
position_m, yaw_rad, quality, valid, reason)`. World coordinates are proposed
as right-handed metres with `z` up. The StampFly body axes and yaw sign must be
verified against the firmware before any adapter is written; this document is
not that verification.

The transform chain is:

1. camera intrinsics and lens distortion;
2. camera-to-world extrinsics;
3. marker-to-body transform;
4. body/world pose and quality gates.

Pixel displacement must not be mapped directly to roll/pitch. Floor
homography is not a substitute for height-aware pose estimation.

## Experiment still required

Camera position, field of view, room bounds, marker dimensions/weight,
illumination, and desired height are not recorded in the repository. A
non-flying fixture should cover at least three heights, five planar positions,
multiple yaw angles, occlusion, and an incorrect marker ID. It must report
position error, missing observations, reprojection/quality error, and p95/p99
processing time with an independently measured ground truth.

AprilTag/ArUco and their versions are intentionally not fixed here; the
choice belongs to the measurement task and must be based on the actual Python
and CPU environment.
