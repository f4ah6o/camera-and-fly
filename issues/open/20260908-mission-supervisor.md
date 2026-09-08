# 離陸・保持・着陸ミッションの状態機械を fake 入出力で実装する

Status: open
Model: unknown
Created: 2026-09-08
Updated: 2026-09-08
Branch: codex/20260908-mission-supervisor

## 概要

操作・観測の健全性・制御期限に応じてミッションを進める純粋な状態機械と replay テストを実装する。

## 背景

既存 host はzero controllerだけ。位置推定、自由飛行用transport、高度/着陸指令はそれぞれ別調査で仕様化するため、この課題はsemantic actionとfake adapterまでを扱う。

## 問題

制御器に開始/復帰/停止判定を埋め込むと、映像復帰や再接続から暗黙に再離陸する挙動を生みやすい。

## 目標

現在時刻・操作・health snapshotを入力し、次状態とsemantic actionを返す deterministic な supervisor を作る。

## 対象外

実機 ARM/TAKEOFF/LAND adapter、位置推定器、PID/制御ゲイン、flight firmware の作成。

## 提案する方針

1. `host/mission.py` に `step(state, operator_event, health, now_monotonic)` を置く。I/Oやsleepを内部に持たせない。状態は IDLE、PREFLIGHT、READY、ARMING、TAKING_OFF、HOLDING、LANDING、COMPLETE、FAULT とし必要性をテストで説明する。
2. `HealthSnapshot` は calibration_valid、observation_valid/age、telemetry_valid/age、transport_ready、battery_ok、within_bounds、capabilities、armed/grounded を含む。未取得はFalse/unknownとして扱い、boolの暗黙truthinessを使わない。
3. IDLE→PREFLIGHT は検査開始、全条件成立→READY。READYからはoperatorの明示STARTイベントだけがARMINGを許す。ARM確認後もTAKEOFFは別semantic actionにする。再接続/フレーム復帰/プロセス再起動はSTARTではない。
4. taking_off→holding、holding→landing、landing→completeは時刻だけで判定せず適切な到達/接地feedbackを必須にする。各状態にtimeoutと理由を設ける。通常終了と故障終了を区別する。
5. soft失敗は `REQUEST_SAFE_RECOVERY(reason)`、緊急停止は `EMERGENCY_STOP(reason)` を出す。safe recoveryの物理動作は高度/failsafe設計のadapterに委譲し、未接続adapterは起動不可にする。未知状態で「とりあえずホバリング」を返さない。
6. FAULTはlatched。operator reset→IDLEに戻せても改めてpreflight/STARTが必要。再送はaction IDで識別し、既に完了したARM/TAKEOFFを重複実行しない。
7. `host/tests/test_mission.py` に状態表から正常系列と各状態の障害系列を追加する。設定のhold秒数、bounds、age、battery閾値は単位とfinite validationを持ち、試験用値と実飛行プロファイルを混同しない。

## 受け入れ条件

- [ ] 明示STARTなしにARM/TAKEOFF actionが出ない。
- [ ] 正常な離陸→保持→着陸→接地→完了系列と各timeout系列を再現できる。
- [ ] FAULT後の映像復帰/通信再接続でREADYや飛行へ自動復帰しない。
- [ ] 未校正、unknown telemetry、stale観測、bounds逸脱が状態表どおり処理される。
- [ ] real adapterは未実装であることをCLI/文書に明示し、fakeミッションで実機I/Oを開かない。

## テスト計画

`.venv/bin/python -m unittest discover -s host/tests -v`。table-drivenテストで各状態×各faultを網羅。fake clockでdeadline直前/一致/超過、重複START、reset、旧action ACKを検証する。

## リスク

本課題の成功はミッション制御フローの検証であり、飛行物理の検証ではない。後続adapterでsemantic actionを既存DISARMへ安易に代入しない。

## 変更履歴

`CHANGES.md` impact: yes

項目案：操作・観測の健全性・制御期限に応じてミッションを進める純粋な状態機械と replay テストを実装する。

実装時に既存の変更履歴規約を確認する。現時点では `CHANGES.md` は存在しないため、計画作成だけを理由に新設しない。

## 注記

実装担当の想定：`gpt-5.6-luna`、reasoning effort `max`。`Model` は本文の最終編集者を表すため、担当予定モデルとは区別する。作成環境から正確なモデル識別名を確認できないため `unknown`。実装着手時に自身の識別名へ更新する。

依存：[20260908-host-control-scheduler](20260908-host-control-scheduler.md)

全体計画：[20260908-flight-roadmap](20260908-flight-roadmap.md)。共通の実装手順、既存資産の保護、未確定条件を着手前に読む。
