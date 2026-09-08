# SSH 経由の起動イメージ更新と復旧方式を調査する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: completed-scope
Luna-Ready: no-direct-work
Branch: codex/20260908-ssh-update-recovery-design

## Luna Max 着手契約

recovery boundary設計と子issue作成は完了済み。実装はboot child chainで行う。source-derived sequential rootfs/kernel updateをatomicと表現しない。

## 概要

Atom Cam 1 の SD カーネル/rootfs を SSH から更新するため、実際の起動・置換・失敗復旧の方式を確定する。

## 背景

`vendor/atomcam_tools/initramfs_skeleton/init` は `/rootfs/update/` の ZIP を展開し rootfs と kernel を順に置換する。`/rootfs` は後に `/media/mmc` へ移動され、`/boot` は ro remount される。1/2 パーティション分岐がある。

## 問題

現状は rootfs と kernel の一括原子更新ではない。転送成功と boot 成功は異なる。起動不能では SSH によるロールバックもできないため、A/B と自動復旧を根拠なく約束できない。

## 目標

実機の SD レイアウトに基づく設計記録と、小さく分割した後続実装イシューを作る。

## 対象外

この調査での kernel/rootfs 置換、再起動、電源断試験、bootloader/MTD の書き換え。

## 提案する方針

1. `initramfs_skeleton/init`、`buildscripts/post_image.sh`、`S20mountfs`、`S21rootkeys`、`S55sshd` の時系列を `docs/ssh-update-design.md` に記す。既存 updater は rcS の MTD ガードより前に動くことを含める。
2. 元の Atom Cam のユーザー確認済み MAC と SSH host key を登録する手順を定める。既知 IP だけで特定しない。接続先が確定したら read-only で mounts、SD partitions、free space、CPU/MTD flags、利用可能な hash/rename/sync ツールを取得する。設定内容・認証ファイルは採取しない。
3. SD 上の versioned rootfs 選択、boot-success marker、失敗回数、前版復旧の実現性を調べる。bootloader の固定 kernel 名、initramfs 自体の更新、電源断時の FAT/exFAT 永続化を分けて評価する。
4. 「ダウンロード/検証中」「rootfs 切替後」「kernel 切替後」「初回 boot 中」「SSH 再接続不可」の故障表を作り、各点で使う前版と手動 SD 復旧経路を明記する。kernel 二重化ができないならその限界を明記し、rootfs 更新から段階導入する。
5. manifest の対応モデル・version・互換性・hash の照合、署名要否と信頼元、残容量、ロック、staging と commit の区別、SSH 再接続 health check を設計する。カメラが再起動しても鍵/ネットワーク設定を維持する方針を決める。
6. 結果から「initramfs updater」「Mac 配布 CLI」「起動 health/rollback」「SD 復旧試験」を必要に応じて分割し `issues/open` に作成する。各課題に確定したファイル、コマンド契約、電源断時の受け入れ条件を転記する。

## 受け入れ条件

- [x] 実測とソース由来と未確認情報を区別した設計文書がある。
- [x] SSH が戻らない場合も含む復旧手順が記載され、原子性の保証範囲が明確。
- [x] SD と内部 flash の境界が明記され、MTD 書き込みを要求しない。
- [x] 後続実装イシューが作成され、調査文書へ相互リンクされている。

## テスト計画

一時ディレクトリ上で updater の各切替順序を模擬し故障表と照合。実機は識別済み対象へのメタデータ read-only 取得のみ。調査不能項目は未確認と明記し、依存する後続実装を着手可能扱いにしない。

## リスク

単一 kernel ファイルの置換は rootfs A/B だけでは復旧できない。SD ファイルシステムの rename と停電耐性を同一視しない。

## 実装記録（2026-09-08）

`docs/ssh-update-design.md` を pinned source に基づいて更新し、1/2 partition 分岐、rootfs/kernel の逐次 `mv` + `sync`、`switch_root` 後の `/media/mmc`、SD-backed SSH/config 領域、固定 kernel 名による rollback 限界を記録した。実機で未測定の filesystem/layout、bootloader、power-loss durability は明示的に unknown のまま残した。

SSH が戻らない場合は offline SD recovery が必要であり、内部 MTD write を復旧手段にしない。boot-image update は atomic と主張しない。

後続課題を作成した：

- `20260908-atomcam-boot-layout-inspection.md`
- `20260908-initramfs-rootfs-selector.md`
- `20260908-boot-image-deploy-cli.md`
- `20260908-boot-health-rollback.md`
- `20260908-sd-recovery-powerloss-test.md`

これらの hardware-dependent acceptance は real-device evidence が得られるまで未達。boot-image の実機更新/reboot/rollback/power-loss test は今回実施していない。

## 変更履歴

`CHANGES.md` impact: no

## 注記

実装担当の想定：`gpt-5.6-luna`、reasoning effort `max`。`Model` は本文の最終編集者を表すため、担当予定モデルとは区別する。作成環境から正確なモデル識別名を確認できないため `unknown`。実装着手時に自身の識別名へ更新する。

依存：[20260908-sd-release-verification](20260908-sd-release-verification.md)

全体計画：[20260908-flight-roadmap](20260908-flight-roadmap.md)。共通の実装手順、既存資産の保護、未確定条件を着手前に読む。
