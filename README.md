# Atom Cam 1 × StampFly

Atom Cam 1 supplies local vision input, the Mac runs the vision and flight
policy, and an M5Stack StampFly keeps the existing stabilization loop and
hard failsafe.

~~~text
Atom Cam 1
    │ local JPEG / RTSP / WebRTC
    ▼
Mac controller
    ├─ vision
    ├─ flight policy
    └─ local watchdog
          │ USB serial, CF1
          ▼
StampFly
    ├─ existing stabilization
    ├─ USB claim fencing
    ├─ 250 ms command watchdog
    └─ explicit ARM
~~~

The camera in this project is the original Atom Cam (Ingenic T31). It is not
the separate Atom Cam 2 device that may already appear on the Wi-Fi network.

## Current safety state

The first custom firmware has been built and flashed as camfly-safe.

- CAMFLY_SAFE_TEST=1 is enabled.
- All four low-level motor PWM functions write literal zero in this build.
- USB control must claim the aircraft before it can send SET or ARM.
- ARM is explicit and never automatic.
- A missing SET heartbeat for more than 250 ms disarms, returns to parking mode,
  and keeps the USB claim held so ESP-NOW cannot silently take over.
- A flight-enabled custom build has not been built or flashed.

Before flashing, verify the exact board identity and serial device locally.
Do not publish a host-specific serial device name or hardware MAC address in
this repository.

Factory flash backup:

- keep the backup outside Git;
- record its size and SHA-256 in a private inventory; and
- never delete or overwrite it during development.

## Pinned upstream sources

- M5Stack/M5StampFly: fa7b6a59c7f297d398d381840184451dce137c48
- mnakada/atomcam_tools: 313048b4d652b0058271ffa42de1289e9d5d08ee

firmware/stampfly/ is the project-owned standalone copy of the StampFly
firmware. The vendor repositories remain submodules.

## Build and flash StampFly

Use the project virtual environment:

~~~sh
cd firmware/stampfly
../../.venv/bin/pio run -e camfly-safe
~~~

The safe image is:

    firmware/stampfly/.pio/build/camfly-safe/firmware.bin

Before every upload, enumerate /dev/cu.usbmodem*. If more than one device is
present, stop and identify the StampFly explicitly with esptool; never infer
the port.

~~~sh
find /dev -maxdepth 1 -name 'cu.usbmodem*' -print
STAMPFLY_PORT=/dev/cu.usbmodemXXXX
../../.venv/bin/esptool --port "$STAMPFLY_PORT" chip-id
../../.venv/bin/pio run -e camfly-safe -t upload --upload-port "$STAMPFLY_PORT"
~~~

The first on-device checks are:

~~~text
CF1 HELLO
CF1 STATUS
CF1 CLAIM
CF1 SET 1 0 0 0 0 0 5
CF1 ARM
~~~

Keep sending zero SET packets at 20 Hz or faster. Stopping them must produce
CF1 EVENT DISARM watchdog; STATUS must retain claimed=1, report armed=0,
connected=0, mode=3, and safe_test=1.

## Mac host controller

The host dependencies are pyserial and the Python standard library.

The first controller mode claims the aircraft, sends zero-control SET packets at
20 Hz, and prints telemetry. It never calls ARM:

~~~sh
python host/controller.py --port /dev/cu.usbmodemXXXX
~~~

The protocol implementation is in host/stampfly.py. It provides:

- HELLO and CLAIM handshake
- sequence-numbered SET
- explicit ARM and DISARM methods
- local 200 ms watchdog in addition to the 250 ms firmware watchdog
- best-effort DISARM on close and exceptions
- STATUS parsing
- canonical altitude, range, and IMU validity/age/source parsing
- no automatic ARM

The host-side bounded scheduler is in `host/control_loop.py`. It publishes
immutable, expiring intents and rejects an expired intent before writing a
new SET, so a delayed STATUS or camera request cannot turn an old command into
a fresh heartbeat. The zero controller uses this scheduler and still never
calls ARM.

