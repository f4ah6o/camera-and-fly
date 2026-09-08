# CF1 USB RX flood / TX backpressure 時の 400Hz watchdog到達性を safe実機で確認する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: hardware-validation
Luna-Ready: yes-if-safe-hardware
Branch: main

## 概要

CF1 hardeningの残acceptanceとして、RX floodとUSB TX backpressure相当の負荷中でも`usb_bridge_poll()`がboundedでwatchdog判定へ戻ることを`camfly-safe`で確認する。

## この issue だけでやること

- 明示StampFly portと`camfly-safe` identityを確認する。
- overlong/大量invalid/大量SETをbounded rateで送るhost probeを用意する。
- TXをhost側で読まない/遅く読む条件を作り、400Hz loop/tick/watchdog eventを観測する。
- 全SET zero、ARM 0を維持する。
- native testでは証明できない実USB scheduling部分だけ測定する。

## 対象外

非ゼロSET、ARM、motor output、watchdog延長、flight build。

## 受け入れ条件

- [ ] RX flood中もbounded pollからwatchdog判定へ戻る証拠がある。
- [ ] TX backpressure相当でも400Hz loopを無期限blockしない。
- [ ] 全hardware SET zero、ARM 0、safe PWM zeroを確認する。
- [ ] tick/overrun/command/watchdog結果をdevice identityなしで保存する。
- [ ] 不成立ならhardening親issueのacceptanceをuncheckedのままにする。

## Luna Max 着手契約

safe hardware identity確認なしでは実施しない。負荷試験のためにARM/non-zeroを使わない。

## 依存

[20260908-cf1-protocol-hardening](20260908-cf1-protocol-hardening.md)
