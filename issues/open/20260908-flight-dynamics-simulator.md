# 遅延・欠損付きの簡易飛行 dynamics simulator を追加する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: implementation
Luna-Ready: yes
Branch: main

## Luna Max 着手契約

このissueは実機不要で単独着手可能。`host/flight_sim.py` + testsをpure/fake-clockで実装し、`host/qualification.py` schemaへ出力する。controller本体、実flight parameter同定、hardware I/Oは触らない。検証はhost test suite。

## 概要

G1 qualification 用に、外側位置制御器を実機なしで評価できる決定論的な簡易 dynamics と delay/loss injection を実装する。

## 背景

G0のFSM/replayだけでは、観測遅延、command expiry、producer停止が位置・速度・高度へ与える影響を同じ入力で比較できない。G2以降の実機試験へ進む前に、ソフトウェアだけで再現可能なG1 evidenceが必要である。

## 問題

実時間や実カメラに依存した試験は、同じfaultでも状態系列が変わり、古い観測や指令の延命を見逃しやすい。空力モデルを装っていない、明示的な試験用plantがまだない。

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

## 提案する方針

`host/flight_sim.py` に `world_frd` の最小plant、SI単位のcommand/observation channel、seed付きfault injectionを実装する。各出力に `evidence_kind=simulation` と `simulation=true` を付け、成功しても実機安定性とは判定しない。

## 受け入れ条件

- [x] 同一入力から deterministic な状態系列と qualification log を生成する。
- [x] 観測遅延・drop・command expiry・jitter を個別/組合せで注入できる。
- [x] stale observation や期限切れ intent で古い非ゼロ指令を延命しない。
- [x] 単位/座標系/飽和が table-driven test で検証される。
- [x] この simulator の成功を実飛行安定性として記録しない。

## テスト計画

`.venv/bin/python -m unittest discover -s host/tests -v`

## リスク

モデル定数はfixtureであり、実機の制御ゲイン・空力・安全余裕を推定するために使わない。simulatorのoutcomeはG1 software evidenceに限定する。

## 変更履歴

`CHANGES.md` impact: yes。G1 fixtureとschema-v1 simulation recordsを追加した。

## 実装記録（2026-09-08）

`host/flight_sim.py` と `host/tests/test_flight_sim.py` を追加した。delay/drop/reorder/jitter、producer stop、command TTL、world/body frameの符号、saturation/slew、deterministic JSONLをテストし、host test suiteでPASSした。

## 注記

このissueの完了は実機試験の許可やG2/G3通過を意味しない。依存：[20260908-flight-qualification](20260908-flight-qualification.md)、[20260908-vision-localization-design](20260908-vision-localization-design.md)
