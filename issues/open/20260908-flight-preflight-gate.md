# flight adapter 起動前の fail-closed preflight gate を実装する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: implementation
Luna-Ready: blocked-on-flight-build-environment
Branch: main

## 概要

firmware/transport/camera/calibration/telemetry/battery/grounded/armed の全前提を機械的に検証し、1項目でもunknown/invalidならARM/TAKEOFF pathを生成しないpreflight gateをhostへ実装する。

## この issue だけでやること

- immutable `PreflightSnapshot` / result contractを定義する。
- required capability/version/identityを明示列挙する。
- unknown bool、stale telemetry、calibration mismatch、wrong firmware、not-grounded、armedをfail closedにする。
- reconnect/process restart後は再preflight必須にする。
- fake fixture/table-driven testsを追加する。

## 対象ファイル

`host/preflight.py`、`host/tests/test_preflight.py`、必要最小限のintegration/mission adapter接続。

## 対象外

firmware flash、motor output、G3 physical test、battery thresholdの推測。

## 受け入れ条件

- [ ] required field/capabilityの欠落1件ごとに拒否テストがある。
- [ ] wrong safe/flight firmware identityを区別する。
- [ ] stale/unknown telemetryとcalibration mismatchを拒否する。
- [ ] reconnect後に過去のpass結果を再利用しない。
- [ ] host testsがPASSする。

## Luna Max 着手契約

実機条件の値を推測で埋めず、profile入力にする。software gateだけを完成させ、G3実機は別issueに残す。

## 依存

[20260908-flight-build-environment](20260908-flight-build-environment.md)、[20260908-flight-qualification](20260908-flight-qualification.md)
