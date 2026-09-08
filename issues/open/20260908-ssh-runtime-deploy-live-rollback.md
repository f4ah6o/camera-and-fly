# Atom Cam runtime v1→v2→rollback を strict SSH 実機で確認する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: hardware-validation
Luna-Ready: blocked-on-fault-injection
Branch: main

## 概要

software fault-injection完了後、識別済みoriginal Atom Cam 1へ無害なversion fixtureをstage/activateし、v1→v2→rollbackをstrict SSHで確認する。

## この issue だけでやること

- operator-verified targetとknown_hostsを使う。
- inspect/dry-run→stage v1→activate→status→stage v2→activate→rollback→statusを実施する。
- entrypointは無害なversion表示だけとしservice restart/rebootしない。
- active/previous/hashの結果をdevice identityを除いて記録する。

## 対象外

kernel/rootfs、reboot、MTD、camera service置換、StampFly。

## 受け入れ条件

- [ ] strict host key/target identity確認後のみwriteする。
- [ ] v1→v2→rollback後にactiveがv1へ戻る。
- [ ] 各releaseのhash/manifestが一致する。
- [ ] service restart/reboot/MTD writeを行わない。
- [ ] IP/MAC/host-key/credentialをtracked fileに残さない。

## Luna Max 着手契約

実機接続情報が取得できなければblockedで終了し成功扱いしない。別cameraを探索しない。

## 依存

[20260908-ssh-runtime-deploy-fault-injection](20260908-ssh-runtime-deploy-fault-injection.md)
