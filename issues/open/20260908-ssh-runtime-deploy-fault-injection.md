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

## 背景

runtime deployはSD上へversioned releaseをstageし、active/previous markerを明示操作する。SSH転送、extract/hash、marker、lockの途中失敗で、別operationの一時領域やactive markerを壊さない境界が必要である。

## 問題

通常の成功テストだけでは、failure pathのcleanup ownership、同一release retry、異内容collision、preflight停止を確認できない。real SSHを使うと対象カメラを変更するリスクがある。

## 目標

in-memory fake remoteにfailure pointとlock/temporary/incoming trackingを実装し、active/previousが失敗前と同じで、owned stateだけがcleanupされることを自動検証する。

## この issue だけでやること

- stage upload中断、remote extract/hash失敗、activate marker write失敗、lock競合をfailure injectionする。
- same release identical retryはidempotent、異内容はrejectする。
- active/previous/temp/lockのcleanup ownershipを境界ごとに検証する。
- strict identity/preflight failureがwrite前に停止するtestを増やす。

## 対象ファイル

`host/camera_deploy.py`、`host/tests/test_camera_deploy.py`、親issueの実装記録。

## 対象外

real SSH、実カメラv1/v2/rollback、reboot、boot image更新。

## 提案する方針

stage/activate/rollbackそれぞれでlockを取得し、transfer/extract/hash/marker/lockを注入可能にする。stageは同一payloadだけidempotentにし、異内容の同releaseはrejectする。production remote scriptにもtoken付きstaging pathとownership-aware cleanupを反映する。

## 受け入れ条件

- [x] transfer/extract/hash/marker/lock各失敗点でactiveが不変。
- [x] identical retryのみidempotentで、release ID内容衝突を拒否する。
- [x] cleanupが他operationのstaging/lockを削除しない。
- [x] wrong identity/capacity/hash-tool failureがwrite前に停止する。
- [x] host testsがPASSする。

## テスト計画

`host/tests/test_camera_deploy.py` でfailure pointごとのmarker不変、temporary/incoming/lock cleanup、idempotent/collision、identity/capacity/hash-tool preflightをfake remoteで検証する。real SSHは実行しない。

## リスク

fake remoteはshell/tar/filesystemの全挙動を再現しない。実機でのpower-loss marker durability、SSH server差異、camera model差異は別の安全な測定が必要である。

## 変更履歴

`CHANGES.md` impact: yes。remote deploymentのfailure injectionとownership tracking testsを追加した。

## 実装記録（2026-09-08）

`host/camera_deploy.py` のproduction stage temp名をtoken付きにし、`FakeRemoteRunner`へtransfer/extract/hash/marker/lock fault injectionを追加した。host deploy testsがPASSし、real SSH・実カメラ変更は未実施である。

## Luna Max 着手契約

実機SSHを開かずfake runnerで完了する。failure pathのためにproduction safety checkを緩めない。

## 依存

[20260908-ssh-runtime-deploy](20260908-ssh-runtime-deploy.md)

## 注記

本issueの完了は、active markerの電源断atomicity、実cameraへ安全にdeployできること、reboot後のruntime動作を保証しない。
