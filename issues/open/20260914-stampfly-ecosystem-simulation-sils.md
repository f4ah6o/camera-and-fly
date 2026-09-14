# StampFly Ecosystem の sim/SILS を fail-closed な任意バックエンドとして統合する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-14
Updated: 2026-09-14
Kind: implementation
Luna-Ready: yes
Branch: main

## Luna Max 着手契約

このissueは実機不要で単独着手可能。`host/stampfly_sim.py` と focused tests を中心に、必要な issue index / README / CHANGES のみ更新し、既存 `host/flight_sim.py`、`host/integration.py`、`host/mission.py`、`host/qualification.py` の挙動は変更しない。外部 `sf` の install / download / flashing / serial discovery / ARM / 非ゼロ指令は対象外。外部 `sf` が PATH に無くても tests は通る。検証は host test suite と `git diff --check`。

## 概要

macOS で文書化されている StampFly Ecosystem の `sf sim` / `sf sils` CLI を、成功を実飛行合格と混同しない形で host 側から任意利用できる薄い fail-closed バックエンドを追加する。

対象は実 `sf` CLI の次の surface に限定する。

- `sf sim list`
- `sf sim headless [vpython|genesis] -d <seconds> -o <file.sflog.zip>`
- `sf sils scenario <path.scn>`

## 背景

既存の `host/flight_sim.py` は決定論的なローカル G1 plant であり、外部依存なしで完結する。一方、StampFly Ecosystem の `sf` は VPython / Genesis などの高忠実度 sim と SILS regression を提供し、実機前の追加 evidence を得る候補になる。ただし `sf` はこのリポジトリの一部ではなく、環境によっては存在しない。

## 問題

外部 sim の起動を無条件に前提にすると、未導入環境で test/CI が壊れる。また sim の成功をそのまま実飛行の合格として記録すると、安全境界が崩れる。任意 argv を転送できる薄い wrapper は shell 経由や危険な command shape を通しやすい。

## 目標

外部 `sf` を任意依存として検出し、許可した command shape だけを配列 argv で実行し、結果を simulation provenance として返す。未導入・timeout・非ゼロ終了は明示エラーにし、成功を実飛行合格として扱わない。

## 対象外

- `sf` の install / download / build / network アクセス
- firmware flashing、serial/USB の open、port discovery
- ARM / disarm / takeoff / motor command / 非ゼロ制御値
- `host/stampfly.py` の実機 API 呼び出し
- `sf sim run`（VPython）や `sf sils gui` など対話的/表示系 surface の自動実行
- camera → mission → sim の真の closed-loop（別issue。下記 follow-up 参照）

## 実装方針

1. `host/stampfly_sim.py` を追加する。`sf` を明示 path または PATH から検出し、`found` / `source` / `reason` を含む決定論的な diagnostics を返す。
2. command は tuple argv のみで構築し、shell 文字列や `shell=True` を使わない。`sf sim list` / `sf sim headless [vpython|genesis] -d N -o FILE` / `sf sils scenario PATH.scn` の3種に限定し、backend は固定集合、output/scenario は制御文字と option-like 値を拒否し、scenario は `.scn` 拡張子を必須にする。headless は常に明示 `-o` を渡し、未指定時は workspace `artifacts/` 配下の既定 path を使う。
3. process runner を dependency injection 可能にし、tests では fake runner を使う。既定 runner は `shell=False`、stdin 無効、timeout、出力は一時ファイルへ逃がして先頭のみ取得する。
4. timeout / 非ゼロ終了 / 実行ファイル不在を明示例外にする。非ゼロ終了例外は provenance を保持する。
5. 結果は `provider=stampfly_ecosystem`、`evidence_kind=simulation`、`simulation=true`、`flight_qualified=false`、command kind/backend、exit status、bounding/redaction 済み stdout/stderr metadata を持つ構造体で返す。
6. CLI は既定で読み取り専用の `list` とし、`headless` / `sils-scenario` のみ明示 subcommand で実行する。実機 action は提供しない。

## 受け入れ条件

