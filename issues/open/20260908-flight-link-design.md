# 自由飛行用の Mac–StampFly 制御リンクを選定する

Status: open
Model: unknown
Created: 2026-09-08
Updated: 2026-09-08
Branch: codex/20260908-flight-link-design

## 概要

USB に依存しない制御通信の方式と所有権・欠損時動作を調査し、実装仕様を確定する。

## 背景

現在 Mac→StampFly は USB CF1。機体には既存 ESP-NOW receiver があるが、USB claim 中は `rc.cpp` がその入力を無視する。Mac の通常 Wi-Fi を ESP-NOW 送信機として使えるとは仮定できない。

## 問題

USB 接続は自由飛行の機械的制約になる。単に Wi-Fi socket を足すと既存 controller と競合し、古いパケットや再接続で飛行状態が復帰する可能性がある。

## 目標

保有機材・遅延実測・既存 firmware の制約に基づき1方式を選び、host adapter と firmware receiver を分割した後続課題を作る。

## 対象外

機材の購入、実飛行、未認証 network ARM、USB cable を付けたまま自由飛行扱いすること。

## 提案する方針

1. `rc.cpp`、`usb_bridge.cpp` の所有権・packet・callback 実行文脈を読む。使える外付け ESP32 USB↔ESP-NOW bridge があるかを記録する。機材不明なら購入を仮定せず必要仕様を示す。
2. 候補は Mac UDP/TCP→機体 Wi-Fi、Mac USB→ESP32 gateway→ESP-NOW を比較する。カメラ Wi-Fi との共存、通信遅延/欠損、実装量、追加機材、既存送信機との競合を表にする。データシート/API は選定時の一次資料で確認する。
3. 最小のモーター停止 prototype で20 Hz以上の送信とACKを測定し、p95/p99/max、欠損/重複/逆順/切断/再接続を記録する。機体の400 Hz loop への負荷も測る。
4. session ID、sequence、期限、単一 controller 所有権、auth/pairing、RELEASE、heartbeat、緊急停止を仕様化する。network session と既存 USB claim の優先関係を明文化し、通信復帰で ARM が復元しないようにする。
5. 無線側の遅延が既存250 ms watchdog に収まらない場合、無条件に watchdog を延長せず方式を再評価する。ACK と command freshness を分ける。gateway が古い SET を再生し続けない条件を含める。
6. `docs/flight-link-design.md` と、採用した host transport、receiver/gateway、safe 実機欠損試験の後続課題を作成する。採用を保留した場合は不足する測定や機材を具体化する。

## 受け入れ条件

- [ ] 採用方式と不採用理由が記録され、必要な機材と未所持のものが明確。
- [ ] 遅延と欠損の実測があり、250 ms期限との適合を評価している。
- [ ] 単一所有者、session再作成、古いpacket拒否、緊急停止の仕様がある。
- [ ] 後続イシューが確定した wire format/ファイル/異常系テストを含む。

## テスト計画

机上で packet fixture、遅延/重複/逆順 injector、gateway停止をテスト。実機は `camfly-safe` で比較測定し、既存 USB claim 中の ESP-NOW fencing が壊れないことを確認する。

## リスク

Wi-Fi と ESP-NOW のchannel制約や電波混雑は実測が必要。リンク切れ時に着陸できるかは機上 failsafe 設計に依存し、host側だけでは解決しない。

## 変更履歴

`CHANGES.md` impact: no

## 注記

実装担当の想定：`gpt-5.6-luna`、reasoning effort `max`。`Model` は本文の最終編集者を表すため、担当予定モデルとは区別する。作成環境から正確なモデル識別名を確認できないため `unknown`。実装着手時に自身の識別名へ更新する。

依存：[20260908-cf1-protocol-hardening](20260908-cf1-protocol-hardening.md)

全体計画：[20260908-flight-roadmap](20260908-flight-roadmap.md)。共通の実装手順、既存資産の保護、未確定条件を着手前に読む。
