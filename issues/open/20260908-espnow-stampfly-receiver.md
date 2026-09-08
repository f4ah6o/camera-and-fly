# StampFly に versioned ESP-NOW flight-link receiver と ownership fence を実装する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: implementation
Luna-Ready: blocked-on-protocol-core
Branch: main

## 概要

pure protocol core を StampFly の ESP-NOW receive pathへ接続し、legacy RC / USB CF1 / network flight session の単一ownerを fail-closed に実装する。

## この issue だけでやること

- versioned packetを`Stick`更新前に完全検証する。
- USB claim中はnetwork ownerへ移行しない。
- explicit handoff/releaseが完了するまでdual-owner windowを作らない。
- disconnect/session expiry/resetでzero/disarmedへ戻し、旧non-zero SETを復元しない。
- `camfly-safe`ではPWM zero gateを維持しARMをflight outputへ接続しない。

## 対象ファイル

`firmware/stampfly/src/rc.cpp` と新規 receiver state files、native tests。gateway firmware/host adapterは触らない。

## 対象外

実gateway測定、pairing UI、flight build motor enable、watchdog延長。

## 受け入れ条件

- [ ] invalid packetはStick/freshness/peer/ownerを更新しない。
- [ ] USB claim/network/legacyのowner transitionがtable-driven native testでPASSする。
- [ ] disconnect/reset/reconnectでrearm/old SET復元がない。
- [ ] `bash firmware/stampfly/tests/run_native_tests.sh` がPASSする。
- [ ] `.venv/bin/pio run -d firmware/stampfly -e camfly-safe` がSUCCESSする。

## Luna Max 着手契約

legacy RC parser hardeningを保持する。motor output enableは絶対に含めない。owner state と native tests を同じcommitで完成させる。

## 依存

[20260908-espnow-flight-protocol-core](20260908-espnow-flight-protocol-core.md)
