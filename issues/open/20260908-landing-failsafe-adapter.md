# LAND と機上failsafe adapterを状態機械として実装する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: implementation
Luna-Ready: blocked-on-altitude-and-telemetry
Branch: main

## Luna Max 着手契約

telemetry-validityとaltitude-command-api完了後に着手。pure LAND/fallback state + firmware/host adapter + testsを実装し、`camfly-safe`でwire pathまで確認。250ms watchdog変更とflight enableは含めない。

## 概要

通常LAND、sensor degradation、通信断、operator emergencyを区別するpure state machineとfirmware adapterを実装する。実装とflight buildでの有効化は分離する。

## 背景

既存 `auto_landing()` と `DISARM` は別動作であり、現250 ms watchdogは通信断でDISARMする。これを測定なしに自動着陸へ置換しない。

## 目標

`docs/altitude-failsafe-design.md` の遷移表をmachine-testableにし、valid telemetryがある場合だけLANDを進める。完全リンク断の機上fallbackはhost LANDとは別状態として扱う。

## 提案仕様

- semantic state：IDLE / LAND_REQUESTED / DESCENDING / GROUND_CONFIRM / COMPLETE / FALLBACK / EMERGENCY_STOP / FAULT。
- LAND action-idは冪等。同じIDの異内容は拒否する。
- altitude/range/IMU validityが必要条件。invalid時の動作は設定されたfallback capabilityがなければfail closed。
- reconnectでDESCENDINGやARM状態を自動復元しない。
- 現250 ms watchdogは、fallbackがsafe hardwareで明示的にqualifiedされるまで変更しない。

## 受け入れ条件

- [ ] `LAND 7` のduplicate再送で二重遷移せず、別内容のID衝突を拒否する。
- [ ] LAND中のToF invalid、IMU invalid、telemetry stale、host disconnectを個別fixtureで再現できる。
- [ ] grounded確認なしにCOMPLETE/DISARMへ正常着陸扱いで進まない。
- [ ] reconnect後にARM/LANDを自動再開しない。
- [ ] fallback実装を`camfly-safe`で検証でき、flight buildでの有効化は別commit/issueになる。
- [ ] operator emergencyとnormal LANDのログ理由が区別される。

## テスト計画

pure state/fake telemetryを先行し、motor-stop buildでwire/actionログを検証する。実モーター出力を伴う試験はflight qualificationのstage gate後のみ。

## リスク

有効な高度・姿勢情報なしの自動LANDは成立しない。通信断時にhostから新しい指令を送れる前提を置かない。

## 変更履歴

`CHANGES.md` impact: yes

## 注記

依存：[高度/failsafe設計](20260908-altitude-failsafe-design.md)、[telemetry validity](20260908-telemetry-validity.md)、[高度command API](20260908-altitude-command-api.md)
