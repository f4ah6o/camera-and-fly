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

## 背景

既存のESP-NOW receiverはlegacy 25-byte RC packetであり、session ownership、expiry、再接続、application acceptanceを表現しない。gatewayや実無線APIを先に結ぶとwire semanticsの不備を実機で検証することになる。

## 問題

malformed、duplicate、reordered、wrong-session packetがfreshnessやsetpointを更新すると、無線遅延や再接続時に古い指令が残る。RF deliveryとapplication ACKも同じ成功として扱えない。

## 目標

versioned packet codecとreceiver-side session stateをC++/Pythonで同じbytes/semanticsとして固定し、実無線統合前にpure native/host testsで拒否境界とre-sessionを証明する。

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

## 提案する方針

little-endian `CF` frameにversion/kind/session/sequence/TTL/capability/payload/CRCを持たせる。`CLAIM`で新sessionを開始し、SETはstrict sequenceとreceiver-local TTLで受理する。ACKはAPPLICATIONとRF_DELIVERYを分け、legacy receiverとARM経路は変更しない。

## 受け入れ条件

- [x] wire schema/version/capabilities が docs と code で一致する。
- [x] malformed/duplicate/reorder/stale/wrong-session が state/freshness を更新しない。
- [x] sequence exhaustion は disarmed re-session を要求する。
- [x] reconnect/new session は以前のSET/ARMを復元しない。
- [x] native/host tests がPASSする。

## テスト計画

`firmware/stampfly/tests/test_flight_link_protocol.cpp` と
`host/tests/test_flight_link_protocol.py` でcodec bytes、CRC、SET/ACK payload、session ownership、TTL、duplicate/reorder/stale/wrong-session、sequence exhaustionを検証する。native test runnerへ登録し、host suiteと合わせて実行する。

## リスク

pure coreは認証、pairing、RF delivery、ESP-NOW channel、gateway lifecycleを提供しない。wire formatを実receiverへ接続する前に、認証とmotor-stopped latency/loss measurementを別issueで完了する必要がある。

## 変更履歴

`CHANGES.md` impact: yes。version-1 protocol core、Python mirror、docs、native/host testsを追加した。

## 実装記録（2026-09-08）

`firmware/stampfly/src/flight_link_protocol.{hpp,cpp}` と `host/flight_link_protocol.py` を追加した。legacy 25-byte RC path、ESP-NOW API、ARM enableは変更していない。native protocol runner、host test suite、`camfly-safe` buildがPASSした。

## Luna Max 着手契約

legacy 25-byte RC formatを変更して互換性を壊さない。実無線コードを同じcommitに入れない。pure core と test を完成させてコミットする。

## 依存

[20260908-espnow-gateway-flight-link](20260908-espnow-gateway-flight-link.md)、[20260908-cf1-protocol-hardening](20260908-cf1-protocol-hardening.md)

## 注記

このissueの完了は、無線flight transportの採用、ARM可能なfirmware、または実機飛行の完了を意味しない。
