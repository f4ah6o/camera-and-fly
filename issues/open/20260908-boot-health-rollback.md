# early boot health と前版 rollback protocol を実装する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08

## 概要

versioned rootfs candidate が正常起動したことを明示的に確定し、失敗回数が上限に達した場合に previous rootfs へ戻す protocol を実装する。

## 問題

SSH 再接続だけを health とすると、sshd より前の失敗を rollback できず、逆に sshd 起動後の主要サービス失敗を成功扱いする可能性がある。固定 kernel 名の失敗は rootfs fallback だけでは復旧できない。

## 実装方針

- initramfs が candidate attempt を記録し、userspace の明示 health action だけが success を確定する。
- health 条件は rootfs mount、必須 init stage、設定 mount、必要な runtime readiness を段階化し、SSH 自体は観測手段の1つに留める。
- success marker は release/operation ID と結び付け、古い marker を別 candidate に流用しない。
- candidate の attempt 上限到達時は previous rootfs を選ぶ。previous 不明/壊れでは推測せず offline recovery を要求する。
- kernel は単一固定名のため、kernel update 後に boot できないケースを「自動 rollback 済み」と扱わない。

## 受け入れ条件

- [ ] fake boot sequence で success/missing-success/old-marker/repeated-failure を再現する。
- [ ] SSH 起動だけでは candidate success にならない。
- [ ] stale/foreign operation marker を拒否する。
- [ ] previous が有効な rootfs failure は規定回数後に fallback できる fixture がある。
- [ ] kernel failure と initramfs failure は offline recovery が必要な未保証ケースとして残る。

## 依存

[20260908-atomcam-boot-layout-inspection](20260908-atomcam-boot-layout-inspection.md)、[20260908-initramfs-rootfs-selector](20260908-initramfs-rootfs-selector.md)
