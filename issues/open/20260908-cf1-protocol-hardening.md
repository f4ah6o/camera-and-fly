# CF1 の入力検証・鮮度・処理時間を堅牢化する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: coordinator
Luna-Ready: use-child-issue-for-last-hardware-gate
Branch: codex/20260908-cf1-protocol-hardening

## Luna Max 着手契約

pure/native hardeningは完了済み。残る実USB RX flood/TX backpressure acceptanceは `cf1-usb-flood-qualification` だけで実施する。親codeを再設計しない。

## 実行子issue

software/native hardeningは完了済み。最後の実USB scheduling acceptanceだけを [cf1-usb-flood-qualification](20260908-cf1-usb-flood-qualification.md) でzero-only検証する。

## 概要

CF1 が不正値や古い指令を受理せず、過剰なシリアル受信でも機体の制御周期を占有しないようにする。

## 背景

`firmware/stampfly/src/usb_bridge.cpp` は `sscanf` で SET を解析し clamp している。sequence は ACK に返すだけで比較しない。`usb_bridge_poll()` は 400 Hz の `loop_400Hz()` の先頭で呼ばれる。

## 問題

NaN は clamp を通過でき、余剰 token と不正 mode も明確に拒否されない。長すぎる行の末尾を別指令として読む可能性と、受信を無制限に drain して周期を遅らせる可能性がある。CLAIM 時に過去 SET の時刻が残る。

## 目標

不正入力は制御状態・有効 SET 時刻を一切更新しない。session 内で新しい指令だけが受理され、watchdog と ARM 前提がテストできる。

## 対象外

無線化、飛行用ビルド有効化、watchdog 時の着陸化、高度制御変更。

## 提案する方針

1. `usb_bridge.cpp` から Arduino 非依存の parser/state 部分を小さく切り出す。`uint32_t now_ms` と入力文字列を渡して結果を得る形にし、native C++ テストから実際のロジックを呼べるようにする。
2. SET は token 数を厳密に検証。sequence は uint32 の有効範囲、roll/pitch/yaw は有限で [-1,1]、throttle は有限で [0,1]、mode は 0/1 と 4/5 のみ。範囲外を clamp して受理せず明示的 ERR とする。既存ホストの正常 SET は互換を維持する。
3. CLAIM で sequence と SET 鮮度を初期化し、CLAIM 後に受理した SET がなければ ARM 不可とする。session 内は strictly increasing sequence、最大値到達時は disarm/reclaim を必須とする方式を初期仕様とし、wrap の曖昧な比較を避ける。duplicate/reordered 指令は heartbeat として数えない。
4. overlong line は改行まで捨てる状態を持ち、途中に `CF1 ARM` があっても実行しない。1 poll の受信処理量を定数で制限し、watchdog を poll 前後で確認する。応答送信が詰まっても制御周期を阻害しないよう、bounded queue または nonblocking TX を選び、その根拠を記録する。
5. DISARM は不正 SET の後でも実行可能にする。250 ms ルール、USB claim fencing、再接続で自動 ARM しない条件、safe PWM ゼロを維持する。`millis()` の wrap を unsigned 差分で検証する。
6. `host/stampfly.py` と README に拒否条件/sequence 上限を反映。必要な protocol capability を HELLO に互換性を保って追加する。識別不能な旧版で新制約を前提に運用しない。

## 受け入れ条件

- [x] NaN/Inf、余剰 token、不正 mode、範囲外、duplicate/stale sequence は ERR となり Stick/鮮度を更新しない。
- [x] CLAIM→ARM と RELEASE→CLAIM→ARM は新 SET なしで拒否される。
- [x] overlong 行の末尾の ARM は実行されず、次の正常行は復帰できる。
- [ ] 受信洪水と TX 停滞時にも watchdog 判定へ到達する。
- [x] native テストと camfly-safe ビルドが成功し、safe PWM のゼロ固定が保持される。

## テスト計画

`firmware/stampfly/tests/` に native parser/state テストを追加し実行コマンドを README に固定。時間 249/250/251 ms、時計 wrap、sequence 最大値、数千文字行、queue 満杯を含める。safe 実機で 20 Hz 正常通信と停止時の DISARM/claim 保持を確認。

## リスク

ERR 増加をホストが古い応答と誤対応しない設計が必要。400 Hz と USB TX の相互作用は native テストだけでは保証できない。

## 実装記録（2026-09-08）

`cf1_protocol.{hpp,cpp}` に Arduino 非依存 parser/session/LineCollector を実装し、SET の厳密 token/finite/range/mode/sequence 検証、CLAIM 時 freshness reset、uint32 sequence exhaustion、250 ms watchdog 境界、overlong discard-until-newline を native test で検証した。`usb_bridge.cpp` は 1 poll 96 byte の RX budget と `availableForWrite()` を使う bounded/nonblocking TX gate を持ち、watchdog を RX 処理の前後で評価する。

`camfly-safe` build と safe-hardware 600.011 秒 run が成功し、11,169 SET は全て zero、ARM=0、fault=0。

RX flood/TX 停滞時のコード経路は bounded だが、実 USB で queue-full/TX-stall を能動注入した測定はまだないため、その acceptance は未達として残す。

## 変更履歴

`CHANGES.md` impact: yes

項目案：CF1 が不正値や古い指令を受理せず、過剰なシリアル受信でも機体の制御周期を占有しないようにする。

実装時に既存の変更履歴規約を確認する。現時点では `CHANGES.md` は存在しないため、計画作成だけを理由に新設しない。

## 注記

実装担当の想定：`gpt-5.6-luna`、reasoning effort `max`。`Model` は本文の最終編集者を表すため、担当予定モデルとは区別する。作成環境から正確なモデル識別名を確認できないため `unknown`。実装着手時に自身の識別名へ更新する。

依存：なし。

全体計画：[20260908-flight-roadmap](20260908-flight-roadmap.md)。共通の実装手順、既存資産の保護、未確定条件を着手前に読む。
