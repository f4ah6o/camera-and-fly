# Atom Cam 1 の boot/SD layout を read-only で実測する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08

## 概要

識別済みの original Atom Cam 1 に strict SSH で接続し、boot-image updater の設計に必要な SD/boot/tool 情報だけを read-only で取得する。

## 対象外

ファイル更新、reboot、MTD write/erase、鍵/Wi-Fi設定の採取、別カメラの探索。

## 取得項目

- mount source/type/options と SD partition 構成;
- `/boot` と `/media/mmc` の対応、free space;
- kernel/rootfs/initramfs の存在と固定ファイル名;
- 利用可能な `sha256sum`/`md5sum`/`mv`/`sync`/`flock` 相当ツール;
- CPU/model/boot flags の非秘密メタデータ;
- host key が strict known_hosts と一致すること。

Device-specific MAC/IP/host-key 値そのものは公開 issue/docs に記載せず、検証結果だけを残す。

## 受け入れ条件

- [ ] operator-verified target identity と strict host-key check の後だけ取得する。
- [ ] SD layout/filesystem/free space/tool availability を実測として記録する。
- [ ] source-derived fact と real-device fact を区別する。
- [ ] 認証情報・Wi-Fi設定・private key・device-specific network identity をログに残さない。
- [ ] 書き込み/reboot/MTD操作を一切行わない。

## 依存

[20260908-ssh-update-recovery-design](20260908-ssh-update-recovery-design.md)
