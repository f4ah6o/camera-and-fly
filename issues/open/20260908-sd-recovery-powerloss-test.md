# disposable SD で boot-image 復旧と power-loss 境界を検証する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08

## 概要

実機 target を壊す前に、disposable SD/image 上で rootfs/kernel/selector commit の各中断点と offline recovery 手順を再現し、どこまで復旧を保証できるか測定する。

## 対象外

内部MTD write/erase、target camera での無準備な電源断、秘密設定のログ取得。

## 実装方針

- target layout inspection で確認した filesystem/layout を disposable media/image に再現する。
- download、stage、selector/rootfs rename、kernel rename、sync、first boot marker の各境界で中断 fixture を作る。
- filesystem check 後に active/previous/kernel/manifest がどの状態になるか記録する。
- offline recovery は known-good hash-verified artifact から復元し、対象 disk/card の識別を fail closed にする。
- destructive host command は disposable target の明示識別と dry-run/confirmation boundary を持つ専用手順に固定する。
- target camera の power cut は disposable media 結果と operator 手順のレビュー後の別承認にする。

## 受け入れ条件

- [ ] interruption matrix と期待/実測状態が保存される。
- [ ] wrong disk/card を拒否する識別手順がテストされる。
- [ ] known-good への offline restore を再現し hash/filesystem check を通す。
- [ ] rename+sync の実測結果を atomicity の保証範囲として限定的に記述する。
- [ ] target camera の real power-loss/rollback は実施されるまで unchecked のまま残す。

## 依存

[20260908-atomcam-boot-layout-inspection](20260908-atomcam-boot-layout-inspection.md)、[20260908-boot-image-deploy-cli](20260908-boot-image-deploy-cli.md)、[20260908-boot-health-rollback](20260908-boot-health-rollback.md)
