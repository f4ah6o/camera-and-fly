# 外付けESP32 gateway経由のESP-NOW flight linkを実装・測定する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: coordinator
Luna-Ready: use-child-issues
Branch: main

## Luna Max 着手契約

直接3層を同時実装しない。順序は `espnow-flight-protocol-core` → 並行して `espnow-host-transport` / `espnow-gateway-firmware` / `espnow-stampfly-receiver` → `espnow-link-safe-qualification`。親は設計整合と最終採否だけ更新する。

## 実行子issue

1. [protocol core](20260908-espnow-flight-protocol-core.md) — 最初にpure wire/session contractを固定。
2. protocol core後、[host transport](20260908-espnow-host-transport.md)、[gateway firmware](20260908-espnow-gateway-firmware.md)、[StampFly receiver](20260908-espnow-stampfly-receiver.md) を独立セッションで実装。
3. 3層完了後、[safe qualification](20260908-espnow-link-safe-qualification.md) でzero-only実機測定。

この親issueへ3層の実装を直接混ぜない。

## 概要

Mac USB serial -> 外付けESP32 gateway -> ESP-NOW -> StampFly のzero-control transportをversioned session protocolとして実装し、`camfly-safe`で遅延・欠損・ownershipを測定する。

## 背景

`docs/flight-link-design.md` で現legacy ESP-NOW packetを調査した。USB claim fencingは存在するが、legacy packetにはsession/sequence/expiry/authがなく、そのまま自律flight transportには使えない。現在のtest setupではStampFly以外のUSB ESP32 gatewayは列挙されていない。

## 目標

実機gatewayを明示してから、Mac host adapter、gateway firmware、StampFly receiverの3層を分離して実装する。最初のhardware試験では全control値を0に固定しARMを公開しない。

## wire format要件

- protocol version + message kind。
- 128-bit相当以上のsession nonce/ID、または衝突/再利用を防げる同等設計。
- uint32 sequence、strictly increasing、wrap時はdisarmed re-session。
- command TTL/freshness。古いSETをgatewayが再送し続けない。
- `CLAIM`, `SET`, `ACK`, `RELEASE`, `DISARM`, `EMERGENCY_STOP`。
- ACKは RF delivery と application acceptance を区別する。
- finite/range/mode validationはCF1と同等以上。
- pairing/authenticationを明示し、checksumのみを認証としない。

例：

`SET(session, seq=41, ttl_ms=100, roll=0, pitch=0, yaw=0, throttle=0, control=ANGLE, alt=MANUAL)`

receiverは `(session, seq)` がfreshかつowner一致の場合だけ適用する。

## 実装境界

- `host/`：transport adapterとlatency probe。USB deviceを自動推測しない。
- gateway：Mac serialとESP-NOWのbridge。queueはbounded latest-value、stale SETをdrop。
- `firmware/stampfly/`：versioned receiver state。legacy RC/USB/networkの単一ownerを明示。
- `camfly-safe`ではmotor PWMゼロ、ARM messageを受理してもflight出力へ接続しないsafe試験モードを維持する。

## 受け入れ条件

- [ ] gateway hardware/serial identityを明示し、誤device時はwrite前に停止する。
- [ ] duplicate/reordered/stale/wrong-session/malformed packetがStick/freshnessを更新しない。
- [ ] USB claim中はnetwork ownerへ切り替わらず、release/handoff中にもdual-owner windowがない。
- [ ] gateway disconnect/reset/reconnectで以前のnon-zero SETやARMを復元しない。
- [ ] `camfly-safe`で>=20 Hzを60秒以上測定し、application ACK latency p50/p95/p99/max、loss/duplicate/reorderを記録する。
- [ ] 400 Hz loop負荷と249/250/251 ms watchdog境界を記録し、250 msに十分なmarginがない場合は不採用とする。
- [ ] ARM 0件・hardware SET全値0のwire logをsafe試験で確認する。

## テスト計画

pure packet/session test -> fake serial/gateway test -> native receiver test -> `camfly-safe` zero-only hardwareの順。gatewayが未接続の間はhardware acceptanceを成功扱いしない。

## リスク

ESP-NOW channel、干渉、gateway USB scheduling、gateway reset、aircraft radio負荷は実測依存。watchdogを無条件に延長して合格扱いしない。

## 変更履歴

`CHANGES.md` impact: yes

## 注記

依存：[flight-link design](20260908-flight-link-design.md)、[CF1 hardening](20260908-cf1-protocol-hardening.md)
