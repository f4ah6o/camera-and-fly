# 読み取り専用 SD イメージの再ビルドとリリース検証を再現可能にする

Status: open
Model: unknown
Created: 2026-09-08
Updated: 2026-09-08
Branch: codex/20260908-sd-release-verification

## 概要

読み取り専用パッチを含む成果物を検査し、ハッシュと検証結果を機械可読な manifest に保存する。

## 背景

対象は [build.sh](../../atomcam-sd/build.sh) と `atomcam-sd/patches/`。現在 `artifacts/` の ZIP は厳格化した MTD ガードより前に作られている。ビルドは既存 4 GiB Lima と保持された builder を利用する。

## 問題

固定ログ名が既存成果物と衝突し再ビルドできない。失敗時の staging archive 回収は生成後のフラグ設定に依存する。ZIP CRC だけでは読み取り専用改修が反映されたことを保証できない。

## 目標

新旧成果物を保持したまま再ビルド・検証でき、後続の SSH 更新が検証済み bundle を識別できる。

## 対象外

SD 書き込み、SSH 配布、内部フラッシュ書き込み、カメラの起動試験。

## 提案する方針

1. `build.sh` の出力・ログを同じ release ID ごとの新規ディレクトリへ揃える。既存の固定名との互換性を README に記載し、既存ファイルは移動しない。`..`、symlink 経由の保存先逸脱を拒否する。
2. 同じ vendor/build tree を使う同時ビルドをロックで拒否する。INT/TERM は成功扱いにせず、元のシグナルに応じた非ゼロ終了とする。生成途中の staging archive も、その実行が所有するものだけ回収する。
3. `atomcam-sd/verify_release.py` を追加する。ZIP の許可ファイル4つ、重複名、絶対/上位パス、symlink、CRC、サイズ上限、kernel uImage/SquashFS ヘッダを検査する。authorized_keys の中身をログに出さない。
4. builder 内の展開済み rootfs と実際の kernel `.config` を検査し、S16 更新停止、S20 ro mount、rcS ガード、flash_erase stub、カーネル driver の writeable bit 除去を証拠として記録する。ソースだけの確認を「バイナリ検証済み」としない。検査の使うパスは既存ビルドを読んで特定する。
5. manifest v1 に source commit、各パッチ SHA-256、builder digest、各ファイル SHA-256/size、検査結果、release ID を記録。秘密鍵は含めない。ハッシュは破損検知であり配布元の認証ではないと明記する。
6. ガードの既存7テストを含めて実行し、別名でビルドを1回実行して manifest を生成。VM を利用できない場合は試行ログと再開コマンドを残して未完了とする。

## 受け入れ条件

- [ ] 既存 ZIP/ログが不変で、新しい manifest と ZIP が対応する。
- [ ] 出力衝突、ビルド失敗、シグナル中断、同時実行で既存成果物・submodule を壊さない。
- [ ] 破損・重複・path traversal ZIP は失敗し、不足した検証を成功表示しない。
- [ ] 新しい読み取り専用ガードを含む再ビルド結果の検証証拠がある。

## テスト計画

`python3 -m unittest discover -s atomcam-sd/tests -v`。ZIP fixture による verifier テストと fake limactl による失敗/中断テストを追加。`bash -n atomcam-sd/build.sh`。最後に実ビルド1回と `git -C vendor/atomcam_tools status --short` を確認。

## リスク

ソースとビルドキャッシュの不一致、数十分規模のビルド、VM 容量不足。展開検査は生成物を対象にし、ホストで不明なバイナリを実行しない。

## 変更履歴

`CHANGES.md` impact: yes

項目案：読み取り専用パッチを含む成果物を検査し、ハッシュと検証結果を機械可読な manifest に保存する。

実装時に既存の変更履歴規約を確認する。現時点では `CHANGES.md` は存在しないため、計画作成だけを理由に新設しない。

## 注記

実装担当の想定：`gpt-5.6-luna`、reasoning effort `max`。`Model` は本文の最終編集者を表すため、担当予定モデルとは区別する。作成環境から正確なモデル識別名を確認できないため `unknown`。実装着手時に自身の識別名へ更新する。

依存：なし。

全体計画：[20260908-flight-roadmap](20260908-flight-roadmap.md)。共通の実装手順、既存資産の保護、未確定条件を着手前に読む。
