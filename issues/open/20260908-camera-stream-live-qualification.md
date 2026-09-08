# Atom Cam RTSP/WebRTC の実機低遅延性能を測定し採用経路を決める

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: hardware-validation
Luna-Ready: blocked-on-decoder-adapter
Branch: main

## 概要

software decoder adapter 完了後、識別済み original Atom Cam 1 の候補streamを60秒以上ずつ測定し、閉ループ観測に採用する経路を1つ決める。

## この issue だけでやること

- operator が明示した target/URL だけを使い、自動探索しない。
- RTSP main/sub と実装可能な WebRTC 経路を同一probe契約で測定する。
- decoded FPS、interarrival、decode/queue age、drop/late、CPU負荷の p50/p95/p99/max を保存する。
- LED/表示タイマー等の独立基準で exposure-to-receive を測り、測定誤差を記録する。
- 採用/不採用理由と `vision` への入力契約を docs に固定する。

## 対象ファイル

`docs/camera-measurement.md`、`docs/vision-design.md`、親 issue、ローカル `artifacts/`。probe code の修正は測定上必要な最小変更のみ。

## 対象外

marker calibration、PID、ARM、非ゼロSET、atomcam2、raw動画のGit追加。

## 受け入れ条件

- [ ] 各候補を60秒以上測定し、分位点/欠損/CPUが記録される。
- [ ] exposure-to-receive の独立測定と誤差がある。
- [ ] 1経路を採用、または全候補rejectの理由を明示する。
- [ ] reconnectだけでmission READY/ARMへ戻らないことを確認する。
- [ ] device-specific IP/MAC/host-key/raw credentialをtracked fileへ残さない。

## Luna Max 着手契約

`20260908-camera-stream-decoder-adapter.md` が完了するまで着手しない。実機結果を推測で埋めない。測定不能なら blocker と再開コマンド/必要機材だけ記録し acceptance はuncheckedのままにする。

## 依存

[20260908-camera-stream-decoder-adapter](20260908-camera-stream-decoder-adapter.md)
