# Atom Cam の SD ランタイムを SSH で更新・差し戻しできる CLI を追加する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: coordinator
Luna-Ready: use-child-issues
Branch: codex/20260908-ssh-runtime-deploy

## Luna Max 着手契約

core CLI/idempotent stageは実装済み。残りはsoftware failure fixture `ssh-runtime-deploy-fault-injection` とreal strict-SSH確認 `ssh-runtime-deploy-live-rollback` に分割。親を直接拡張しない。

## 実行子issue

1. [failure injection](20260908-ssh-runtime-deploy-fault-injection.md) — fake runnerだけで残software acceptanceを完了。
2. [live rollback](20260908-ssh-runtime-deploy-live-rollback.md) — 1完了後、strict SSH実機でv1→v2→rollback。

boot image更新は別系統であり、この親issueへ混ぜない。

## 概要

カメラの SD 上に versioned ランタイムを配置し、検証後に選択して前版へ戻せる SSH 配布 CLI を実装する。

## 背景

現在カメラへの SSH 接続は一度確認済み。既存 rootfs は SquashFS で、SD は `/media/mmc` にある。起動イメージ更新は別の調査課題とする。

## 問題

小さなスクリプト/ランタイム更新にも配布・照合・差し戻しの手順がなく、途中失敗や誤った接続先を扱えない。

## 目標

`host/camera_deploy.py` で inspect、stage、activate、status、rollback を明示的に実行できる。最初は手動実行型ランタイムで成立させる。

## 対象外

kernel/rootfs 更新、再起動、SSH 鍵の作成/上書き、自動起動への登録、既存 camera streaming サービスの置換、StampFly 更新。

## 提案する方針

1. CLI は明示した `--host`、専用 known_hosts、期待 MAC/モデル、ローカル bundle を受ける。SSH host key 検証を必須とし `StrictHostKeyChecking=no` は使わない。秘密鍵内容は読まず既存 ssh-agent を使用する。MAC 未登録時は stage 前に終了する。
2. SSH 呼び出しを injectable な runner に分離し、引数配列と stdin で固定 remote script を渡す。remote shell に release ID やファイル名を無検証展開しない。初期テストは fake SSH のみ。
3. `/media/mmc/camfly/releases/<release-id>/` と `active`/`previous` の小さなテキストファイルを提案構造とする。release ID は ASCII 許可文字に限定。FAT/exFAT を想定して symlink・chmod の永続性を必要条件にしない。launcher は manifest の entrypoint を `/bin/sh` で実行する仕様から開始する。
4. `inspect` で mount と残容量と hash ツールを確認し、書き込み先が SD 配下であることを確認。不明な配置なら失敗。manifest v1 は type=runtime、対象モデル、ID、entrypoint、許可ファイル一覧、各 size/hash を持つ。ZIP 全体の hash だけで済ませない。
5. `stage` は新規一時ディレクトリへ転送し、全ファイルを検証してから release として確定。絶対パス、`..`、symlink、重複、容量上限超過を拒否。既存 release の内容を上書きしない。ロックで多重操作を防ぐ。
6. `activate` は検証済み release のみ選択し、同じ SD 内の一時ファイルから active を rename、sync する。現在のプロセスは勝手に停止しない。`rollback` は保持した前版を選ぶ。壊れた active/previous は推測せずエラーとする。電源断完全保証は主張せず手動復旧コマンドを記す。
7. 無害な version 表示 entrypoint fixture を同梱し、識別済みカメラで v1→v2→rollback を手動実行して確認する。自動で service restart/reboot しない。後の boot integration は SSH 更新調査の子課題に渡す。

## 受け入れ条件

- [x] 各コマンドに help と dry-run があり、dry-run は転送・書き込み・実行しない。
- [ ] 誤 host key/MAC、容量不足、hash 不一致、途中切断、多重操作で active が不変。
- [x] 同じ release の再 stage は一致なら冪等、内容が違えば拒否。
- [ ] fixture v1/v2 の切替と前版への rollback を fake SSH と実機で記録できる。

## テスト計画

`host/tests/test_camera_deploy.py` で remote runner とローカル一時 SD を使い、各操作境界で失敗注入。引数中の空白・引用符・shell 文字で別コマンドが実行されないことを確認。実機不在でも単体テストは走る。

## リスク

更新済みファイルと稼働中プロセスは別状態。status は active release と実行確認結果を区別する。hash 照合は SSH で認証された送信元を前提とし、第三者配布の署名検証を代替しない。

## 実装記録（2026-09-08）

`host/camera_deploy.py` に strict known-hosts SSH runner、target MAC/model/hash-tool preflight、manifest/file hash validation、inspect/stage/activate/status/rollback、dry-run、remote lock、same-SD marker update を実装した。stage は毎回 unique upload token を使い、operation-owned temp を cleanup する。同一 release が既に存在する場合は全 manifest/file 内容が一致するときだけ idempotent success とし、内容違いは拒否する。

`host/tests/test_camera_deploy.py` では stage→activate→rollback、idempotency、identity/capacity/hash-tool preflight、traversal/hash mismatch を fake runner で検証している。SSH runner は `StrictHostKeyChecking=yes` と専用 known_hosts を使用し、dry-run は remote transfer/write を行わない。ただし host-key mismatch、転送途中切断、remote lock競合の各 fault point で active 不変を自動注入する test はまだ揃っていないため、その複合 acceptance は未達として残す。

識別済み real Atom Cam での fixture v1→v2→rollback と、real SSH disconnect/rollback は未実施なので最後の acceptance は未達のまま残す。device-specific network identity は記録しない。

## 変更履歴

`CHANGES.md` impact: yes

項目案：カメラの SD 上に versioned ランタイムを配置し、検証後に選択して前版へ戻せる SSH 配布 CLI を実装する。

実装時に既存の変更履歴規約を確認する。現時点では `CHANGES.md` は存在しないため、計画作成だけを理由に新設しない。

## 注記

実装担当の想定：`gpt-5.6-luna`、reasoning effort `max`。`Model` は本文の最終編集者を表すため、担当予定モデルとは区別する。作成環境から正確なモデル識別名を確認できないため `unknown`。実装着手時に自身の識別名へ更新する。

依存：[20260908-sd-release-verification](20260908-sd-release-verification.md)

全体計画：[20260908-flight-roadmap](20260908-flight-roadmap.md)。共通の実装手順、既存資産の保護、未確定条件を着手前に読む。
