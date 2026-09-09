# P2-2: reconnect の open failure で stale local state を残さない

Status: open
Model: unknown
Created: 2026-09-09
Updated: 2026-09-09
Kind: implementation
Luna-Ready: yes
Branch: main

## 概要

`host/flight_link.py` の live serial `reconnect()` を、再 open に失敗しても stale な claimed / setpoint state を残さない bounded fail-closed 処理にする。成功時は新しい serial/session を claim し、旧 SET・session・ARM state を復元しない。

## 背景

`FlightLink.reconnect()` は non-injected live serial では old serial を close してから `_open_serial()` を呼ぶ。現在は `_open_serial()` が失敗すると、後続の `_faulted = False`、`_reset_session()`、fresh claim に到達しない。

## 問題

正常に claimed で setpoint を保持している接続から reconnect を試し、新 serial の open に失敗した場合、closed endpoint の裏で `claimed=True`、`requires_reconnect=False`、setpoint / session などの stale local state が一時的に残る可能性がある。次の wire write は fail-closed でも、上位 health/status 判定が不正確になる。

## 目標

reconnect の open failure を上位が明確に disconnected/faulted と判定できる state にする。成功した retry だけが fresh session を claim でき、以前の SET / ARM / session を復元しない。

## Safety invariant

- failed reconnect 後は `claimed=False`。
- failed reconnect 後は `has_setpoint=False`。
- failed reconnect 後は `armed=False`。
- failed reconnect 後は `requires_reconnect=True`。
- failed reconnect 後に old SET、old session、old ARM state を復元しない。
- open failure は bounded に例外を返し、closed endpoint を claimed として利用可能に見せない。
- successful reconnect は明示的な fresh claim だけを行い、自動 SET / ARM / mission resume を行わない。

## このissueだけでやること

- live serial reconnect の open / swap / failure handling を修正する。new serial を先に open して atomic に swap する方式、または open failure 時に必ず disconnected/faulted state へ遷移する方式のいずれかを採用する。
- failed reconnect の local invariants と retry semantics を fake serial tests で固定する。
- injected serial reconnect の既存回帰を維持する。

## 対象ファイル

- `host/flight_link.py`
- `host/tests/test_flight_link.py`

## 提案する方針

実装方式は atomic swap（A）または open failure 時の明示的 fail-closed 遷移（B）でよい。ただし、失敗を caller に返す時点で local state を reset し、`requires_reconnect=True` とする。後続 retry で open と fresh CLAIM が成功した場合だけ `claimed=True` に戻し、旧 session / setpoint を持ち越さない。

## 対象外

- ACK packet reorder の判定（P2-1）
- wire protocol の変更、gateway / receiver firmware の変更
- port 自動探索、暗黙の reconnect、automatic re-arm
- 実 serial device の接続試験、実 RF / hardware qualification

## 受け入れ条件

- [x] previously claimed の live connection に対する successful reconnect が新しい endpoint と fresh session を使い、new CLAIM 以外の旧 SET / ARM state を送らない。
- [x] previously claimed の live connection で `_open_serial()` が失敗した場合、`FlightLinkDisconnected` 相当の bounded error を返す。
- [x] open failure の直後に `claimed=False`、`has_setpoint=False`、`armed=False`、`requires_reconnect=True` が同時に成立する。
- [x] open failure の直後に旧 `session_id`、setpoint、last-set freshness を再利用しない。
- [x] failed reconnect の後、次の retry で open と fresh CLAIM が成功すれば利用可能になるが、旧 state は復元されない。
- [x] injected serial reconnect の既存 behavior、closed injected endpoint の要求、no-rearm regression が維持される。
- [x] failed open 後に closed / stale endpoint への通常 SET が発行されない。

## 必須tests

- successful live reconnect
- previously claimed connection からの open failure
- failed reconnect の後の retry
- injected serial reconnect regression

テストは `_open_serial()` または pyserial 境界を deterministic に差し替え、old endpoint の close、fresh endpoint の CLAIM、state invariants、旧 SET 非復元を観測できる fake serial で行う。

## テスト計画

まず `.venv/bin/python -m unittest host.tests.test_flight_link -v` を実行し、次に `.venv/bin/python -m unittest discover -s host/tests -v` で host 全体を確認する。実 serial port、実 RF、無期限 retry は使用しない。

## dependencies

- baseline transport：[20260908-espnow-host-transport](20260908-espnow-host-transport.md)
- session / reconnect semantics：[20260908-espnow-flight-protocol-core](20260908-espnow-flight-protocol-core.md)
- 実装上の外部 dependency：なし
- hardware dependency：なし

## リスク

open failure の例外処理だけを追加して local state の reset を後回しにすると、上位が stale claimed state を観測する。失敗経路の直後に4つの state invariantと旧 session/setpoint 非復元を同じテストで確認し、成功経路とは分離して検証する。

## 変更履歴

`CHANGES.md` impact: yes。実装内容を `CHANGES.md` の Unreleased に記録した。

## 実装記録（2026-09-09）

- fail-closed 方式（B）を採用した。live serial の close 後に reopen が失敗した場合、session、claim、setpoint、freshness、decoder/ACK state を直ちに reset し、`requires_reconnect=True` の disconnected error を返す。
- retry の open 成功後は旧 state を復元せず、新 endpoint へ fresh session の CLAIM だけを送る。injected endpoint と closed endpoint の既存契約も維持した。
- `host/tests/test_flight_link.py` に successful live reconnect、open failure、failed retry、injected reconnect の fake serial coverage を追加した。
- `.venv/bin/python -m unittest host.tests.test_flight_link -v`、`.venv/bin/python -m unittest discover -s host/tests -v`、`git diff --check` が PASS。実 serial・実 RF・hardware acceptance は実施していない。

## Luna Max 着手契約

- 開始時に `git status --short --branch` と current `FlightLink` reconnect tests を確認し、既存の未コミット変更を保持する。
- 変更範囲は原則として `host/flight_link.py` と `host/tests/test_flight_link.py` に限定する。
- protocol format、firmware、automatic reconnect / re-arm、実機接続を変更しない。
- A/B の採用方式、open failure 直後の state、retry 後の fresh session を実装記録と deterministic test 結果に明記する。

## 注記

- 2026-09-09: review finding P2-2 を既存の host transport 完了スコープとは分離した Luna-ready implementation issue として記録した。
