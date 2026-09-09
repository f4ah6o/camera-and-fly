# P2-1: reordered ACK で有効な matching ACK を捨てない

Status: open
Model: unknown
Created: 2026-09-09
Updated: 2026-09-09
Kind: implementation
Luna-Ready: yes
Branch: main

## 概要

`host/flight_link.py` の `_wait_for_application_ack` で、ACK packet sequence の monotonicity と target command の matching を分離する。packet-level reorder があっても、正しい `acknowledged_kind` / `acknowledged_sequence` / `ack_class` を持つ application ACK を timeout までに受け取った場合は捨てない。

## 背景

flight-link は RF delivery ACK と application ACK を別に扱い、session、packet sequence、acknowledged command sequence を検証する。受信 queue と decoder は bounded である必要があるため、重複・逆順・foreign packet を無制限に保持できない。

## 問題

現在は packet sequence が `_last_ack_sequence` 以下かを target matching より前に判定し、通過した packet で `_last_ack_sequence` を更新する。例えば無関係な ACK packet #11 の後に、現在の target に対する有効な ACK packet #10 が届くと、#10 が duplicate 扱いで捨てられ、host が timeout する。

## 目標

ACK の packet delivery 順序に左右されず target application ACK を正しく待ち受ける。一方で、wrong target、wrong session、wrong ACK class、duplicate は受理せず、bounded queue / seen-state の上限を維持する。

## Safety invariant

- foreign session、target と異なる `acknowledged_kind` / `acknowledged_sequence`、application ACK でない `ack_class` は、matching ACK として受理しない。
- packet sequence が高い無関係 ACK を先に見ても、後続の低い packet sequence にある正しい target application ACK を packet reorder だけで捨てない。
- duplicate ACK や wrong acknowledged sequence で別 command を false-accept しない。
- pending queue、unmatched queue、order/seen state は既存の bounded memory 契約を超えない。

## このissueだけでやること

- `_wait_for_application_ack` の packet sequence tracking と target matching の順序 / state を修正する。
- RF delivery ACK の記録と application ACK の完了判定を分離したままにする。
- reorder、duplicate、wrong sequence、foreign session、bounded queue/seen-state の fake serial tests を追加・更新する。

## 対象ファイル

- `host/flight_link.py`
- `host/tests/test_flight_link.py`

## 提案する方針

まず session、ACK class、acknowledged kind、acknowledged command sequence の意味を判定し、target matching に必要な packet を packet sequence の単調性だけで捨てない。重複検出や未一致 packet の保持は bounded な既存 queue / counter / limited state で実装し、application ACK を RF delivery ACK と混同しない。

## 対象外

- wire protocol の packet format、gateway firmware、receiver firmware の変更
- `ControlScheduler` の stop / DISARM 修正（P1-2）
- reconnect open failure の local state 修正（P2-2）
- 実 RF / hardware delivery の測定

## 受け入れ条件

- [x] 無関係な新しい ACK packet #11 が先に届き、その後に target の低い ACK packet #10 が届く場合、target application ACK を valid として返す。
- [x] RF delivery ACK が application ACK より先、または逆順で届いても、application ACK の待ち受け結果と `last_delivery_ack` の意味が壊れない。
- [x] 同一 ACK の duplicate は成功を二重計上せず、後続 command の ACK として誤受理されない。
- [x] `acknowledged_sequence` が target と異なる ACK は、packet sequence が新しくても target の成功にしない。
- [x] foreign session の ACK は無視し、target application ACK として受理しない。
- [x] bounded pending / unmatched queue と seen/order state の上限を維持し、大量 ACK でメモリが無制限に増えない。
- [x] 既存の timeout、strict target matching、sequence exhaustion、disconnect の regression がない。

## 必須tests

- higher unrelated ACK packet sequence before lower matching ACK
- RF delivery ACK reorder
- duplicate ACK
- wrong acknowledged sequence
- foreign session
- bounded queue / seen-state

上記は `host/tests/test_flight_link.py` の fake serial / deterministic clock で実装し、少なくとも一つは現在の target application ACK が packet reorder だけで timeout しないことを直接確認する。

## テスト計画

まず `.venv/bin/python -m unittest host.tests.test_flight_link -v` を実行し、次に `.venv/bin/python -m unittest discover -s host/tests -v` で host 全体を確認する。実 RF、serial device、無制限の実時間待ちには依存しない。

## dependencies

- wire semantics：[20260908-espnow-flight-protocol-core](20260908-espnow-flight-protocol-core.md)
- baseline transport：[20260908-espnow-host-transport](20260908-espnow-host-transport.md)
- 実装上の外部 dependency：なし
- hardware dependency：なし

## リスク

packet sequence の検証を緩めすぎると古い ACK や別 command の ACK を誤受理する。target matching を先に明示し、session / kind / acknowledged sequence / ACK class の全条件と bounded state を regression tests で固定する。

## 変更履歴

`CHANGES.md` impact: yes。実装内容を `CHANGES.md` の Unreleased に記録した。

## 実装記録（2026-09-09）

- `_wait_for_application_ack()` は ACK payload の target matching と packet sequence order を分離し、bounded seen/order state で duplicate だけを除外する。高い unrelated ACK の後の低い matching ACK を受理できる。
- application ACK 完了後に既に buffered な ACK を bounded に drain し、RF-delivery ACK が application ACK の前後どちらでも `last_delivery_ack` に記録される。同じ command の完了 ACK は bounded completed-target state で再利用しない。
- `host/tests/test_flight_link.py` に reorder、RF/application 両順序、duplicate、wrong target、foreign session、bounded state を追加した。
- `.venv/bin/python -m unittest host.tests.test_flight_link -v`、`.venv/bin/python -m unittest discover -s host/tests -v`、`git diff --check` が PASS。実 RF・hardware acceptance は実施していない。

## Luna Max 着手契約

- 開始時に `git status --short --branch`、対象 transport の current tests、既存の bounded limit を確認し、未コミット変更を保持する。
- 変更範囲は原則として `host/flight_link.py` と `host/tests/test_flight_link.py` に限定する。
- protocol format、gateway / receiver firmware、real RF qualification、P1/P2 の別 issue を変更しない。
- matching ACK を false-accept していないことと、bounded memory の証拠をテスト結果に残す。

## 注記

- 2026-09-09: review finding P2-1 を既存の host transport 完了スコープとは分離した Luna-ready implementation issue として記録した。
