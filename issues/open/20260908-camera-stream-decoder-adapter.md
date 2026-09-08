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

## 受け入れ条件

- [ ] decoder backend/version/導入方法と選定理由が記録される。
- [ ] queue が bounded latest-value で、古いframeをcatch-up処理しない。
- [ ] receive/decode/source timestamps を混同しない schema がある。
- [ ] disconnect/reconnect/decoder停止/burst fixture が deterministic にPASSする。
- [ ] `.venv/bin/python -m unittest discover -s host/tests -v` がPASSする。

## Luna Max 着手契約

開始時に `git status --short --branch` と親 issue を確認する。実カメラidentityは探索しない。依存追加は lock/requirements の既存規約を確認して最小限に行う。完了時は変更ファイル、採用backend、テスト結果、live測定が未実施であることを issue に記録して1コミットにまとめる。

## 依存

[20260908-camera-low-latency-stream](20260908-camera-low-latency-stream.md)、[20260908-camera-stream-measurement](20260908-camera-stream-measurement.md)
