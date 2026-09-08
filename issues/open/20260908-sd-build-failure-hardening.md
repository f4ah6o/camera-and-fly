# Atom Cam SD build の failure/signal/concurrency cleanup をfixtureで検証する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: implementation
Luna-Ready: yes
Branch: main

## 概要

`atomcam-sd/build.sh` の未達acceptanceである出力衝突、build failure、INT/TERM、同時実行時のcleanup/既存成果物保護をfake runnerで再現可能にする。

## 背景

SD buildはvendor sourceへ一時patchを適用し、Lima内builderの成果物をrelease artifactへコピーする。長時間build、signal、または同時実行で一時ファイルやpatchが残ると、次回buildや既存artifactの信頼性を損なう。

## 問題

failure pathを実builderで再現するのは遅く破壊的で、既存releaseを上書きしないことや他runのlockを触らないことを自動検証できていなかった。

## 目標

fake Lima/builderとisolated git sourceを使い、成功・builder failure・signal・lock競合・collisionのcleanup ownershipを自動検証する。失敗時に既存成果物を削除/変更しない。

## この issue だけでやること

- `limactl`/builderをfake化してfailure pointを注入するtest harnessを追加する。
- release dir collision、staging archive既存、build途中失敗、INT/TERM、lock競合を検証する。
- cleanup後にvendor submodule clean、他runのartifact/lock不変を確認する。
- real長時間buildは不要。既存manifest/verifier testsは維持する。

## 対象ファイル

`atomcam-sd/build.sh`、`atomcam-sd/tests/test_build_release.py`、必要ならtest fixtures。

## 対象外

実SD書込、実カメラ、内部flash、既存artifact削除。

## 提案する方針

build scriptに環境変数で検証対象を差し替えられる境界、release collision check、source/staging/release/lockの所有フラグ付きtrap cleanupを追加する。fixtureは一時git repo、fake `limactl`、既存sentinelで失敗後の状態を確認する。

## 受け入れ条件

- [x] collision/failure/INT/TERM/concurrencyをautomated testで再現する。
- [x] 失敗時も既存release/ZIP/logを変更しない。
- [x] 自分が作ったstaging/lockだけcleanupする。
- [x] vendor submoduleがtest後cleanである。
- [x] SD testsと`bash -n atomcam-sd/build.sh`がPASSする。

## テスト計画

`atomcam-sd/tests/test_build_release.py` を一時workspaceで実行し、success/builder failure/SIGTERM/lock competitionとartifact sentinelを確認する。既存SD test suiteと `bash -n atomcam-sd/build.sh` も実行する。

## リスク

fixtureはbuilderのfailure境界とshell cleanupを検証するが、Lima/Docker/実SDの電源断やfilesystem durabilityは証明しない。実artifactはテストで変更しない。

## 変更履歴

`CHANGES.md` impact: yes。SD buildのfailure/signal/concurrency hardeningとisolated fake harnessを追加した。

## 実装記録（2026-09-08）

`atomcam-sd/build.sh` にroot/source/patch/output/verifier overrides、release collision guard、所有権付きcleanupとbuild lockを追加した。`atomcam-sd/tests/test_build_release.py` の3 fixture testsがPASSし、既存SD testsとshell syntax checkもPASSした。

## Luna Max 着手契約

実builderを壊すfailure injectionはしない。fake command/environmentで完了させる。既存`artifacts/`を削除/上書きしない。

## 依存

[20260908-sd-release-verification](20260908-sd-release-verification.md)

## 注記

本issueはsoftware fixtureによるhardeningであり、実SDへの書き込み、実builderの長時間実行、内部flash操作を行っていない。
