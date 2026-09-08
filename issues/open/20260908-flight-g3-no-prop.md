# flight build の G3 no-propeller preflight を実機で検証する

Status: open
Model: GPT-5.6 Sol
Created: 2026-09-08
Updated: 2026-09-08
Kind: hardware-validation
Luna-Ready: blocked-on-preflight-gate
Branch: main

## 概要

flight build とpreflight gate完成後、プロペラを外した機体だけを対象に semantic action/output bounds/abort/reconnect-no-rearm を実機確認する。

## この issue だけでやること

- operatorがpropeller removalとtarget deviceを明示確認する。
- flight build identity/capabilityとpreflight全項目を記録する。
- action pathのoutput bounds、abort、disconnect/reconnect、no automatic re-armを確認する。
- known-good `camfly-safe` への戻し方を実機確認する。

## 対象外

プロペラ装着、離陸、自由飛行、G4/G5。

## 受け入れ条件

- [ ] propeller removed をoperator確認したrunだけ実施する。
- [ ] preflight欠落時にARM/TAKEOFFへ進まない。
- [ ] output bounds/abort/reconnect-no-rearmがログで確認できる。
- [ ] `camfly-safe`へ戻してsafe identity/PWM zero contractを確認する。
- [ ] device-specific identityをtracked fileへ残さない。

## Luna Max 着手契約

物理条件が未確認なら実施しない。flight成功とは呼ばない。プロペラ装着状態へ進めない。

## 依存

[20260908-flight-preflight-gate](20260908-flight-preflight-gate.md)、[20260908-flight-build-environment](20260908-flight-build-environment.md)
