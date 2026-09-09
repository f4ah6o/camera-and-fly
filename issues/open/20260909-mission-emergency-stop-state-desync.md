# P1-1: IDLE / COMPLETE でも EMERGENCY_STOP を欠落させない

Status: open
Model: Luna Max
Created: 2026-09-09
Updated: 2026-09-09
Kind: implementation
Luna-Ready: yes
Branch: main

## 概要

`host/mission.py` の純粋な mission FSM で、operator の `EMERGENCY_STOP` を物理状態に応じて必ず semantic action へ変換する。FSM が `IDLE` または `COMPLETE` でも、telemetry が機体の危険な状態を示す場合に停止要求を捨てない。

## 背景

mission supervisor は `HealthSnapshot.armed` と `HealthSnapshot.grounded` を持ち、実機状態と FSM 状態を別々に表現している。既存の emergency-stop 分岐は active な mission state だけを対象にしている。

## 問題

現在は `IDLE` / `COMPLETE` で `EMERGENCY_STOP` を受けても action を生成しない。そのため、例えば mission state が `IDLE` でも `armed=True` または `grounded=False` の異常状態なら、operator の緊急停止が wire action まで到達しない。

## 目標

FSM state と実機 telemetry が食い違う場合でも、operator の緊急停止を fail-safe に `ActionKind.EMERGENCY_STOP` として返す。正常な `IDLE` / `COMPLETE` の通常遷移は変更しない。

## Safety invariant

- `operator_event is EMERGENCY_STOP` かつ `health.armed is True` または `health.grounded is False` のとき、FSM state に関係なく `EMERGENCY_STOP` action を発行する。
- emergency stop の入力が通常の `NONE`、`START`、`RESET`、飛行継続 action にすり替わらない。
- emergency stop 処理で mission が自動的に READY、ARM、TAKEOFF、HOLDING へ復帰しない。
- `mission.py` は引き続き I/O、serial、sleep を持たず、semantic action の生成だけを担当する。

## このissueだけでやること

- `IDLE` / `COMPLETE` を含む emergency-stop の優先順位または物理状態判定を修正する。
- emergency-stop の理由と既存の latched fault semantics を保つ、または安全性を損なわない形で整理する。
- 指定された mission unit tests を追加・更新する。

## 対象ファイル

- `host/mission.py`
- `host/tests/test_mission.py`

## 提案する方針

operator の `EMERGENCY_STOP` を state-specific な通常遷移より先に評価し、少なくとも `armed=True` または `grounded=False` なら `EMERGENCY_STOP` action を返す。全 state で最優先処理する実装も許容するが、その場合も正常な `IDLE` / `COMPLETE` の `NONE` / `RESET` 回帰をテストで固定する。

## 対象外

- real adapter、serial、wire protocol、firmware の変更
- `ControlLoop.stop()` の DISARM 処理（P1-2）
- mission FSM の状態追加、timeout 設計、通常の離陸・着陸フローの再設計
- hardware を使った実機試験

## 受け入れ条件

- [ ] `IDLE + armed=True + EMERGENCY_STOP` が `ActionKind.EMERGENCY_STOP` を返す。
- [ ] `IDLE + grounded=False + EMERGENCY_STOP` が `ActionKind.EMERGENCY_STOP` を返す。
- [ ] `COMPLETE + armed=True + EMERGENCY_STOP` が `ActionKind.EMERGENCY_STOP` を返す。
- [ ] `COMPLETE + grounded=False + EMERGENCY_STOP` を含め、異常な実機状態で停止 action が抑制されない。
- [ ] 正常な `IDLE` / `COMPLETE` の通常入力は、既存どおり `NONE` または `RESET` と state を返す。
- [ ] emergency-stop の処理で ARM、TAKEOFF、通常 SET 相当の semantic action が同じ transition から生成されない。
- [ ] 既存の active-state emergency stop、FAULT latch、action ID のテストが回帰しない。

## 必須tests

- `host/tests/test_mission.py` に次の4ケースを追加する。
  - `IDLE + armed=True + EMERGENCY_STOP`
  - `IDLE + grounded=False + EMERGENCY_STOP`
  - `COMPLETE + armed=True + EMERGENCY_STOP`
  - `COMPLETE + grounded=False + EMERGENCY_STOP`
- 正常な `IDLE` / `COMPLETE` behavior regression を table-driven または同等の deterministic test で検証する。
- 既存の active-state emergency-stop と FAULT latch のテストを実行する。

## テスト計画

まず `.venv/bin/python -m unittest host.tests.test_mission -v` を実行し、必要に応じて `.venv/bin/python -m unittest discover -s host/tests -v` で host 全体を確認する。実時間、serial、実機 I/O はテストに持ち込まない。

## dependencies

- 基盤契約：[20260908-mission-supervisor](20260908-mission-supervisor.md)
- 実装上の外部 dependency：なし
- hardware dependency：なし

## リスク

全 state 最優先にすると、正常な operator event の意味を変える可能性がある。異常な `armed` / `grounded` を確実に拾いつつ、正常な `IDLE` / `COMPLETE` と RESET の回帰を明示的に固定する。

## 変更履歴

`CHANGES.md` impact: yes。これは safety-visible な emergency-stop behavior の修正候補だが、この issue 作成コミットでは `CHANGES.md` を変更しない。実装完了時に既存の変更履歴規約を確認する。

## Luna Max 着手契約

- 開始時に `git status --short --branch` とこの issue の対象外を確認し、既存の未コミット変更を保持する。
- 変更範囲は原則として `host/mission.py` と `host/tests/test_mission.py` に限定する。
- この issue では実装修正だけを行い、firmware、real transport、実機接続、別 P1/P2 issue の変更を混ぜない。
- 受け入れ条件を満たす deterministic test の結果、変更ファイル、未実施の hardware acceptance を issue に記録する。

## 注記

- 2026-09-09: review finding P1-1 を既存の mission supervisor 完了スコープとは分離した Luna-ready implementation issue として記録した。
