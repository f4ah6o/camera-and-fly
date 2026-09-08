# 高度・ToF・IMU telemetry の validity と鮮度をCF1へ追加する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: implementation
Luna-Ready: yes
Branch: main

## Luna Max 着手契約

今すぐ単独着手可能。主対象はfirmware sensor freshness/validity state、CF1 STATUS/capability、`host/stampfly.py` parser、native/host tests。最後に`camfly-safe` zero-output build/logで確認し、flight policyは変更しない。

## 概要

保持された数値と「現在有効な観測」を区別できるよう、CF1 STATUS/capabilityへ高度・ToF・IMUのvalidityと鮮度を追加する。

## 背景

現STATUSは `Altitude` と `Range` を返すが、ToF invalid時に保持された値か、新しい測定かをhost側で区別できない。`Altitude2`、`Alt_flag`、`Range0flag` も現在のCF1 STATUS契約にはない。

## 問題

数値だけを受け取るhostは、保持値、stale値、未取得値、センサ更新失敗を同じ「有効な値」として扱う余地がある。再接続後に前回セッションの鮮度を持ち越すこともできない。

## 目標

センサ更新時刻・validity・sourceを明示し、mission/landing adapterがunknown/stale値を有効扱いしないようにする。

## 対象外

高度制御、failsafe閾値、landing adapter、センサドライバの交換、実飛行許可は扱わない。実機遷移ログは別途取得する。

## 提案仕様

- capability例：`telemetry_validity_v1`。
- STATUS例：`altitude_m=... altitude_valid=0|1 altitude_age_ms=... range_mm=... range_valid=0|1 range_age_ms=... imu_valid=0|1`。
- ageはfirmware monotonic基準でbounded整数とし、host wall clockと混同しない。
- invalid時も最後の数値をdebug用に返せるが、`*_valid=0`を優先する。
- age overflow/unknownは明示値またはvalid=falseで表現し、0msに丸めない。

## 提案する方針

`telemetry_contract::Validity` をfirmwareのセンサ更新とCF1 STATUSの間に置き、更新成功時だけfreshnessを進める。hostは `telemetry_validity_v1` capabilityと厳格なbool/age/source/unit検証を必須にし、無効値をfail closedする。

## 受け入れ条件

- [x] ToF失敗後に保持された `Range` が `range_valid=1` として出ない。
- [x] altitude/IMUのunknown・stale・invalidがmachine-readableに区別できる。
- [x] host parserはbool/age/unitの欠落・NaN・範囲外をfail closedする。
- [x] reconnect直後の未取得telemetryをfreshとして扱わない。
- [ ] fake clock/native testと`camfly-safe`実機ログでvalidity遷移を確認する。

## テスト計画

センサ更新/失敗/復帰、age境界、millis wrap、STATUS token欠落、不正boolをpure/fakeで検証する。実機ではプロペラ停止状態のみでToF遮蔽等を観測する。

## リスク

sensor driverが明示validityを露出していない箇所では、更新成功条件をソースに結び付けて定義する必要がある。

## 実装記録（2026-09-08）

- `firmware/stampfly/src/telemetry_validity.{hpp,cpp}` とセンサ更新経路に、ToF/IMU/高度のfreshness・invalid・millis wrap処理を追加した。
- `usb_bridge.cpp` と `host/stampfly.py` に `telemetry_validity_v1` と canonical STATUS fieldsを追加し、legacy `altitude`/`range` はdebug互換として残した。
- `firmware/stampfly/tests/test_telemetry_validity.cpp`、host parser tests、`camfly-safe` buildを実行してPASSした。実機ログは未取得のため、最後の受け入れ条件は未完了である。

## 変更履歴

`CHANGES.md` impact: yes

## 注記

依存：[高度/failsafe設計](20260908-altitude-failsafe-design.md)
