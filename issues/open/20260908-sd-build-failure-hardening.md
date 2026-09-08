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

## この issue だけでやること

- `limactl`/builderをfake化してfailure pointを注入するtest harnessを追加する。
- release dir collision、staging archive既存、build途中失敗、INT/TERM、lock競合を検証する。
- cleanup後にvendor submodule clean、他runのartifact/lock不変を確認する。
- real長時間buildは不要。既存manifest/verifier testsは維持する。

## 対象ファイル

`atomcam-sd/build.sh`、`atomcam-sd/tests/test_build_release.py`、必要ならtest fixtures。

## 対象外

実SD書込、実カメラ、内部flash、既存artifact削除。

## 受け入れ条件

- [ ] collision/failure/INT/TERM/concurrencyをautomated testで再現する。
- [ ] 失敗時も既存release/ZIP/logを変更しない。
- [ ] 自分が作ったstaging/lockだけcleanupする。
- [ ] vendor submoduleがtest後cleanである。
- [ ] SD testsと`bash -n atomcam-sd/build.sh`がPASSする。

## Luna Max 着手契約

実builderを壊すfailure injectionはしない。fake command/environmentで完了させる。既存`artifacts/`を削除/上書きしない。

## 依存

[20260908-sd-release-verification](20260908-sd-release-verification.md)
