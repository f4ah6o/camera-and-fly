# safe build と分離した flight build / preflight gate を実装する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08

## 概要

既定の `camfly-safe` を維持したまま、motor output を許可する flight build を明示的に分離し、プロペラ取り外し状態から始める preflight gate を実装する。

## 目標

誤操作や再接続で safe build から flight build へ切り替わらず、firmware/機体/transport/telemetry/camera/calibration の identity と capability を検査してからのみ flight adapter を有効化できるようにする。

## 対象外

実飛行の実施、物理安全設備の代替、自動 firmware 書き換え。

## 実装方針

- `camfly-safe` を既定 environment のまま保持し、flight environment は別名・別 capability にする。
- CLI に safe->flight 自動切替を作らない。flash は明示コマンドと対象 port 指定を必須とする。
- preflight は firmware version/capability、transport ownership、camera stream、calibration ID、telemetry validity、battery profile、grounded/armed state を検査する。
- 最初の実機検証はプロペラ取り外し状態で semantic action と output bounds を確認する。
- known-good safe build と factory backup への戻し方を文書化し、失敗時に自動再 ARM しない。

## 受け入れ条件

- [ ] safe build は引き続き全 motor PWM をゼロ固定する。
- [ ] flight build は別 environment/capability で、明示選択なしに生成・書込されない。
- [ ] preflight 欠落項目が1つでもあれば ARM/TAKEOFF を許可しない。
- [ ] no-propeller G3 で output bounds、abort、reconnect/no-rearm を記録する。
- [ ] known-good safe build / factory backup 復旧手順が実機で確認される。

## 依存

[20260908-flight-adapter-integration](20260908-flight-adapter-integration.md)、[20260908-flight-qualification](20260908-flight-qualification.md)
