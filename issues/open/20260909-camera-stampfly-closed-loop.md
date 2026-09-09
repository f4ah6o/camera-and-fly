# Atom Cam 1 → Mac → StampFly 閉ループ連携を段階統合する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-09
Updated: 2026-09-09
Kind: coordinator
Luna-Ready: use-child-issues
Branch: main

## Luna Max 着手契約

この親 issue では直接実装しない。`issues/open/README.md` の dependency chain を見て ready な子 issue を1件だけ選ぶ。既存 issue を再実装せず、完了済み scope は再開しない。

## 概要

固定 Atom Cam 1 の低遅延映像から StampFly の pose を推定し、Mac の外側位置制御で bounded intent を生成し、StampFly の既存 400 Hz 姿勢制御へ渡す閉ループを段階的に完成させる。

設計の正本は `docs/camera-stampfly-control-architecture.md`。

## 到達目標

明示 START 後に、固定カメラで機体マーカーを観測しながら低高度離陸 → 指定位置 HOLD → LAND を行えること。ただしこの親 issue の完了は G4/G5 staged flight の evidence まで必要とし、software 実装だけで完了扱いにしない。

## 実行チェーン

### Camera / vision

1. `20260908-camera-stream-live-qualification.md`
2. `20260908-vision-detector-calibration.md`
3. `20260908-vision-live-qualification.md`

`camera-stream-decoder-adapter` の software scope は実装済みなので再開しない。

### Altitude / mission semantics

1. `20260908-telemetry-validity.md` の残 hardware acceptance
2. `20260908-altitude-command-api.md`
3. `20260908-landing-failsafe-adapter.md`

### Free-flight link

1. `20260908-espnow-flight-protocol-core.md`
2. `20260908-espnow-host-transport.md`
3. `20260908-espnow-gateway-firmware.md`
4. `20260908-espnow-stampfly-receiver.md`
5. `20260908-espnow-link-safe-qualification.md`

### Position control

1. `20260908-outer-position-controller.md`
2. `20260909-vision-control-runtime-integration.md`

`flight-dynamics-simulator` の software scope は実装済みなので再開しない。

### End-to-end safe integration

1. `20260908-flight-adapter-integration.md`
2. `20260909-camera-stampfly-closed-loop-safe-qualification.md`
3. `20260908-flight-build-environment.md`
4. `20260908-flight-preflight-gate.md`
5. `20260908-flight-g3-no-prop.md`
6. `20260908-flight-criteria-freeze.md`
7. `20260908-staged-flight-test.md`

## 共通 invariant

- Atom Cam は外部固定センサ。映像を StampFly へ送らない。
- Mac は outer loop、StampFly は既存 inner attitude loop を担当する。
- stale/invalid pose から非ゼロ intent を生成しない。
- heartbeat で古い observation / intent を延命しない。
- reconnect で ARM / TAKEOFF / ALT target / mission progress を復元しない。
- `camfly-safe` hardware acceptance では ARM 0、非ゼロ motor output 0 を維持する。
- USB cable 付き試験を free flight 成功として数えない。

## 受け入れ条件

- [ ] 採用 stream の live latency / freshness evidence がある。
- [ ] marker calibration と live pose accuracy evidence がある。
- [ ] altitude / landing / telemetry semantics が実機で validity を含めて成立する。
- [ ] free-flight transport が latency / loss / reconnect / ownership 条件を満たす。
- [ ] outer position controller と runtime composition が deterministic tests を通る。
- [ ] `camfly-safe` で camera → pose → controller → scheduler → flight adapter の全経路を zero-output で実証する。
- [ ] flight build / preflight / no-prop / staged flight の各 gate を順番に通過する。

## 対象外

人物追跡、障害物回避、屋外飛行、複数機、学習型 end-to-end controller、Mac から motor PWM を直接生成する方式。

## 注記

この coordinator は child issue の依存関係と evidence の整合性だけを管理する。1セッションで複数 child をまとめて実装しない。
