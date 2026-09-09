# live vision pose を outer controller と bounded scheduler へ接続する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-09
Updated: 2026-09-09
Kind: implementation
Luna-Ready: blocked-on-vision-and-outer-controller
Branch: main

## Luna Max 着手契約

`20260908-vision-detector-calibration.md`、`20260908-vision-live-qualification.md`、`20260908-outer-position-controller.md` 完了後に着手する。主対象は host runtime composition と deterministic fake tests。実カメラ、実radio、ARM、flight build はこの issue で扱わない。

## 概要

bounded latest `PoseObservation` と mission target を outer position controller へ渡し、生成された expiring `ControlIntent` を既存 `host/control_loop.py` scheduler へ接続する runtime boundary を実装する。

## この issue だけでやること

- vision consumer、mission target、outer controller、scheduler の composition root を追加する。
- observation sequence / generation / freshness を controller 入力直前でも検証する。
- controller が intent を生成しない tick と、明示 zero intent を区別する。
- generated time / valid-until を scheduler が書き換えないことを固定する。
- invalid/stale pose、camera disconnect、detector restart、producer stall、mission FAULT で非ゼロ intent が継続しないことを deterministic test する。
- runtime metrics/log schema に observation age、controller decision、intent expiry、drop reason を記録する。

## 対象ファイル

主対象候補：`host/vision_control_runtime.py`、`host/tests/test_vision_control_runtime.py`。既存 `host/vision.py`、outer controller、`host/control_loop.py`、`host/mission.py` は必要最小限の adapter 変更のみ許可する。

## 対象外

- detector / calibration 自体の実装
- controller gain tuning
- ESP-NOW / USB transport implementation
- ARM / TAKEOFF / LAND wire action
- 実機カメラ acceptance
- flight enable

## 実装方針

runtime は wall-clock に依存しない injectable monotonic clock を使う。consumer は latest-value slot を読むだけで decoder I/O を待たない。pose が更新されない間に command heartbeat だけで同じ非ゼロ intent を延命しない。mission が HOLD 以外なら controller 出力 policy を明示し、未定義 state で推測指令を作らない。

## 受け入れ条件

- [ ] fresh valid pose + HOLD target からのみ outer controller を通した intent を生成する。
- [ ] stale/invalid pose、disconnect、generation change、sequence rollback で fail closed する。
- [ ] producer stall 後に最後の非ゼロ intent が expiry を越えて送られない。
- [ ] heartbeat / telemetry update が observation age や intent validity を更新しない。
- [ ] same input + fake clock から deterministic log が得られる。
- [ ] `.venv/bin/python -m unittest discover -s host/tests -v` が PASS する。

## テスト計画

synthetic `PoseObservation`、fake mission target、fake controller output、fake scheduler sink を使い、fresh→stale、disconnect→reconnect、generation rollover、producer stop、FAULT transition を table-driven で検証する。

## 依存

- `20260908-vision-detector-calibration.md`
- `20260908-vision-live-qualification.md`
- `20260908-outer-position-controller.md`
- `20260908-host-control-scheduler.md`
- `20260908-mission-supervisor.md`

## 注記

この issue の完了は実 transport、実カメラ、または飛行成功を意味しない。次は `20260909-camera-stampfly-closed-loop-safe-qualification.md` で全経路を `camfly-safe` zero-output 条件で評価する。
