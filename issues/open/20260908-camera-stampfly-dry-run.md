# カメラ入力と StampFly をモーター停止状態で統合する

Status: open
Model: unknown
Created: 2026-09-08
Updated: 2026-09-08
Branch: codex/20260908-camera-stampfly-dry-run

## 概要

映像取得と CF1 制御を同時に動かし、録画 replay と障害注入で連携経路を検証する dry-run モードを追加する。

## 背景

既存 controller はゼロ SET と STATUS のみでカメラと連動しない。姿勢制御用の観測器・位置制御器はまだない。

## 問題

単体で動く camera と serial を単純に直列接続すると制御周期が止まる。連携の合否と実飛行の合否を分ける必要がある。

## 目標

一つの CLI で「最新映像の状態」と「機体状態」を時系列に記録し、全異常時に規定の fault に遷移する。

## 対象外

機体への非ゼロ指令、ARM、ライブ位置推定、飛行用 transport。

## 提案する方針

1. `host/integration.py` を追加し、camera worker と control loop の latest-value interface を合成する。既存 controller は zero-control の入口を維持する。
2. `--mode replay` を既定にして実機 I/O を作らない。`--mode safe-hardware --camera-url ... --port ...` は明示選択、初期 STATUS に safe_test=1 がない場合は開始を拒否。途中で safe_test/claim が変化したら fault とする。
3. hardware への SET は純粋な出力 gate で常にゼロ/ANGLE/MANUAL に限定し、ARM API をこの adapter に公開しない。別スレッドや CLI オプションから bypass できない構造にする。
4. camera valid/stale/error、last receive age、serial last send age、ACK/STATUS、event、fault reason を JSONL に記録する。monotonic 相対時刻と schema_version/session ID を付ける。frame raw bytes はログから分離する。
5. `host/replay.py` でフレーム到着イベントと telemetry fixture を fake clock 上で再生する。欠損、古いフレーム、逆順、HTTP2秒停止、serial停止、queue満杯を入力できるようにする。raw画像なしの synthetic fixture で通常テストを動かす。
6. 終了時に run summary（周期の max/p95、camera gaps、faults、送信コマンド数）を出す。ログ書き込み不能は制御ループを無期限に止めず明示 fault にする。保存 queue と保存量に上限を設ける。

## 受け入れ条件

- [ ] replay は camera/serial 接続なしで同じ入力から同じ状態遷移を再現する。
- [ ] 全試験で ARM が0件、hardware SET の全制御値が0であることを wire log で確認する。
- [ ] camera の2秒停止が serial loop の2秒停止にならない。
- [ ] serial停止・stale intent・不正 STATUS で fault を latch し、復帰で自動再開しない。
- [ ] safe 実機で10分間の連携ログと欠損注入結果を保存する。

## テスト計画

`host/tests/test_integration.py` の fake wire assertion と replay golden state sequence を実行。機体の安全ビルドを確認して実機10分試験。元の zero controller の実行例も smoke test する。

## リスク

ゼロ SET が継続する試験は飛行制御の安定性の証明にはならない。カメラの通信復旧とミッション再開を区別する。

## 変更履歴

`CHANGES.md` impact: yes

項目案：映像取得と CF1 制御を同時に動かし、録画 replay と障害注入で連携経路を検証する dry-run モードを追加する。

実装時に既存の変更履歴規約を確認する。現時点では `CHANGES.md` は存在しないため、計画作成だけを理由に新設しない。

## 注記

実装担当の想定：`gpt-5.6-luna`、reasoning effort `max`。`Model` は本文の最終編集者を表すため、担当予定モデルとは区別する。作成環境から正確なモデル識別名を確認できないため `unknown`。実装着手時に自身の識別名へ更新する。

依存：[20260908-cf1-protocol-hardening](20260908-cf1-protocol-hardening.md)、[20260908-host-control-scheduler](20260908-host-control-scheduler.md)、[20260908-camera-stream-measurement](20260908-camera-stream-measurement.md)

全体計画：[20260908-flight-roadmap](20260908-flight-roadmap.md)。共通の実装手順、既存資産の保護、未確定条件を着手前に読む。
