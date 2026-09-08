# 固定カメラの marker detector と versioned calibration loader を実装する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: implementation
Luna-Ready: blocked-on-camera-stream-live-qualification
Branch: main

## 概要

採用済み低遅延streamを入力に、marker detector、camera intrinsics/extrinsics、marker-to-body transformをversioned artifactとして扱うhost実装を追加する。

## この issue だけでやること

- detector library/version/macOS arm64導入方法を一次資料で固定する。
- calibration artifact schema/load validationを実装する。
- duplicate marker/nonfinite matrix/reflection/non-rigid/resolution mismatchをrejectする。
- decoded frame→`PoseObservation`変換を実装し、既存quality gateへ接続する。
- synthetic/projected fixtureでaxis/yaw transformを検証する。

## 対象ファイル

`host/vision_detector.py`、`host/calibration.py`、host tests、`docs/vision-design.md`。live calibration dataはGitへ入れない。

## 対象外

3高さ×5位置の実測、実飛行、PID、thresholdの最終確定。

## 受け入れ条件

- [ ] detector/version/導入コマンドが固定される。
- [ ] calibration schema validationがfail closedである。
- [ ] known transform fixtureでbody/world符号が一致する。
- [ ] bad marker/calibration/resolutionをinvalid observationとして扱う。
- [ ] host testsがPASSする。

## Luna Max 着手契約

stream adapterが未完了なら着手しない。実測thresholdを推測でdefault flight limitにしない。software detector/calibrationだけを1commitにする。

## 依存

[20260908-camera-stream-live-qualification](20260908-camera-stream-live-qualification.md)、[20260908-vision-localization-design](20260908-vision-localization-design.md)
