# 校正済み pose から外側位置制御指令を生成する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08

## 概要

校正済み `world_frd` pose と mission target から、StampFly 内部姿勢制御へ渡す bounded な外側位置制御指令を実装する。

## 目標

world 水平誤差を制御し、yaw に応じて `body_frd` へ変換して roll/pitch command を生成する。高度は metre-valued altitude API が利用可能になってから同じ controller boundary に接続する。

## 対象外

機体内 400 Hz 姿勢 PID の置換、未校正画像からの直接制御、実飛行ゲインの推測。

## 実装方針

- 入力は timestamp、校正済み pose、target、mission state、capability/validity とする。
- 観測時刻と送信時刻を分離し、zero-order hold の明示 expiry を持つ immutable intent を返す。
- world -> body yaw transform、符号、rad/m/s を unit test で固定する。
- angle command の saturation、slew-rate、anti-windup、integral reset を明示する。
- invalid/stale pose、bounds 外、mission fault、capability 欠落では非ゼロ intent を生成しない。
- PID/制御ゲインは G1 simulator と後続同定で決め、根拠のない既定飛行値を置かない。

## 受け入れ条件

- [ ] world/body 変換の各軸・yaw 符号が既知ベクトルで検証される。
- [ ] NaN/Inf、stale/invalid pose、bounds 外で fail closed する。
- [ ] saturation/slew/anti-windup/integral reset を deterministic test で再現する。
- [ ] generated/valid-until timestamp を heartbeat が更新しない。
- [ ] G1 simulator 上で明示 criteria を用いた結果を保存し、実飛行の成功とは区別する。

## テスト

`.venv/bin/python -m unittest discover -s host/tests -v`

## 依存

[20260908-flight-dynamics-simulator](20260908-flight-dynamics-simulator.md)、[20260908-camera-low-latency-stream](20260908-camera-low-latency-stream.md)、校正済み detector 実装、[20260908-altitude-command-api](20260908-altitude-command-api.md)
