# G4/G5 の段階実飛行と結果集計を実施する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: hardware-validation
Luna-Ready: blocked-on-g1-g2-g3-and-criteria

## Luna Max 着手契約

G4/G5専用。`flight-criteria-freeze`と全G1-G3/free-flight dependencyが完了するまで着手しない。実施時は全runを保存し、失敗/abortを除外しない。

## 概要

G0〜G3 を通過した構成だけを対象に、管理された低高度の短時間飛行と反復ホバリング/着陸を実施し、全 run を qualification evaluator で集計する。

## 目標

試験前に物理条件と定量 criteria を固定し、成功・失敗・operator abort を同じ run 集合として保存する。5回連続完遂等の候補値は、先行測定後に明示 criteria として承認された場合のみ使用する。

## 対象外

屋外、高速軌道、障害物回避、人の近傍での試験、未承認 criteria による飛行。

## 実施前ゲート

- G1 simulator と外側 controller の結果がレビュー済み。
- G2 safe-hardware と G3 no-propeller が成功済み。
- free-flight link、low-latency camera、calibration、telemetry validity、altitude/LAND adapter が実装・測定済み。
- 対象機体、電源/バッテリー、場所、飛行領域、高度、観測有効範囲、impact mitigation、operator START/abort/stop を手順書で確定。
- criteria JSON を試験前に固定し、途中で都合よく変更しない。

## 受け入れ条件

- [ ] G4 の各 run に開始条件、operator、criteria、software/firmware/calibration ID が記録される。
- [ ] camera/link/telemetry/bounds/battery fault ごとの中止・landing/emergency path を実地で確認する。
- [ ] G5 の completed/aborted/failed run を欠落なく同一データセットに保存する。
- [ ] `host/qualification.py` で p95/max、success rate、連続完遂数を再計算できる。
- [ ] 合格しなかった run を削除・除外して結果を作らない。

## 依存

[20260908-flight-g3-no-prop](20260908-flight-g3-no-prop.md)、[20260908-flight-criteria-freeze](20260908-flight-criteria-freeze.md)、[20260908-outer-position-controller](20260908-outer-position-controller.md)、[20260908-espnow-link-safe-qualification](20260908-espnow-link-safe-qualification.md)、[20260908-vision-live-qualification](20260908-vision-live-qualification.md)
