# RTSP/WebRTC decoded-frame adapter を bounded latest-frame として実装する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: implementation
Luna-Ready: yes
Branch: main

## 概要

Atom Cam 1 の RTSP/WebRTC を比較測定できるよう、decoder backend を差し替え可能な bounded latest-frame adapter と probe を host 側へ実装する。実カメラの採否測定は別 issue とする。

## 背景

既存のJPEG helperは受信時刻付きのsnapshot取得を扱うが、RTSP/WebRTCのdecoded frame、decoder reconnect、source timestampを表す契約がない。consumerがdecoder I/Oや未bounded queueに引き込まれると、遅延した古いframeをcatch-upする。

## 問題

frameのreceive time、decode-complete time、source PTS/DTSを一つのtimestampへ潰すと、freshness判定とsource遅延測定を誤る。disconnect後に直前frameをvalidとして残すことも避ける必要がある。

## 目標

FFmpeg CLIを第一候補として、decoder I/Oをworkerへ隔離し、consumerには一つの最新decoded frameと明示的なtimestamp/connection状態だけを返す。synthetic probeでburst・再接続・停止を再現できるようにする。

## この issue だけでやること

- decoder backend の一次資料/macOS arm64 対応を確認し、1つ以上の実装候補を固定する。
- `host/` に decoded-frame contract、worker、bounded latest-value slot、probe を実装する。
- source PTS/DTS、receive monotonic、decode-complete monotonic を区別する。
- burst/queue backlog/decoder stop/disconnect/reconnect を synthetic/local fixture で再現する。
- consumer が decoder I/O を直接待たないことをテストする。

## 対象ファイル

主対象：`host/camera_stream.py`、`host/camera_stream_probe.py`、`host/tests/test_camera_stream.py`、`README.md`。
既存 `host/camera_worker.py` / `host/atomcam.py` は契約再利用のための最小変更のみ許可する。

## 対象外

実カメラ60秒測定、露光遅延測定、marker detector、ARM/非ゼロSET、mission自動復帰。

## 提案する方針

`DecodedFrame` はreceive/decode-complete monotonic clockとsource PTS/DTSを別フィールドで持つ。`LatestDecodedFrameSlot` は常に最新値だけを保持し、decoder世代の切り替えとdisconnectでvalidityを落とす。FFmpeg rawvideo pipeにsource timestamp side-channelがない場合はunknownのまま出力する。

## 受け入れ条件

- [x] decoder backend/version/導入方法と選定理由が記録される。
- [x] queue が bounded latest-value で、古いframeをcatch-up処理しない。
- [x] receive/decode/source timestamps を混同しない schema がある。
- [x] disconnect/reconnect/decoder停止/burst fixture が deterministic にPASSする。
- [x] `.venv/bin/python -m unittest discover -s host/tests -v` がPASSする。

## テスト計画

`host/tests/test_camera_stream.py` でslot overwrite、disconnect/reconnect、worker retry、timestamp separationを検証する。probeはsyntheticを既定とし、FFmpeg live probeは明示URLとdurationを要求する。

## リスク

FFmpeg rawvideo出力だけではsource PTS/DTSを復元できないため、source timestampが必要な測定には別demuxer backendが必要になる。live probeの結果はmeasurement-onlyであり、flight evidenceではない。

## 変更履歴

`CHANGES.md` impact: yes。FFmpeg/synthetic decoded-frame adapterとprobeを追加した。

## 実装記録（2026-09-08）

`host/camera_stream.py`、`host/camera_stream_probe.py`、`host/tests/test_camera_stream.py` を追加した。選定backendはFFmpeg CLI（macOS arm64での導入は `brew install ffmpeg`、開発環境の観測versionは9.0.1）である。synthetic fixtureとhost test suiteはPASSした。実カメラの採否・60秒測定は対象外のため未実施である。

## Luna Max 着手契約

開始時に `git status --short --branch` と親 issue を確認する。実カメラidentityは探索しない。依存追加は lock/requirements の既存規約を確認して最小限に行う。完了時は変更ファイル、採用backend、テスト結果、live測定が未実施であることを issue に記録して1コミットにまとめる。

## 依存

[20260908-camera-low-latency-stream](20260908-camera-low-latency-stream.md)、[20260908-camera-stream-measurement](20260908-camera-stream-measurement.md)

## 注記

decoder adapterの完了はRTSP/WebRTCの実カメラ性能合格、低遅延要件、または飛行許可を意味しない。
