# 遅延・欠損付きの簡易飛行 dynamics simulator を追加する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08

## 概要

G1 qualification 用に、外側位置制御器を実機なしで評価できる決定論的な簡易 dynamics と delay/loss injection を実装する。

## 目標

位置・速度・yaw・高度を持つ最小 plant と、観測遅延、drop、command hold、jitter を fake clock 上で再生し、同じ seed/input から同じログを得る。

## 対象外

実機パラメータ同定、高忠実度空力、モーター/プロペラモデル、飛行許可。

## 実装方針

- `host/flight_sim.py` を純粋ロジックとして実装し wall clock / I/O を使わない。
- 単位は SI、world/body frame は `docs/vision-design.md` と一致させる。
- angle/altitude command の飽和と slew、観測周期と送信周期を別々に設定できるようにする。
- delay/loss/reorder/stale observation、producer stop、command expiry を fixture として注入する。
- モデル定数は「試験 fixture」と明記し、実機同定値を装わない。
- 出力を `host/qualification.py` が読める schema-v1 JSONL に変換する。

## 受け入れ条件

- [ ] 同一入力から deterministic な状態系列と qualification log を生成する。
- [ ] 観測遅延・drop・command expiry・jitter を個別/組合せで注入できる。
- [ ] stale observation や期限切れ intent で古い非ゼロ指令を延命しない。
- [ ] 単位/座標系/飽和が table-driven test で検証される。
- [ ] この simulator の成功を実飛行安定性として記録しない。

## テスト

`.venv/bin/python -m unittest discover -s host/tests -v`

## 依存

[20260908-flight-qualification](20260908-flight-qualification.md)、[20260908-vision-localization-design](20260908-vision-localization-design.md)
