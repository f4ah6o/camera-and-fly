# initramfs に versioned rootfs selector と boot-attempt state を追加する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08

## 概要

固定 rootfs ファイルを直接上書きする現行 updater を置換せず、まず host fixture 上で versioned rootfs 選択・前版 fallback・boot-attempt state の安全な protocol を実装する。

## 目標

rootfs release を versioned file として保持し、小さな selector state から候補/前版を選ぶ。候補が規定回数 boot-success を得られなければ前版を選択できる設計にする。

## 対象外

kernel A/B の保証、bootloader変更、内部MTD書き込み、実機 enablement 前の電源断保証。

## 実装方針

- selector state は schema/version、candidate、previous、attempt count、operation ID を持つ。
- 不正/欠落/unknown release は推測せず known-good または明示 recovery path へ fail closed する。
- `boot-success` は sshd の起動だけに依存しない早期/後期 marker protocol とし、後続 health issue と連携する。
- state/file commit は同一 filesystem の staging + rename + sync を使うが、power-loss atomicity は実測まで主張しない。
- 最初は一時ディレクトリ fixture で interruption point を全列挙し、実カメラへ書かない。

## 受け入れ条件

- [ ] candidate/previous/attempt state の deterministic fixture tests がある。
- [ ] malformed/missing selector で未知 rootfs を起動対象にしない。
- [ ] boot-success がない candidate は規定回数後に previous を選ぶ fixture を再現できる。
- [ ] commit 各境界の中断後状態が故障表と一致する。
- [ ] 実機適用は boot layout と power-loss evidence が揃うまで無効。

## 依存

[20260908-atomcam-boot-layout-inspection](20260908-atomcam-boot-layout-inspection.md)、[20260908-boot-health-rollback](20260908-boot-health-rollback.md)
