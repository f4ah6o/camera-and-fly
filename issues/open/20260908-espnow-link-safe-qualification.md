# ESP-NOW gateway flight link を camfly-safe 実機で測定する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: hardware-validation
Luna-Ready: blocked-on-host-gateway-receiver
Branch: main

## 概要

明示識別した外付けESP32 gatewayとStampFlyを使い、zero-only `camfly-safe` でflight-linkのlatency/loss/ownership/watchdog marginを測定する。

## この issue だけでやること

- gateway/StampFlyのport identityをoperator確認し、自動探索しない。
- >=20 Hz、60秒以上、全SET zero、ARM 0で測定する。
- host-send→gateway→ESP-NOW→application ACK の p50/p95/p99/max と loss/duplicate/reorder を保存する。
- gateway disconnect/reset/reconnect、wrong session、receiver resetを注入する。
- 400 Hz loop timing と249/250/251ms watchdog境界を記録する。

## 対象ファイル

measurement probe/log summary/docs/parent issue。hardware identity/raw logsはtracked fileへ入れない。

## 対象外

非ゼロSET、ARM、watchdog延長、自由飛行。

## 受け入れ条件

- [ ] gateway/StampFly identityをwrite前に明示確認する。
- [ ] 60秒以上zero-onlyでARM 0をwire log確認する。
- [ ] ACK latency/loss/duplicate/reorderと400Hz loop負荷を保存する。
- [ ] disconnect/reset/reconnectで旧state復元がない。
- [ ] 250msに十分なmarginがない場合は方式をrejectし、watchdogを延長して合格扱いしない。

## Luna Max 着手契約

hardwareが未接続なら実施しない。成功を捏造せず blocker を残す。全制御値zero以外の試験を追加しない。

## 依存

[20260908-espnow-host-transport](20260908-espnow-host-transport.md)、[20260908-espnow-gateway-firmware](20260908-espnow-gateway-firmware.md)、[20260908-espnow-stampfly-receiver](20260908-espnow-stampfly-receiver.md)
