# Open issues — Luna Max execution index

Updated: 2026-09-09

このディレクトリの issue は **1 issue = 1 Luna Max 実装/検証セッション** を原則とする。親 coordinator から直接実装を始めず、`Luna-Ready` と依存を確認して子 issue を1件だけ選ぶ。

## 共通開始手順

1. repo `/Volumes/DevSSD/Developer/camera-and-fly` で `git status --short --branch` を確認する。
2. 既存未コミット変更があれば保持し、対象 issue 外の変更を巻き戻さない。
3. issue の `Luna Max 着手契約`、依存、対象外を読む。
4. pure logic → fake fixture → safe build → 明示された実機検証、の順を崩さない。
5. hardware acceptance を code test で代用してチェックしない。
6. device-specific IP/MAC/host-key/credential/raw private evidence をtracked fileへ残さない。
7. 完了時は issue の実装記録/checkboxを実証範囲だけ更新し、`git diff --check` と issue 指定testを実行して1コミットにまとめる。

## Ready now — software only

| Issue | 1セッションの成果 |
|---|---|
| [espnow-flight-protocol-core](20260908-espnow-flight-protocol-core.md) | pure wire/session codec/state + tests |
| [sd-build-failure-hardening](20260908-sd-build-failure-hardening.md) | build failure/signal/concurrency fake tests |
| [ssh-runtime-deploy-fault-injection](20260908-ssh-runtime-deploy-fault-injection.md) | deploy interruption/concurrency/idempotency fixtures |

## Ready only with explicitly verified hardware

| Issue | Gate |
|---|---|
| [atomcam-boot-layout-inspection](20260908-atomcam-boot-layout-inspection.md) | operator-verified original Atom Cam 1 + strict known_hosts; read-only only |
| [cf1-usb-flood-qualification](20260908-cf1-usb-flood-qualification.md) | explicit StampFly port + `camfly-safe`; zero SET / ARM 0 only |
| [telemetry-validity](20260908-telemetry-validity.md) | software contract complete; remaining fake-clock + `camfly-safe` validity transition acceptance only |

## Dependency chains

### Camera → StampFly closed-loop integration

Coordinator: [camera-stampfly-closed-loop](20260909-camera-stampfly-closed-loop.md). Architecture: `../../docs/camera-stampfly-control-architecture.md`.

Existing camera/vision + altitude + ESP-NOW chains
→ `outer-position-controller`
→ [vision-control-runtime-integration](20260909-vision-control-runtime-integration.md)
→ `flight-adapter-integration`
→ [camera-stampfly-closed-loop-safe-qualification](20260909-camera-stampfly-closed-loop-safe-qualification.md)
→ `flight-build-environment` → `flight-preflight-gate` → `flight-g3-no-prop` → `flight-criteria-freeze` → `staged-flight-test`.

The two new implementation/qualification issues are blocked until their explicit dependencies are complete; do not select them early.

### Camera / vision

`camera-stream-decoder-adapter`
→ `camera-stream-live-qualification`
→ `vision-detector-calibration`
→ `vision-live-qualification`
→ `outer-position-controller`

Parent coordinators: [camera-low-latency-stream](20260908-camera-low-latency-stream.md), [vision-localization-design](20260908-vision-localization-design.md).

### ESP-NOW free-flight link

`espnow-flight-protocol-core`
→ (`espnow-host-transport` + `espnow-gateway-firmware` + `espnow-stampfly-receiver`)
→ `espnow-link-safe-qualification`
→ `flight-adapter-integration`

Parent coordinators: [espnow-gateway-flight-link](20260908-espnow-gateway-flight-link.md), [flight-link-design](20260908-flight-link-design.md).

### Altitude / landing

`telemetry-validity`
→ `altitude-command-api`
→ `landing-failsafe-adapter`
→ `flight-adapter-integration`

Source contract: [altitude-failsafe-design](20260908-altitude-failsafe-design.md).

### Atom Cam boot recovery

`atomcam-boot-layout-inspection`
→ `initramfs-rootfs-selector`
→ `boot-health-rollback`

In parallel after layout/selector:
`boot-image-deploy-cli`

Then:
`sd-recovery-powerloss-test`

Separate runtime-only path:
`ssh-runtime-deploy-fault-injection`
→ `ssh-runtime-deploy-live-rollback`

Build hardening:
`sd-build-failure-hardening`

Parent coordinators: [ssh-update-recovery-design](20260908-ssh-update-recovery-design.md), [ssh-runtime-deploy](20260908-ssh-runtime-deploy.md), [sd-release-verification](20260908-sd-release-verification.md).

### Flight qualification / enablement

`flight-dynamics-simulator`
→ `outer-position-controller`

After semantic/link dependencies:
`flight-adapter-integration`
→ `flight-build-environment`
→ `flight-preflight-gate`
→ `flight-g3-no-prop`

After camera/vision/link/G1-G3 measurements:
`flight-criteria-freeze`
→ `staged-flight-test` (G4/G5)

Parent coordinators: [flight-qualification](20260908-flight-qualification.md), [flight-build-preflight](20260908-flight-build-preflight.md), [flight-roadmap](20260908-flight-roadmap.md).

## Completed scopes — do not restart

These files remain as contracts/history and should not be selected as implementation tasks:

- [camera-stream-decoder-adapter](20260908-camera-stream-decoder-adapter.md) — software adapter/fixtures complete; live qualification remains separate
- [flight-dynamics-simulator](20260908-flight-dynamics-simulator.md) — deterministic G1 simulator complete
- [camera-stream-measurement](20260908-camera-stream-measurement.md)
- [camera-stampfly-dry-run](20260908-camera-stampfly-dry-run.md)
- [host-control-scheduler](20260908-host-control-scheduler.md)
- [mission-supervisor](20260908-mission-supervisor.md)
- [altitude-failsafe-design](20260908-altitude-failsafe-design.md)
- [ssh-update-recovery-design](20260908-ssh-update-recovery-design.md)

`cf1-protocol-hardening` has one remaining hardware gate only; use [cf1-usb-flood-qualification](20260908-cf1-usb-flood-qualification.md) rather than reopening its completed software work.
