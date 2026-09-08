# Mac host の ESP-NOW gateway transport adapter を実装する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
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

- [ ] explicit port/identity boundaryがある。
- [ ] session/sequence/TTL/application ACKをprotocol coreと一致して扱う。
- [ ] partial/duplicate/reorder/disconnectがbounded testでPASSする。
- [ ] stale producerをtransport heartbeatで延命しない。
- [ ] reconnectでclaim/ARM/old SETを自動復元しない。

## Luna Max 着手契約

fake serialだけで完了可能。hardware測定を混ぜない。既存`ControlScheduler`のexpiry semanticsを変更しない。

## 依存

[20260908-espnow-flight-protocol-core](20260908-espnow-flight-protocol-core.md)
