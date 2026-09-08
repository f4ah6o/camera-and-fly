# metre単位の高度・TAKEOFF指令APIを追加する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Branch: main

## 概要

既存CF1 throttleの意味を変更せず、明示的なTAKEOFFとmetre単位高度目標をversioned capabilityとして追加する。

## 背景

`docs/altitude-failsafe-design.md` のソース確認により、AUTO_ALTは `Alt_ref += thlo * 0.001` であり、現CF1 throttle `[0,1]` では降下方向の高度rateを表現できない。旧SETを高度mとして再解釈してはいけない。

## 目標

pure parser/stateから実装し、duplicate/reordered/expired commandで高度目標やaction stateを更新しないAPIを作る。既定ビルドは引き続き `camfly-safe` とする。

## 提案仕様

- capability例：`altitude_target_v1`、`takeoff_v1`。
- wire example（最終spellingは実装時に固定）：`CF1 TAKEOFF <action-id> <target-m>`、`CF1 ALT <sequence> <target-m>`。
- `target-m` は finite metre、範囲はソース上限だけで決めず実測flight profileから設定可能にする。
- TAKEOFFはaction-idで冪等、ALTはsession内strict sequenceと期限を持つ。
- CLAIM/reconnect/reset後は以前のTAKEOFF/ALTを復元しない。
- capability未対応firmwareへ新commandを送らない。

## 受け入れ条件

- [ ] 旧 `CF1 SET` の意味と互換性が変わらない。
- [ ] NaN/Inf/範囲外/duplicate/stale/unknown capabilityで目標状態を更新しない。
- [ ] `TAKEOFF 42 0.30` の同一action-id再送は同じactionとして扱い、異内容で同じIDは拒否する。
- [ ] `ALT` の単位がmetreで、hostとfirmwareの双方にfinite/range/rate-limit検証がある。
- [ ] CLAIM/reconnect後は新しい明示START/TAKEOFFなしに高度制御を再開しない。
- [ ] native testと`camfly-safe` buildが成功し、PWMゼロ固定を維持する。

## テスト計画

pure protocol/stateテストでaction-id衝突、sequence逆順、期限、時計wrap、capability欠落、0/境界/範囲外高度を検証する。実機はまず`camfly-safe`のみ。

## リスク

既存高度制御器の性能はAPI追加だけでは保証されない。target range/rateをソース定数だけで安全値とみなさない。

## 変更履歴

`CHANGES.md` impact: yes

## 注記

依存：[高度/failsafe設計](20260908-altitude-failsafe-design.md)、[telemetry validity](20260908-telemetry-validity.md)
