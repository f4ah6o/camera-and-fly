# カメラフレームの鮮度・遅延・欠損を測定できる入力基盤を作る

Status: open
Model: unknown
Created: 2026-09-08
Updated: 2026-09-08
Branch: codex/20260908-camera-stream-measurement

## 概要

JPEG 入力を堅牢化し、実カメラの映像性能を記録して視覚制御に使えるか判断できるようにする。

## 背景

`host/atomcam.py` は JPEG 全体を無制限 read し、取得後の wall time を captured_at とする。これは撮影時刻ではない。RTSP は URL 生成だけで decoder はない。

## 問題

受信時刻だけで映像が新鮮とは判断できず、古い画像やキュー遅延を閉ループ制御へ渡す可能性がある。

## 目標

control loop を止めない bounded latest-frame 入力と、実測によるストリーム選択の根拠を残す。

## 対象外

マーカー位置推定、ARM/SET 送信、未評価 RTSP ライブラリの決め打ち。

## 提案する方針

1. `AtomCamFrame` に frame ID、request_started_monotonic、received_monotonic を追加し、既存 captured_at は互換性を保ちながら受信 wall time と文書化する。センサ撮影時刻が取得できない場合は unknown とする。
2. timeout/fps/最大 JPEG bytes を有限かつ正の値で検証。read は上限+1まで、無効 JPEG/切断/redirect を扱い、別 host への redirect を禁止する。base URL の userinfo/query/fragment を不用意にログへ出さない。IPv6 の RTSP URL を正しく構築する。
3. `host/camera_worker.py` に bounded latest-frame slot と stop を追加。HTTP 待ち/再試行は camera worker 内だけ。制御側は frame を待たず取得し、age/validity を判定できるようにする。再接続は上限付き backoff、過去 frame を新着扱いしない。
4. `host/camera_probe.py` で一定期間の受信 FPS、request latency p50/p95/p99/max、timeout率、byte数、間隔を JSON に保存。値は network/request latency と明記する。
5. 識別済み Atom Cam 1 で、静止時と機体相当の移動物体、通常照明/暗所、他のネットワーク負荷ありを測る。表示タイマーや LED の変化を別の基準で観測し、撮影〜受信の end-to-end latency を測る手順と誤差を `docs/camera-measurement.md` に記す。
6. JPEG が用途を満たさなければ RTSP/WebRTC の測定用 prototype を別調査として追加。ライブラリはその時点の一次資料と実機互換性を確認して固定する。画像の同一 hash は静止場面でも起きるので、それだけで freeze と断定しない。

## 受け入れ条件

- [ ] HTTP timeout/巨大 response/破損 JPEG はメモリ上限内で失敗し、control loop をブロックしない。
- [ ] request/receive/capture 時刻を混同せず、未知の撮影時刻が明示される。
- [ ] ローカル HTTP fixture で遅延・停止・redirect・再接続を再現できる。
- [ ] 実測レポートに試験時間、照明、解像度、遅延分位点、欠損率と採否判断がある。

## テスト計画

`host/tests/test_atomcam.py` と `test_camera_worker.py` でローカル HTTP server/fake clock を使用。実機では各条件を最低60秒測定し測定ファイルの場所を記録する。raw 画像は opt-in 保存とし tracked data に自動追加しない。

## リスク

HTTP cache の無効化だけでセンサ時点の鮮度は保証できない。映像周期が遅ければ20 Hz指令を出せても20 Hz観測ではない。

## 変更履歴

`CHANGES.md` impact: yes

項目案：JPEG 入力を堅牢化し、実カメラの映像性能を記録して視覚制御に使えるか判断できるようにする。

実装時に既存の変更履歴規約を確認する。現時点では `CHANGES.md` は存在しないため、計画作成だけを理由に新設しない。

## 注記

実装担当の想定：`gpt-5.6-luna`、reasoning effort `max`。`Model` は本文の最終編集者を表すため、担当予定モデルとは区別する。作成環境から正確なモデル識別名を確認できないため `unknown`。実装着手時に自身の識別名へ更新する。

依存：なし。

全体計画：[20260908-flight-roadmap](20260908-flight-roadmap.md)。共通の実装手順、既存資産の保護、未確定条件を着手前に読む。
