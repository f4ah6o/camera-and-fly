# camfly-safe と分離した flight build environment を定義する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: implementation
Luna-Ready: blocked-on-flight-adapter
Branch: main

## 概要

`camfly-safe` を既定のまま保持し、motor-capable firmware を別 environment/capability として明示定義する。flashや実機enableはこのissueでは行わない。

## この issue だけでやること

- PlatformIOに別名flight environmentを追加し、safe environmentのPWM zero contractを変更しない。
- build capability/versionをSTATUS/HELLO等で識別可能にする。
- flight envの生成は明示環境名指定を必須とし、default/allから誤生成されない構成を確認する。
- build-only tests/docsを追加する。

## 対象ファイル

`firmware/stampfly/platformio.ini`、build flags/capability code、README/tests。

## 対象外

flight firmware upload、ARM、motor run、preflight UI、G3/G4/G5。

## 受け入れ条件

- [ ] `camfly-safe`は全PWM zero gateを維持する。
- [ ] flight envは別名/capabilityで明示選択が必要。
- [ ] safe buildとflight buildを機械的に識別できる。
- [ ] safe buildの既存testsがPASSする。
- [ ] このissueでは実機へflight buildを書き込まない。

## Luna Max 着手契約

motor-capable codeが既存にない場合も、推測でcontrol semanticsを作らずadapter dependencyに従う。commitはbuild separationだけに限定する。

## 依存

[20260908-flight-adapter-integration](20260908-flight-adapter-integration.md)
