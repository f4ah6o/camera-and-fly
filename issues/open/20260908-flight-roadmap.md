# SSH 更新・カメラ連携・安定自動飛行の段階的実装計画

Status: open
Model: unknown
Created: 2026-09-08
Updated: 2026-09-08
Branch: codex/20260908-flight-roadmap

## 概要

Atom Cam 1 の SSH 更新と StampFly の映像連携を整備し、測定と段階評価を経て自動飛行へ進む。

## 背景

現行構成は Atom Cam 1 → Mac の映像入力、Mac → USB/CF1 → StampFly の指令。詳細は [README](../../README.md)、[SD ビルド](../../atomcam-sd/README.md)、[ホスト](../../host/controller.py)、[CF1](../../firmware/stampfly/src/usb_bridge.cpp)。

2026-09-08 時点：`camfly-safe` 書き込み済みで全モーター PWM はゼロ固定。飛行用カスタムビルドは未作成。カメラは SD 起動、Web UI、SSH を一度確認したが映像遅延は未評価。カメラの MAC は未記録、過去に記録された IP アドレスは本人性を保証しない。通常 SD イメージと読み取り専用派生イメージを混同しない。

計画開始時の未コミット変更は `atomcam-sd/README.md`、`atomcam-sd/patches/0001-source-read-only.patch`、`atomcam-sd/tests/`。MTD ガードの強化であり保持する。既存 ZIP はこの修正を含まない。

## 問題

更新経路の復旧性、制御通信の欠損処理、映像の鮮度、自由飛行用通信、着陸指令が未整備。「自動飛行できる」と「安定性を測定済み」は分ける必要がある。

## 目標

最初の到達目標は、室内の固定カメラで機体マーカーを観測し、明示的な開始操作後に低高度で離陸・定点保持・着陸する一連のミッション。安定性は再現可能なログと合否基準で判断する。

## 対象外

屋外運用、障害物回避、人物追跡、自由な経路探索、複数機、カメラ内部フラッシュの改変。これらは初回のホバリング成立後に別計画とする。

## 提案する方針

### 暫定前提と決定待ち

- SSH 更新はまず Atom Cam 1 の SD 上のランタイムを対象とする。OS/カーネル更新は起動・復旧経路を調査してから実装仕様を確定する。StampFly は当面 Mac から USB 更新。SSH で StampFly 自体へ接続できるとは仮定しない。
- カメラは室内固定、機体に識別マーカーを置く。カメラを機体搭載する場合は重量・電源・視野・観測方式の設計から見直す。
- カメラの取付位置、作業空間、照明、希望高度、保有する無線ゲートウェイ機材は未確認。依存しないコード作業を先行し、関連調査で記録する。
- USB は机上試験用。自由飛行用のリンクは別途選定し、USB ケーブル付き試験を自由飛行成功と数えない。

### 順序と実行単位

| 段階 | イシュー | 依存 / 完了するとできること |
|---|---|---|
| A1 | [SD リリース検証](20260908-sd-release-verification.md) | 既存ガード変更を検証し、出所を追える成果物を作る |
| A2 | [SSH ランタイム更新](20260908-ssh-runtime-deploy.md) | A1 後。起動イメージを置換せず SD 上のアプリを更新・差し戻し |
| A3 | [SSH 起動イメージ更新調査](20260908-ssh-update-recovery-design.md) | A1 後。OS 更新と復旧の実装イシューを確定 |
| B1 | [CF1 堅牢化](20260908-cf1-protocol-hardening.md) | 独立。異常指令・受信洪水・古い sequence を拒否 |
| B2 | [ホスト周期制御](20260908-host-control-scheduler.md) | B1 と独立して単体開発可。統合は B1 後 |
| B3 | [映像取得と遅延計測](20260908-camera-stream-measurement.md) | 独立。鮮度を判定できるフレーム入力 |
| C1 | [カメラ・機体 dry-run](20260908-camera-stampfly-dry-run.md) | B1+B2+B3 後。モーター停止状態で全経路の欠損を評価 |
| C2 | [視覚位置推定調査](20260908-vision-localization-design.md) | B3 後。校正方式・ライブラリ・精度を検証し実装イシュー化 |
| C3 | [飛行通信調査](20260908-flight-link-design.md) | B1 後。USB から自由飛行用リンクへ移行する仕様を確定 |
| C4 | [高度・着陸・failsafe 調査](20260908-altitude-failsafe-design.md) | B1 後。単位・指令・故障別の動作を確定 |
| D1 | [ミッション状態機械](20260908-mission-supervisor.md) | B2 後。入出力を偽物にして遷移を実装。実機 adapter は C2〜C4 の子課題待ち |
| E1 | [飛行評価設計](20260908-flight-qualification.md) | C1〜C4+D1 後。制御器設計・段階試験の具体的な次課題を作る |

調査の結果が必要な、OS 更新・無線 transport・実視覚推定・高度指令・外側位置制御器・飛行用ビルドの実装課題は、推測で固定せず調査終了時に追加する。各調査の受け入れ条件に「後続イシューを作る」を含める。この親イシューは子課題を作っただけでは完了しない。

### Luna max 向け作業規約

