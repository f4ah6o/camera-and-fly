# ESP-NOW flight link の versioned packet/session core を pure C++/Python で実装する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: implementation
Luna-Ready: yes
Branch: main

## 概要

gateway/無線実機から独立して、flight link の packet codec、session ownership、sequence/TTL、ACK semantics を pure logic として固定する。

## この issue だけでやること

- protocol version/message kind/session ID/uint32 sequence/TTL を明示した wire schema を固定する。
- `CLAIM/SET/ACK/RELEASE/DISARM/EMERGENCY_STOP` を encode/decode する。
- duplicate/reordered/stale/wrong-session/malformed を state 更新前に拒否する。
- application ACK と RF delivery indication を別概念にする。
- reconnect/new session が disarmed/zero state から始まる pure state tests を追加する。

## 対象ファイル

`firmware/stampfly/src/flight_link_protocol.{hpp,cpp}`、`firmware/stampfly/tests/test_flight_link_protocol.cpp`、必要なら `host/flight_link_protocol.py` と host tests。`run_native_tests.sh` へ追加する。

## 対象外

gateway USB、ESP-NOW API、`rc.cpp`への実receiver接続、実機measurement、ARM enable。

## 受け入れ条件

- [ ] wire schema/version/capabilities が docs と code で一致する。
- [ ] malformed/duplicate/reorder/stale/wrong-session が state/freshness を更新しない。
- [ ] sequence exhaustion は disarmed re-session を要求する。
- [ ] reconnect/new session は以前のSET/ARMを復元しない。
- [ ] native/host tests がPASSする。

## Luna Max 着手契約

legacy 25-byte RC formatを変更して互換性を壊さない。実無線コードを同じcommitに入れない。pure core と test を完成させてコミットする。

## 依存

[20260908-espnow-gateway-flight-link](20260908-espnow-gateway-flight-link.md)、[20260908-cf1-protocol-hardening](20260908-cf1-protocol-hardening.md)
