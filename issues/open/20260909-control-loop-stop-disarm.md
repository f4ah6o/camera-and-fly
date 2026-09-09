# P1-2: ControlLoop の正常停止で single-owner DISARM を明示する

Status: open
Model: Luna Max
Created: 2026-09-09
Updated: 2026-09-09
Kind: implementation
Luna-Ready: yes
Branch: main

## 概要

`host/control_loop.py` の正常停止経路を、最後の non-zero `SET` を残さない bounded な shutdown に修正する。single-owner の transport path から best-effort `DISARM` を一度だけ送り、完了後に `STOPPED` へ遷移させる。

## 背景

`ControlScheduler._latch_fault()` は `_disarm_sent` を使って best-effort DISARM を一度だけ送る。一方、現在の `ControlScheduler.stop()` は state を `STOPPED` にするだけで、`ControlLoop.stop(timeout=...)` は worker thread の join 成否にかかわらず scheduler を停止させる。

## 問題

non-zero `SET` の直後に正常 stop すると、receiver の watchdog / TTL expiration まで最後の command が残る可能性がある。また、worker がまだ transport を所有しているのに外側の stop 呼び出しが `STOPPED` を先に設定すると、transport owner thread と shutdown caller の競合境界が曖昧になる。fault path と normal stop の両方で DISARM を追加すると double-disarm にもなる。

## 目標

正常 stop、fault stop、repeated stop、join timeout を一つの明確な shutdown 契約にする。transport I/O の所有者を増やさず、shutdown が無期限にブロックしないことを証明する。

## Safety invariant

- non-zero `SET` の後の正常 stop は、single-owner transport path から best-effort `DISARM` を一度だけ試行してから `STOPPED` へ遷移する。
- fault path で既に DISARM 済みなら、後続の stop は追加の unsafe transport action を発行しない。
- stop 中に通常の non-zero `SET` を新たに送らない。
- worker thread が生存している間は、呼び出し側が完全停止済みとして `STOPPED` を先取りして扱わない。
- `stop(timeout=...)` は bounded で、worker が詰まっていても無期限に join 待ちしない。

## このissueだけでやること

- `ControlScheduler.stop()` と `ControlLoop.stop(timeout=...)` の normal shutdown ownership と順序を修正する。
- normal stop と `_latch_fault()` が共通の one-shot DISARM guard を使うようにする。
- worker join timeout 時に、未完了 shutdown と完全停止を区別できる既存または追加の契約をテストで固定する。
- 必須の fake transport / fake worker tests を追加・更新する。

## 対象ファイル

- `host/control_loop.py`
- `host/tests/test_control_loop.py`

## 提案する方針

stop 要求は worker に伝達し、transport owner が通常 tick を止めて best-effort DISARM を一度だけ実行した後に scheduler を `STOPPED` にする。join が timeout した場合は `STOPPED` を先取りせず、bounded return または明示的な未完了通知など、caller が完全停止と誤認しない契約を採用する。既存の fault latch と同じ disarm guard を再利用する。

## 対象外

- `FlightLink` の ACK reorder / reconnect state（P2-1/P2-2）
- firmware watchdog の期限や receiver 実装
- ARM 自動化、mission FSM、実機飛行試験
- scheduler の expiry semantics や mailbox の一般的な再設計

## 受け入れ条件

- [ ] non-zero `SET` を送った後に normal stop すると、fake transport の DISARM 呼び出しが exactly once になり、その後の scheduler state が `STOPPED` になる。
- [ ] DISARM の試行（成功・best-effort exception のいずれでも）が完了する前に `STOPPED` を公開しない。
- [ ] stop を繰り返しても DISARM、`SET`、その他の transport action が追加発行されず、idempotent である。
- [ ] fault path が先に DISARM した場合、後続の normal stop が double-disarm にならない。
- [ ] `stop(timeout=...)` が live worker の join timeout で bounded に戻り、worker 生存中に完全停止済みと判定できる `STOPPED` を先取りしない。
- [ ] live worker が終了した後は、stop 完了時に DISARM が exactly once であり、最終 state が `STOPPED` になる。
- [ ] stop 要求と tick の境界で non-zero `SET` が DISARM 後に送信されない。

## 必須tests

- `host/tests/test_control_loop.py` に `non-zero SET -> normal stop -> DISARM exactly once` を追加する。
- repeated stop の idempotency を追加する。
- `fault -> stop` で transport action が重複しないことを追加する。
- join timeout 中の live worker と、worker 終了後の stop 完了境界を fake worker / deterministic synchronization で追加する。
- transport owner thread 以外が DISARM を競合発行しないことを検証する。

## テスト計画

まず `.venv/bin/python -m unittest host.tests.test_control_loop -v` を実行し、次に `.venv/bin/python -m unittest discover -s host/tests -v` で scheduler 既存回帰を確認する。実時間 sleep に依存せず、fake transport と制御可能な worker / event を使う。

## dependencies

- 基盤契約：[20260908-host-control-scheduler](20260908-host-control-scheduler.md)
- transport interface の前提：[20260908-espnow-host-transport](20260908-espnow-host-transport.md)
- 実装上の外部 dependency：なし
- hardware dependency：なし

## リスク

shutdown を外側 thread から直接 transport に送ると single-owner invariant を壊す。逆に worker の終了待ちを無制限にすると host process が停止できないため、DISARM の所有者、state 遷移、timeout 時の可視性を分離して検証する。

## 変更履歴

`CHANGES.md` impact: yes。正常停止時の safety behavior が変わる可能性があるが、この issue 作成コミットでは `CHANGES.md` を変更しない。実装完了時に既存の変更履歴規約を確認する。

## Luna Max 着手契約

- 開始時に `git status --short --branch` と worker / transport ownership の現状を確認し、既存の未コミット変更を保持する。
- 変更範囲は原則として `host/control_loop.py` と `host/tests/test_control_loop.py` に限定する。
- `FlightLink`、firmware、実機接続、別 issue の ACK / reconnect 修正を同じ作業に含めない。
- timeout 時に何を返すかの API を変更する場合は、complete stop と未完了 stop の判定方法をテストと issue の実装記録に明記する。

## 注記

- 2026-09-09: review finding P1-2 を既存の host control scheduler 完了スコープとは分離した Luna-ready implementation issue として記録した。