host/controller.py is intentionally not a vision or autonomous-takeoff
program. Vision tracking must be added only after the zero-control heartbeat,
loss-of-heartbeat, explicit ARM rejection, and recovery behavior are stable.

## Replay and dry-run integration

The default integration mode opens no camera or serial device:

~~~sh
.venv/bin/python host/integration.py --mode replay --log /tmp/camfly-replay.jsonl
~~~

Replay records use JSONL events with relative `at` seconds and `kind` values
`frame`, `status`, `camera_error`, `serial_error`, or `stop`. The dry-run gate
has no ARM method and permits only zero ANGLE/MANUAL SET packets. It records
bounded JSONL state and a summary; a fault is latched and recovery never
automatically restarts a mission. `host/mission.py` is a pure fake-input state
machine for explicit START/TAKEOFF/LAND sequencing and is not connected to a
real flight adapter.

Camera acquisition uses a bounded latest-frame slot. To measure request and
receive behavior without saving raw images:

~~~sh
.venv/bin/python host/camera_probe.py --url http://atomcam.local --duration 60 \
  --output /tmp/atomcam-probe.json
~~~

`captured_at` on the legacy `AtomCamFrame` is receive wall time; the JPEG
endpoint does not provide a capture timestamp.

The explicitly selected hardware dry-run keeps the same gate and sends only
zero SET packets after confirming `safe_test=1`:

~~~sh
.venv/bin/python host/integration.py --mode safe-hardware \
  --camera-url http://atomcam.local --port /dev/cu.usbmodemXXXX \
  --log /tmp/camfly-safe-hardware.jsonl
~~~

It has no ARM path and should only be used with the documented motor-stop
preconditions.

## Qualification log evaluation

`host/qualification.py` evaluates hardware-free schema-v1 qualification JSONL.
It reports position-error p95/max, run outcomes including aborted/failed runs,
validity gaps, age/period, saturation, and consecutive completions. It embeds
no default flight limits: without an explicit criteria JSON, `qualified` is
`null`. This prevents planning targets from becoming accidental flight
authorization.

~~~sh
.venv/bin/python -m unittest host.tests.test_qualification -v
.venv/bin/python host/qualification.py /path/to/qualification.jsonl \
  --criteria /path/to/reviewed-criteria.json
~~~

See `docs/flight-qualification.md` for the G0-G5 gate and the remaining
low-latency camera, calibration, free-flight link, altitude/LAND, and flight
build prerequisites.

The G1 plant fixture is deterministic and hardware-free. It uses the
`world_frd` frame, explicit command expiry, and injectable delay/drop/jitter
without opening a camera, serial port, or radio:

~~~sh
.venv/bin/python host/flight_sim.py --output /tmp/camfly-simulation.jsonl
~~~

## SD runtime deployment

`host/camera_deploy.py` supports explicit `inspect`, `stage`, `activate`,
`status`, and `rollback` operations. Every call requires a known-hosts file,
expected original-camera MAC, and expected model. It uses strict SSH host-key
checking, versioned releases under the SD card, manifest hashes, same-SD
rename+sync active/previous markers, and no automatic restart/reboot. These
markers are not claimed to be power-loss atomic. Use `--dry-run` to
validate a local bundle and show the operation without SSH writes. It never
updates internal camera flash.

## Atom Cam 1 local stream

vendor/atomcam_tools targets the original Atom Cam / Ingenic T31 and boots
the modified environment from microSD. Its build output contains:

- factory_t31_ZMC6tiIDQN
- rootfs_hack.squashfs
- authorized_keys
- hostname

The modified camera exposes local services, including:

- JPEG snapshot: http://<camera>/cgi-bin/get_jpeg.cgi
- RTSP main: rtsp://<camera>:8554/video0_unicast
- RTSP sub: rtsp://<camera>:8554/video1_unicast
- WebRTC: local port 8555

The helper in host/atomcam.py consumes the local JPEG endpoint and provides
the RTSP URL. It has no cloud dependency:

