# mission semantic action と実機 flight adapter を統合する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08

## 概要

`host/mission.py` の ARM/TAKEOFF/LAND/safe-recovery semantic action を、versioned capability を持つ実 transport/altitude/landing adapter へ接続する。

## 目標

action ID の冪等性、telemetry validity、link ownership、grounded feedback を保ったまま実機 adapter を構成し、未対応 capability では起動前に fail closed する。

## 対象外

この課題単独での motor enable、飛行試験、LAND を DISARM に置換する実装。

## 実装方針

- adapter は explicit capability negotiation 後だけ生成する。
- ARM/TAKEOFF/LAND は action ID を保存し、duplicate/reordered ACK で再実行しない。
- altitude/ToF/IMU/link validity と age を `HealthSnapshot` に正規化する。
- reconnect/process restart/frame recovery から自動 ARM/再開しない。
- safe recovery と emergency stop を別 API に保ち、LAND prerequisites 不成立時は推測動作をしない。
- fake transport と `camfly-safe` で semantic wire path を検証した後に flight build へ渡す。

## 受け入れ条件

- [ ] capability 欠落・unknown telemetry・stale telemetry で起動/進行を拒否する。
- [ ] duplicate action ID、ACK reorder、reconnect で ARM/TAKEOFF/LAND を重複実行しない。
- [ ] LAND completion は grounded && !armed の実 feedback を必須とする。
- [ ] safe recovery と emergency stop の failure path がテストされる。
- [ ] `camfly-safe` 検証中は ARM/非ゼロ motor output が発生しない。

## 依存

[20260908-mission-supervisor](20260908-mission-supervisor.md)、[20260908-altitude-command-api](20260908-altitude-command-api.md)、[20260908-telemetry-validity](20260908-telemetry-validity.md)、[20260908-landing-failsafe-adapter](20260908-landing-failsafe-adapter.md)、flight-link 実装
