# camera runtime deploy の failure/concurrency/idempotency fixture を完成させる

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: implementation
Luna-Ready: yes
Branch: main

## 概要

`host/camera_deploy.py` のfake remoteを拡張し、途中切断、hash不一致、競合、異内容release再利用などでactive markerが不変であることをソフトウェアだけで証明する。

## この issue だけでやること

- stage upload中断、remote extract/hash失敗、activate marker write失敗、lock競合をfailure injectionする。
- same release identical retryはidempotent、異内容はrejectする。
- active/previous/temp/lockのcleanup ownershipを境界ごとに検証する。
- strict identity/preflight failureがwrite前に停止するtestを増やす。

## 対象ファイル

`host/camera_deploy.py`、`host/tests/test_camera_deploy.py`、親issueの実装記録。

## 対象外

real SSH、実カメラv1/v2/rollback、reboot、boot image更新。

## 受け入れ条件

- [ ] transfer/extract/hash/marker/lock各失敗点でactiveが不変。
- [ ] identical retryのみidempotentで、release ID内容衝突を拒否する。
- [ ] cleanupが他operationのstaging/lockを削除しない。
- [ ] wrong identity/capacity/hash-tool failureがwrite前に停止する。
- [ ] host testsがPASSする。

## Luna Max 着手契約

実機SSHを開かずfake runnerで完了する。failure pathのためにproduction safety checkを緩めない。

## 依存

[20260908-ssh-runtime-deploy](20260908-ssh-runtime-deploy.md)
