# camera → pose → controller → StampFly 全経路を camfly-safe で zero-output 検証する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-09
Updated: 2026-09-09
Kind: hardware-validation
Luna-Ready: blocked-on-runtime-and-flight-adapter
Branch: main

## Luna Max 着手契約

`20260909-vision-control-runtime-integration.md` と `20260908-flight-adapter-integration.md` 完了後に着手する。明示選択された original Atom Cam 1 と StampFly `camfly-safe` のみ使用する。ARM、非ゼロ motor output、flight-enabled build は禁止する。

## 概要

実 Atom Cam の採用済み live stream から decoded frame → marker pose → outer controller → bounded scheduler → flight adapter → StampFly までを一つの runtime で通し、motor output を常時ゼロに固定したまま freshness / dropout / reconnect / telemetry validity を評価する。

## この issue だけでやること

- operator が明示した camera target と StampFly port だけを使い、自動探索しない。
- `camfly-safe` capability / safe_test を開始前に確認する。
- 実 marker を静止 fixture またはプロペラ停止機体で移動し、pose 変化が runtime へ届くことを確認する。
- controller の計算結果は記録してよいが、hardware sink へは zero-output gate を通して送る。
- camera frame loss、decoder disconnect、marker loss、telemetry invalid/stale、serial/link stall を個別に注入する。
- reconnect 後に mission / ARM / TAKEOFF / ALT target が復元されないことを確認する。
- bounded JSONL evidence に frame age、pose validity、controller decision、intent expiry、wire action、telemetry validity、fault reason を保存する。

## 対象ファイル

主対象は integration harness / qualification logging / tests。既存 production logic の変更は、この実機試験で再現した欠陥の最小修正に限定する。raw video と device-specific identity は Git に入れない。

## 対象外

- flight-enabled firmware
- propeller 回転
- ARM
- TAKEOFF 実行
- 非ゼロ motor output
- controller gain tuning
- G3/G4/G5 flight qualification

## Safety invariants

- hardware run 中の ARM count = 0。
- motor PWM は `camfly-safe` により全 channel literal zero。
- hardware transport へ送る SET は zero-only gate を通す。
- stale/invalid pose の後に新規非ゼロ intent を生成しない。
- camera / decoder / transport reconnect で mission を自動再開しない。
- telemetry validity が unknown/stale のとき mission progress を進めない。

## 受け入れ条件

- [ ] 10分以上の連続 run で camera → pose → controller → scheduler → flight adapter → StampFly の全段ログが相関できる。
- [ ] ARM count = 0、非ゼロ hardware SET = 0、motor PWM = 0 を evidence で確認する。
- [ ] marker loss / camera loss / stale pose で controller path が fail closed する。
- [ ] telemetry invalid/stale と link stall で mission progress / intent continuation が停止する。
- [ ] reconnect 後も explicit START なしに mission が再開しない。
- [ ] failure injection 後の recovery が新しい observation / session / explicit operator action を要求する。
- [ ] evidence path、実行コマンド、未検証条件を issue に記録する。

## テスト計画

最初に replay / synthetic fixture で同じ fault matrix を通し、その後 `camfly-safe` hardware を使う。実機 run では camera/serial の target を引数で明示し、自動 discovery を追加しない。

## 依存

- `20260909-vision-control-runtime-integration.md`
- `20260908-flight-adapter-integration.md`
- `20260908-camera-stream-live-qualification.md`
- `20260908-vision-live-qualification.md`
- `20260908-telemetry-validity.md`

## 注記

この issue を通過しても flight authorization ではない。次段は `flight-build-environment` → `flight-preflight-gate` → `flight-g3-no-prop` であり、free-flight はさらに後の staged flight gate に従う。
