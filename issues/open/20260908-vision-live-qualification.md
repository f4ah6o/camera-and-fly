# marker pose の実機精度・符号・dropout を固定カメラで測定する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: hardware-validation
Luna-Ready: blocked-on-detector-calibration
Branch: main

## 概要

detector/calibration実装後、既知位置のfixtureを最低3高さ×5平面位置×複数yawで測定し、pose品質gateの実測thresholdを決める。

## この issue だけでやること

- forward/right/up/yawの正負方向を物理fixtureで確認する。
- 3高さ×5位置×複数yawのerror/dropout/latencyを保存する。
- normal/degraded lighting、occlusion、motion blur、network loadを測る。
- per-axis/yaw median/p95/max、rejected fraction、frame ageを集計する。
- measured thresholdをcalibration/profileへ反映する。

## 対象外

非ゼロflight control、PID tuning、G4/G5。

## 受け入れ条件

- [ ] physical sign testが4軸で一致する。
- [ ] 3高さ×5位置×複数yawのdatasetがある。
- [ ] occlusion/誤ID/blurをinvalidとして集計する。
- [ ] measured thresholdと採否理由がdocsに記録される。
- [ ] raw/device-specific evidenceをtracked fileへ含めない。

## Luna Max 着手契約

正解位置の測定方法/誤差を先に記録する。失敗frameを除外しない。flight actionは一切送らない。

## 依存

[20260908-vision-detector-calibration](20260908-vision-detector-calibration.md)
