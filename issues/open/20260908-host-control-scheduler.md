# ホストの周期送信と watchdog を映像・STATUS 待ちから分離する

Status: open
Model: unknown
Created: 2026-09-08
Updated: 2026-09-08
Branch: codex/20260908-host-control-scheduler

## 概要

制御指令の期限を送信前に確認し、I/O 待ちや古い制御結果の再送で watchdog を回避しない実行基盤を作る。

## 背景

`host/controller.py` と `host/stampfly.py` の CLI は送信直後に `watchdog_check()` を呼ぶ。SET が `_last_set_at` を更新するため、直前の長い停止を隠せる。STATUS 待ちと serial lock も同じ経路にある。

## 問題

camera request は既定2秒、firmware watchdog は250 ms。単純に heartbeat thread を足すだけでは古い非ゼロ指令を永続送信する危険がある。

## 目標

単一 serial 所有者が bounded I/O で通信し、指令生成時刻・送信時刻・受信確認を区別する。

## 対象外

ARM の自動化、位置制御、ネットワーク transport の実装。

## 提案する方針

1. `host/control_loop.py` に serial owner と latest-value mailbox を追加する。serial の read/write はこの所有者だけが行い、カメラや STATUS 呼び出し側は直接 lock を保持しない。
2. 指令は immutable `ControlIntent(sequence, generated_monotonic, valid_until_monotonic, roll, pitch, yaw, throttle, modes)` とする。heartbeat 再送で generated/valid_until を更新しない。producer の更新停止を transport の正常動作と区別する。
3. 送信前に前回 SET からの経過と intent 有効期限を確認。200 ms のローカル上限を超えたら best-effort DISARM と fault latch、通常 SET は送らない。新規 CLAIM 後の初回だけ別扱いとする。firmware の250 ms は最終防御として残す。
4. 通常20 Hz以上の tick とするが、遅延後に溜まった SET を連射せず最新値だけ使う。STATUS は低優先度とし request timeout 全体で制御経路を停止させない。ACK を継続 drain し、partial line を保持する bounded receive buffer を用意する。
5. `StampFly` に fake serial と monotonic clock を注入できるようにする。bool fields は必須の0/1として検証し、safe_test 欠落/不正や不明 mode を正常扱いしない。CLI の hz/duration/timeout に NaN/Inf を拒否する。
6. serial disconnect、短い write、write/read exception、ERR、queue overflow を fault として記録し、自動 reconnect/rearm しない。protocol event と Python 例外を二重報告しても状態遷移は一度にする。
7. 既存 controller と stampfly CLI を新 loop に寄せる。close/終了要求も owner を通す。thread が詰まった場合は待ち続けず異常終了し、firmware watchdog による停止を記録する。

## 受け入れ条件

- [ ] 201 ms の停止後、次の通常 SET を送る前に local watchdog が失敗する。
- [ ] producer が停止した時、送信 thread が生きていても期限を過ぎた非ゼロ指令を再送しない。
- [ ] 2秒のカメラ待ち、STATUS timeout、ログ出力停滞が古い指令の延命にならない。
- [ ] 欠損/不正 STATUS、部分行、大量 ACK、切断を bounded memory で処理できる。
- [ ] 既存の zero controller は ARM を送らず正常終了時と異常時の所有権仕様を維持する。

## テスト計画

`.venv/bin/python -m unittest discover -s host/tests -v`。fake clock による期限境界、fake serial による部分 ACK/遅延/short write/切断/ERR と終了競合を検証。実時間 sleep を中心にした脆いテストを避ける。B1 完了後に safe firmware と20 Hz統合試験。

## リスク

Python thread はハードリアルタイムではない。専用 thread を追加してもブロック中の OS/driver を止められるとは限らず、firmware watchdog の独立性が必要。

## 変更履歴

`CHANGES.md` impact: yes

項目案：制御指令の期限を送信前に確認し、I/O 待ちや古い制御結果の再送で watchdog を回避しない実行基盤を作る。

実装時に既存の変更履歴規約を確認する。現時点では `CHANGES.md` は存在しないため、計画作成だけを理由に新設しない。

## 注記

実装担当の想定：`gpt-5.6-luna`、reasoning effort `max`。`Model` は本文の最終編集者を表すため、担当予定モデルとは区別する。作成環境から正確なモデル識別名を確認できないため `unknown`。実装着手時に自身の識別名へ更新する。

依存：なし。

全体計画：[20260908-flight-roadmap](20260908-flight-roadmap.md)。共通の実装手順、既存資産の保護、未確定条件を着手前に読む。
