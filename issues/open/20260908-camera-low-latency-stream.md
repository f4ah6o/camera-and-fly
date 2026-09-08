# Atom Cam の RTSP/WebRTC を低遅延観測入力として測定する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: coordinator
Luna-Ready: use-child-issues
Branch: main

## Luna Max 着手契約

この親issueでは直接実装しない。softwareは `camera-stream-decoder-adapter`、実機採否は `camera-stream-live-qualification` に分割済み。両方完了後に親acceptance/結論だけ更新する。

## 実行子issue

1. [decoded-frame adapter](20260908-camera-stream-decoder-adapter.md) — software-only。
2. [live stream qualification](20260908-camera-stream-live-qualification.md) — 1完了後の実機測定。

親acceptanceは2件の結果を統合した時だけ更新する。

## 概要

JPEG snapshot の実測遅延が約5.15秒で閉ループ制御に不適合だったため、Atom Cam 1 のローカル RTSP/WebRTC を decoded-frame 単位で比較測定し、視覚位置推定へ渡す入力方式を1つ選ぶ。

## 背景

`docs/camera-measurement.md` の2026-09-08実測では 1920×1080 JPEG の受信が約0.203 fps、request latency p95 約5.145秒だった。HTTP失敗はなかったが、撮影時刻も取得できないため JPEG は閉ループ用途から除外した。

既存 `host/atomcam.py` は RTSP URL を生成するだけで decoder は実装していない。WebRTC はカメラ側ローカルポートが存在するが、host側実装・時刻契約は未選定。

## 目標

RTSP と WebRTC を同じ測定契約で比較し、少なくとも1経路について bounded latest-frame input、decode時刻、queue制御、切断復帰を実測する。250 ms CF1 watchdogより遅い観測を新しい heartbeat で隠さない。

## 対象外

マーカー位置推定、ARM、非ゼロSET、PID調整、クラウド配信、atomcam2。

## 実装方針

1. 現在の `.venv` には `cv2` / `apriltag` / `pupil_apriltags` がないことを前提にし、decoderライブラリは一次資料とmacOS arm64対応を確認してから固定する。依存を推測で追加しない。
2. decoder worker は1スレッド/1プロセスの bounded latest-frame とし、受信queueを無制限にしない。古いframeをcatch-up処理せず破棄できる契約を持つ。
3. 各 frame に stream sequence、receive monotonic、decode-complete monotonic、利用できる場合のみsource PTS/DTSを記録する。PTSをwall clockや露光時刻と同一視しない。
4. RTSP main/sub と WebRTC について、decoded FPS、interarrival、decode latency、queue age、切断、再接続、CPU負荷を最低60秒ずつ測る。raw動画の保存はopt-inとする。
5. LED/表示タイマーを画角に入れ、別基準で exposure-to-display/receive の遅延を測定する。測定誤差を併記する。
6. 採用経路は position observer へ `latest observation` として渡し、stream reconnectだけで mission READY/ARMへ復帰しない。

## 受け入れ条件

- [ ] RTSP/WebRTCの候補、採用decoder、version、導入コマンドと不採用理由が記録されている。
- [ ] 60秒以上の実測で decoded FPS、p50/p95/p99/max、欠損/late、CPU負荷が記録されている。
- [ ] bounded latest-frame と切断/再接続 fixture があり、consumerがdecoder I/Oを待たない。
- [ ] exposure-to-receive の独立測定と誤差があり、閉ループ採否が明記されている。
- [ ] 採用経路が `20260908-vision-localization-design.md` の観測入力条件へリンクされている。

## テスト計画

合成/ローカルstream fixtureで遅延、burst、逆順相当、切断、再接続、decoder停止を再現する。実カメラ試験ではARMも非ゼロSETも送らない。

## リスク

container/codec bufferingによりネットワークが速くてもframe freshnessが悪化しうる。source PTSが露光時刻と同期している保証も別途必要。

## 変更履歴

`CHANGES.md` impact: yes

低遅延カメラ入力が実装された時点で記載する。

## 注記

依存：[20260908-camera-stream-measurement](20260908-camera-stream-measurement.md)

関連：[20260908-vision-localization-design](20260908-vision-localization-design.md)、[20260908-flight-roadmap](20260908-flight-roadmap.md)
