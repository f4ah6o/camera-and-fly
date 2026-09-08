# G4/G5 実施前に flight qualification criteria を明示JSONとして凍結する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: qualification
Luna-Ready: blocked-on-measurements
Branch: main

## 概要

camera/vision/link/altitude/G1/G2/G3の測定結果を入力に、実飛行前の定量criteriaと反復数を`host/qualification.py`が読めるJSONとして確定する。

## この issue だけでやること

- 全依存測定のp95/max/valid rangeを一覧化する。
- horizontal/altitude error、duration、success run count/consecutive run count、fault policyを決める。
- criteria JSONを作り、invalid/aborted/failed runを分母から除外しない契約を確認する。
- criteria変更手順を「試験run開始前のみ」に固定する。

## 対象外

実飛行、ゲイン調整、測定不足を仮値で埋めること。

## 受け入れ条件

- [ ] criteria各値に測定根拠/依存artifactがある。
- [ ] required run count/consecutive countが固定される。
- [ ] aborted/failed/invalid sampleの集計規則が明示される。
- [ ] criteria JSONを`host/qualification.py`でload/testできる。
- [ ] G4開始後に結果都合でcriteriaを変更しない手順が記録される。

## Luna Max 着手契約

依存測定が欠けている間はblockedとして維持する。0.3–0.5m等の既存候補値を自動採用しない。

## 依存

[20260908-flight-qualification](20260908-flight-qualification.md)、[20260908-camera-stream-live-qualification](20260908-camera-stream-live-qualification.md)、[20260908-vision-live-qualification](20260908-vision-live-qualification.md)、[20260908-espnow-link-safe-qualification](20260908-espnow-link-safe-qualification.md)、[20260908-flight-g3-no-prop](20260908-flight-g3-no-prop.md)
