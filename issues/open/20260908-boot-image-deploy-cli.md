# 検証済み boot image を SSH staging/commit する Mac CLI を追加する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08

## 概要

kernel/rootfs boot bundle を strict SSH で転送し、manifest/hash/compatibility を照合して operation-owned staging へ配置する Mac-side CLI を実装する。

## 目標

転送中断や再実行で active boot files を壊さず、commit と reboot を別操作にする。実機 enablement は layout/rollback/power-loss gate 完了後だけとする。

## 対象外

自動 reboot、自動 SSH recovery、MTD write/erase、bootloader変更、未確認 target への配布。

## 実装方針

- `host/camera_deploy.py` の strict known-hosts、target identity、runner injection、unique upload token、idempotent existing-release 検証を再利用/共通化する。
- manifest は model/release/compatibility/kernel/rootfs/hash/size/updater capability を持つ。
- stage は operation-owned temp のみへ書き、既存 active を転送中に上書きしない。
- same operation の identical retry は冪等、内容違いの release ID 再利用は拒否する。
- commit 前に free space、hash、layout capability、rollback readiness を再確認する。
- `commit` と `reboot` は別コマンドとし、dry-run は remote write/reboot を一切行わない。

## 受け入れ条件

- [ ] fake SSH で中断/再試行/競合/hash不一致/容量不足時に active が不変。
- [ ] strict host-key/target identity がない場合は stage 前に失敗する。
- [ ] path traversal/symlink/duplicate/oversize bundle を拒否する。
- [ ] commit と reboot が明示的に分離されている。
- [ ] real-device commit は recovery/power-loss gate が通るまで実施しない。

## 依存

[20260908-atomcam-boot-layout-inspection](20260908-atomcam-boot-layout-inspection.md)、[20260908-sd-release-verification](20260908-sd-release-verification.md)、[20260908-initramfs-rootfs-selector](20260908-initramfs-rootfs-selector.md)