1. 1 回に 1 子イシューを扱う。依存成果物を読み、未達なら、その成果に依存しないテスト・調査だけ進める。仮の仕様を実機で試さない。
2. 着手時に `git status --short --branch` を確認。無関係な未コミット変更を保持する。ファイル名は子課題の提案を基本とし、既存コードに相当機能があれば再利用する。
3. 純粋ロジック → fake 入出力 → safe ビルド → 実機の順。新しいテストコマンドを README に記載する。テストのためにカメラやシリアルポートを自動探索しない。
4. ローカル単体検証の標準は `.venv/bin/python -m unittest discover -s host/tests -v`、SD は `python3 -m unittest discover -s atomcam-sd/tests -v`。テストディレクトリ未作成なら各子課題で作る。ファームウェアは `cd firmware/stampfly && ../../.venv/bin/pio run -e camfly-safe`。
5. `vendor/` は pinned submodule として維持し、カメラ改修は `atomcam-sd/patches/`、StampFly 改修は `firmware/stampfly/` に置く。実装対象以外のリファクタリングを混ぜない。
6. テスト結果、未検証の実機条件、ログのパス、変更履歴判断を各課題に残す。実装済みでも実機受け入れ未達なら完了扱いにしない。

### 共通の運用境界

- 元の Atom Cam 1 のみ対象。atomcam2 を探索・更新しない。カメラ識別と SSH host key の照合は接続先確定の条件。
- SD 更新と内部 MTD 書き込みは別物。既存の MTD 読み取り専用保護を維持する。既存バックアップ・ZIP・ログを上書きしない。
- 1Password Developer Environments が必要なら 1Password MCP と実行時注入を使用し、秘密値を表示・記録しない。SSH は既存 agent/鍵認証を使う。
- ARM は明示的操作のみ。起動・再接続・映像復帰を ARM の契機にしない。異常時に ESP-NOW へ所有権を自動移譲しない。
- この計画作成は実機の飛行開始操作ではない。実飛行段階では対象機体、飛行場所、開始・中止操作を具体化した実施手順を用意する。


## 受け入れ条件

- [x] A〜E の依存関係と各子課題の成果物がリンクされている。
- [ ] SSH ランタイムと起動イメージ更新の双方が実装・復旧試験まで完了している。
- [ ] 映像連携と通信断の試験が camfly-safe で再現できる。
- [ ] 自由飛行用通信・視覚位置推定・高度/着陸指令・位置制御器の後続実装が完了している。
- [ ] 飛行評価で合意した定量基準を満たす反復飛行ログがあり、途中中止や失敗も記録されている。

## テスト計画

計画段階は全 Markdown の必須フィールド・ローカルリンク・依存循環を検証する。実装完了時は各子課題と調査から派生した全実装課題の受け入れ条件を確認する。

## リスク

単眼カメラの遮蔽や遅延、機体の振動、通信欠損、電池変動で達成可能な性能が変わる。安定飛行をソフトウェア実装だけで保証しない。測定で成立しなければ視野・設置・観測センサを再設計する。

## 実装記録（2026-09-08）

依存しないソフトウェア部分を先行実装した。親イシューは open のままとし、実機の合否をコードテストで代替しない。

- A1: `atomcam-sd/build.sh` の release ID、衝突拒否、submodule lock/cleanup、ZIP verifier、builder rootfs/kernel evidence を実装した。生成した成果物と build log はローカルの `artifacts/` に保存したが、device-specific な成果物は公開リポジトリへ含めない。`source_evidence.all_checked_passed=true`。既存の通常 ZIP と旧 read-only ZIP は上書きしていない。
- A2: `host/camera_deploy.py` に strict known-hosts SSH、MAC/model/hash-tool preflight、manifest/hash 検証、stage/activate/status/rollback、same-SD marker rollback、remote lock、fake runner を追加した。実機 SSH の v1→v2→rollback は未実施。
- B1: CF1 parser/session を Arduino 非依存に分離し、有限値・厳密 token 数・sequence freshness・claim reset・250 ms watchdog・overlong discard・poll byte budget・nonblocking TX gate を追加した。native test と `camfly-safe` build が成功した。
- B2: `host/control_loop.py` の immutable expiring intent、single-owner scheduler、latest mailbox、pre-send watchdog、fault latch を `controller.py` と `stampfly.py` に接続した。
- B3: JPEG response bound、同一 host redirect、frame clocks、latest-frame worker、probe 分位点計測、camera fixture tests を追加した。実カメラの exposure-to-receive は未測定。
- C1/D1: `host/integration.py` の既定 replay、zero-only safe adapter、bounded JSONL、fault injection と、実 I/O を持たない `host/mission.py` の明示 START/TAKEOFF/LAND FSM を追加した。`docs/` に vision/link/altitude/qualification/recovery の未確定境界を記録した。

検証結果：`.venv/bin/python -m unittest discover -s host/tests -v`（24件）、`python3 -m unittest discover -s atomcam-sd/tests -v`（11件）、`bash firmware/stampfly/tests/run_native_tests.sh`、`.venv/bin/pio run -e camfly-safe`、`bash -n atomcam-sd/build.sh`、`git diff --check` が成功した。

未達：実カメラの60秒以上の映像計測、識別済みカメラへの SSH deploy、safe hardware 10分ログ、自由飛行 transport、校正済み視覚推定、高度/LAND adapter、flight build、実飛行ログは機材・実測・安全手順が未確定のため実施していない。

## 変更履歴

`CHANGES.md` impact: no

## 注記

実装担当の想定：`gpt-5.6-luna`、reasoning effort `max`。`Model` は本文の最終編集者を表すため、担当予定モデルとは区別する。作成環境から正確なモデル識別名を確認できないため `unknown`。実装着手時に自身の識別名へ更新する。

依存：なし。
