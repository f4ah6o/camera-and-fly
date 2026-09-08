# 高度指令・着陸・故障時動作の機上仕様を確定する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: completed-scope
Luna-Ready: no-direct-work
Branch: codex/20260908-altitude-failsafe-design

## Luna Max 着手契約

設計・子issue分割は完了済み。このファイルを直接再実装しない。残作業は `telemetry-validity` → `altitude-command-api` → `landing-failsafe-adapter` の子issueで行い、親のsource-derived契約を変更する場合だけ更新する。

## 概要

既存の高度制御とモード遷移を調査し、明示的な離陸/高度/着陸指令と故障別動作の後続実装仕様を作る。

## 背景

`flight_control.cpp:get_command()` の AUTO_ALT は `Alt_ref += thlo * 0.001` を実行する。現 CF1 throttle は [0,1] で負値を送れず、m単位の高度指令ではない。`usb_bridge.cpp` のwatchdogはDISARM/PARKINGを優先し、既存auto_landingとは異なる。

## 問題

ゼロ throttle を高度ゼロ、DISARM を着陸と解釈できない。観測不能と通信不能と姿勢異常では成立する対処が異なり、空中での即時モーター停止は落下につながる。

## 目標

単位・有効センサ・状態・開始条件・停止条件を明文化し、既存安定化ループを保って実装できる仕様にする。

## 対象外

この課題でのwatchdog挙動変更、飛行用ビルド、ゲイン調整、未検証の自動着陸を既定化すること。

## 提案する方針

1. `get_command`、`auto_landing`、`judge_mode_change`、`sensor.cpp`、`tof.cpp`、`alt_kalman.cpp` を追い、Altitude/Altitude2/Range、Alt_ref、unit、validity、更新周期、battery判定、ARM button処理を表にする。
2. host送信値→Stick→angle/altitude reference の変換を数値例で説明する。roll/pitch は正規化 stick と rad、STATUSはdegであることを確認し、yaw が角度目標かrate入力かを明記する。
3. 新しい高度目標/TAKEOFF/LANDの protocol 方式を選定する。version/capability、単位、範囲、rate limit、再送冪等性、着地判定、timeout、claim維持、STATE/armedの整合を定義。旧CF1のthrottle意味を黙って変えない。
4. 故障表は camera stale、pose低品質、command期限切れ、host停止、radio断、ToF無効、IMU異常、低電圧、operator emergency、境界逸脱を含む。LAND要求がhostから届く場合と完全リンク断を分ける。
5. センサと姿勢制御が正常なら機上着陸を検討し、正常性を確認できない場合の最後の停止手段を区別する。既存250 ms hard DISARM変更は独立した後続課題にし、safe実機の証拠なしで有効化しない。
6. `docs/altitude-failsafe-design.md` に状態遷移表を作り、pure state試験・PWM停止ビルドでの検証方法を定める。「高度API」「telemetry validity」「landing/failsafe」の後続イシューを作成する。

## 受け入れ条件

- [x] すべての制御/観測量の単位・符号・鮮度・有効条件をソースに結び付けて記録。
- [x] LANDとDISARMが区別され、再送/切断/センサ故障の遷移表がある。
- [x] 機上fallbackの実装と有効化を分け、既存safe動作を維持する手順がある。
- [x] 後続実装イシューが protocol examples と異常系の受け入れ条件を含む。

## 調査結果（2026-09-08）

`docs/altitude-failsafe-design.md` に source-derived unit/符号/validity、LANDとDISARMの分離、通信・camera・ToF・IMU・battery・bounds・emergencyの遷移表を記録した。現CF1 throttle `[0,1]` は AUTO_ALT の `Alt_ref += thlo * 0.001` に対して負方向を表現できず、metre高度APIではない。yawもabsolute headingではなくrate referenceへ変換される。

後続実装を [高度command API](20260908-altitude-command-api.md)、[telemetry validity](20260908-telemetry-validity.md)、[LAND/failsafe adapter](20260908-landing-failsafe-adapter.md) に分割した。いずれも `camfly-safe` で実装と有効化を分離する。

## テスト計画

ソース経路を照合し、0/0.2/1 throttle と mode4/5 の計算を机上検証。fake telemetry とclockで状態表を通す。プロペラ停止のsafe実機でセンサ値/validityを比較する。

## リスク

地面効果、ToF視野/床材、低電圧で自動着陸が失敗する可能性がある。実機の有効範囲を測らず高度閾値を決めない。

## 変更履歴

`CHANGES.md` impact: no

## 注記

実装担当の想定：`gpt-5.6-luna`、reasoning effort `max`。`Model` は本文の最終編集者を表すため、担当予定モデルとは区別する。作成環境から正確なモデル識別名を確認できないため `unknown`。実装着手時に自身の識別名へ更新する。

依存：[20260908-cf1-protocol-hardening](20260908-cf1-protocol-hardening.md)

全体計画：[20260908-flight-roadmap](20260908-flight-roadmap.md)。共通の実装手順、既存資産の保護、未確定条件を着手前に読む。