- [x] 外部 `sf` 未導入でも全 focused tests が通り、明示的な missing-executable diagnostics を返す。
- [x] 生成される argv が `sf sim list` / `sf sim headless [vpython|genesis] -d N -o FILE` / `sf sils scenario PATH.scn` のみである。
- [x] 任意 argv 転送・shell 経由・危険な scenario/output/backend/timeout/duration を拒否する。
- [x] headless は常に明示 `-o` を渡し、CLI 既定出力が workspace `artifacts/` 配下に留まる。
- [x] scenario は識別子ではなく安全に検証した `.scn` path として渡す。
- [x] timeout と非ゼロ終了が明示エラーになる。
- [x] stdout/stderr metadata が上限付きで、secret/home path を redact する。
- [x] 結果 provenance が simulation であり real flight qualification ではないと明示する。
- [x] serial/USB を open せず、`host/stampfly.py` の ARM/disarm/control を呼ばない。
- [x] `git diff --check` が clean。

## テスト計画

`host/tests/test_stampfly_sim.py` で command 構築、missing `sf`、非ゼロ終了、timeout、output bounding/redaction、許可 argv のみの生成、shell 非使用を検証する。外部 `sf` の導入状態に依存しない。最後に `host/tests` の全 unittest を実行する。

## リスク

外部 `sf` の CLI 仕様は upstream 側で変わりうる。このissueは文書化済みの3 surface のみを固定し、対話系や install 系へ広げない。sim の outcome は G1 相当の software evidence に限定し、実機安定性の証明には使わない。

## Follow-up

camera → mission → sim の真の closed-loop は本issueでは扱わない。実装するには次が必要になる。

- StampFly Ecosystem が文書化した telemetry/command API（または安定した stdout protocol）
- 固定 Atom Cam 1 の pose を `host/mission.py` の `HealthSnapshot` と `host/flight_sim.py` の観測へ橋渡しする契約
- `sf` 側の structured telemetry と、こちらの mission FSM を再接続・stale 時に fail-closed にする adapter

これらが揃うまで、`stampfly_sim.py` は片方向の sim 実行と provenance 記録に留める。

## 変更履歴

`CHANGES.md` impact: yes。任意の `sf` sim/SILS backend と provenance を追加した。

## 実装・検証記録（2026-09-14）

- `host/stampfly_sim.py` と `host/tests/test_stampfly_sim.py` を追加。
- allow-list は完全な argv shape を再検証し、余分な option/argument を runner へ渡す前に拒否する。
- provenance の executable/argv と stdout/stderr は home path を redact し、stdout/stderr は bounded capture とする。
- `.venv/bin/python -m unittest host.tests.test_stampfly_sim -v`: 33/33 PASS（実 CLI 整合後、relative output 正規化の regression test を含む）。
- AtomCam を除く host suite: 113/113 PASS。
- full `unittest discover -s host/tests -v` は `test_atomcam.AtomCamTests.setUpClass` の localhost HTTP test server bind が Temote sandbox で `PermissionError: [Errno 1] Operation not permitted` となり、テスト本体実行前に ERROR。StampFly simulation 関連の失敗ではない。
- `git diff --check`: PASS。

### 実 CLI 整合への修正（2026-09-14）

- 実 `sf sim headless` の usage は `[-d DURATION] [-i INPUT] [-o OUTPUT] [{vpython,genesis}]`。`sf sils scenario` の positional は scenario 名ではなく `.scn` file への path。
- `command_headless` は `(sf, sim headless, <backend>, -d N, -o FILE)` を生成し、backend を `vpython`/`genesis` に限定。`-i` 入力 CSV は対象外のまま。
- headless は常に `-o` を明示し、既定出力を workspace `artifacts/stampfly-<backend>-smoke.sflog.zip` に固定した。upstream の `stampfly_ecosystem/logs` へ fallback させない。
- scenario は識別子 regex を廃止し、制御文字と option-like 値を拒否したうえで `.scn` 拡張子 path のみを受け付ける。
- allow-list は backend/output を含む完全な argv shape を再検証する。
- 2026-09-14 時点の実 CLI 実行確認: `sf sim headless vpython -d 2 -o /Volumes/DevSSD/Developer/camera-and-fly/artifacts/stampfly-vpython-smoke.sflog.zip` が 200 samples で成功。
- wrapper 経由でも `--output artifacts/stampfly-vpython-wrapper-smoke.sflog.zip` を workspace の絶対 path に正規化して実行し、1 秒 / 100 samples で成功。生成物は `artifacts/stampfly-vpython-wrapper-smoke.sflog.zip`。
- `git diff --check`: PASS。

## 注記

依存：[20260908-flight-dynamics-simulator](20260908-flight-dynamics-simulator.md)。関連：[20260909-camera-stampfly-closed-loop](20260909-camera-stampfly-closed-loop.md)。全体計画：[20260908-flight-roadmap](20260908-flight-roadmap.md)。
