# Mac host の ESP-NOW gateway transport adapter を実装する

Status: open
Model: GPT-5
Created: 2026-09-08
Updated: 2026-09-09
Kind: implementation
Luna-Ready: blocked-on-protocol-core
Branch: main

## 概要

外付けgatewayの明示serial portへ versioned flight-link packet を送受信するhost transport adapterを実装し、既存`ControlScheduler`/mission adapterから利用できるbounded interfaceを提供する。

## この issue だけでやること

- explicit portのみを開き、自動device探索をしない。
- session claim/release、SET、application ACK、DISARM/emergency APIを実装する。
- write/read timeout、partial line/frame、duplicate/reordered ACK、disconnectをboundedに処理する。
- stale intentを新しいheartbeatとして再生成しない。
- fake serial testsで reconnect/no-rearm を固定する。

## 対象ファイル

`host/flight_link.py`、`host/tests/test_flight_link.py`、必要最小限の`host/control_loop.py` interface。gateway firmware/StampFly firmwareは触らない。

## 対象外

実gateway測定、port自動探索、ARM自動化、非ゼロhardware SET。

## 受け入れ条件

- [x] explicit port/identity boundaryがある。
- [x] session/sequence/TTL/application ACKをprotocol coreと一致して扱う。
- [x] partial/duplicate/reorder/disconnectがbounded testでPASSする。
- [x] stale producerをtransport heartbeatで延命しない。
- [x] reconnectでclaim/ARM/old SETを自動復元しない。

## 実装記録（2026-09-09）

- `host/flight_link.py` に、明示serial portだけを開く version-1 binary
  transport、bounded partial-frame decoder、strict application ACK matching、
  RF-delivery ACKの分離、sequence exhaustion、DISARM/EMERGENCY_STOP、
  explicit new-session reconnectを追加した。
- `host/tests/test_flight_link.py` に fake serial を追加し、partial frame、
  CRC resynchronization、duplicate/reordered/foreign ACK、timeout、disconnect、
  sequence exhaustion、scheduler expiry、reconnect/no-rearmを検証した。
- `CHANGES.md` と `docs/flight-link-design.md` に software-only の範囲と
  hardware未接続の境界を追記した。
- `.venv/bin/python -m unittest host.tests.test_flight_link -v`、host全体、
  native protocol tests、`camfly-safe` buildがPASSした。実gateway測定、
  ESP-NOW接続、ARM/non-zero hardware SETは実施していない。

## Luna Max 着手契約

fake serialだけで完了可能。hardware測定を混ぜない。既存`ControlScheduler`のexpiry semanticsを変更しない。

## 依存

[20260908-espnow-flight-protocol-core](20260908-espnow-flight-protocol-core.md)
