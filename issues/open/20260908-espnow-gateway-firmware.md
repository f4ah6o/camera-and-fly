# 外付けESP32 USB↔ESP-NOW gateway firmware を zero-only で実装する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: implementation
Luna-Ready: blocked-on-protocol-core
Branch: main

## 概要

Mac serial から versioned flight-link packet を受け、bounded latest-value で ESP-NOW へ転送する外付けESP32 gateway firmwareを実装する。hardware qualificationは別issue。

## この issue だけでやること

- gateway project/layout を repo 内で明示し build command を固定する。
- serial input をbounded parserで処理し stale SET を再送しない。
- gateway自身はsession/sequence/TTLを検証し、queueはlatest-value boundedにする。
- reset/reconnect後はclaimなし、ARMなし、旧SETなしから開始する。
- `camfly-safe` qualification前はzero-control試験用gateを既定にする。

## 対象ファイル

`firmware/gateway/`（新規）、gateway native/unit tests、README。StampFly receiver側は触らない。

## 対象外

StampFly `rc.cpp`接続、実aircraft ownership、実機latency測定、非ゼロSET/ARM。

## 受け入れ条件

- [ ] serial parser/queue/TTL がboundedである。
- [ ] stale/duplicate/wrong-sessionをESP-NOW送信しない。
- [ ] reset/reconnectで旧stateを復元しない。
- [ ] zero-only build/test profile が既定である。
- [ ] gateway build/unit tests が再現可能なコマンドでPASSする。

## Luna Max 着手契約

gateway hardware identityがなくてもsoftware実装/testは完了可能。port自動探索は実装しない。protocol coreをコピーせず共有schema/fixtureを再利用する。

## 依存

[20260908-espnow-flight-protocol-core](20260908-espnow-flight-protocol-core.md)