~~~sh
python host/atomcam.py --url http://atomcam.local --once
python host/atomcam.py --url http://atomcam.local --save /tmp/atomcam.jpg
python host/atomcam.py --url http://atomcam.local --rtsp video1
~~~

Decoded RTSP/WebRTC frames use the bounded adapter in
`host/camera_stream.py`. The selected backend is the FFmpeg CLI because it is
available on macOS arm64 without adding a Python decoder dependency and keeps
decoder I/O behind a worker. The local development machine reports FFmpeg
9.0.1; install it with `brew install ffmpeg`. The rawvideo pipe has no source
PTS/DTS side channel, so the adapter leaves those fields unknown and records
receive and decode-complete monotonic clocks separately.

Run the deterministic fixture or a duration-bounded live probe:

~~~sh
.venv/bin/python host/camera_stream_probe.py --backend synthetic
.venv/bin/python host/camera_stream_probe.py --backend ffmpeg \
  --url rtsp://<camera>:8554/video1_unicast --duration 10
~~~

Probe results are measurement evidence only; a live camera result does not
authorize flight.

The endpoint is not usable until the Atom Cam 1 microSD image has been booted.
No microSD card is selected or modified by this repository.

## Atom Cam build boundary

The upstream macOS build uses Docker through Lima:

~~~sh
cd vendor/atomcam_tools
make lima
make
~~~

No physical disk is touched by these commands. A card must be identified using
a before/after disk inventory only after it is newly inserted. Never select the
existing DevSSD, devstorage, VM image, or any other disk as the Atom Cam card.

Lima needs enough free disk for the builder. If a separate `LIMA_HOME` is used,
keep it outside tracked files and set Docker's `DOCKER_HOST` to the endpoint
reported by Lima before running the upstream build. For example:

~~~sh
LIMA_HOME=/path/to/camera-and-fly/.lima-home
~~~

The Atom Cam target remains original Atom Cam 1. Do not update or configure the
Wi-Fi-visible atomcam2.

## Atom Cam artifact verification

Run `atomcam-sd/build.sh` to create a versioned artifact and manifest. Build
outputs, logs, hashes, and test-device results are local evidence and must stay
outside the public repository. The verifier checks that the archive contains
exactly the expected files, validates CRC and magic bytes, and records source
evidence for the read-only guards.

## Internal-flash investigation (2026-09-08)

A firsthand original Atom Cam teardown records a T31 U-Boot boot log and a
128-Mbit (16-MByte) flash dump:
https://honeylab.hatenablog.jp/entry/2020/05/01/112933
This supports the T31 target. Do not apply the Thingino wiki's T20/Wyze-v2
instructions to the original retail Atom Cam without hardware verification.

The pinned kernel.config defines a 16-MiB MTD layout (KiB): boot 256,
kernel 1984, rootfs 3904, app 3904, kback 1984, aback 3904, cfg 384, para 64.
This is source configuration, not a measurement of a particular camera. The
generated 46,182,400-byte rootfs cannot fit in that internal flash. Hardware
boot validation, live MTD inspection, and any SSH test must be recorded in a
private log with device-specific values omitted from this repository.

Do not describe ordinary atomcam_tools startup as a strictly read-only backup
environment: overlay_rootfs/etc/init.d/S16fwupdate reads FWGRADEUP from mtd7
and can erase/program flash when an update is pending. S20mountfs also mounts
the flash cfg filesystem without an explicit read-only option when copying
configuration. A backup workflow must account for these startup paths before
booting; neither was executed against the camera during this investigation.

Manufacturer stock recovery uses model-specific demo.bin on microSD:
https://info.atomtech.co.jp/support/faq/528/
This is not proof of recovery after bootloader damage or arbitrary repartitioning.

Do not publish camera IP addresses, MAC addresses, host keys, credentials, raw
dumps, or other device-specific evidence. Preserve raw dumps privately;
configuration partitions may contain credentials. The SD card may be written
by the documented workflow, but internal camera flash must not be written
without a separate recovery plan.
