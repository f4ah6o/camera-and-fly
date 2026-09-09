# Atom Cam 1 × StampFly 閉ループ制御アーキテクチャ

Updated: 2026-09-09

## 目的

Atom Cam 1 を室内固定の外部視覚センサとして使い、Mac が映像から StampFly の位置・向きを推定し、StampFly へ bounded な姿勢・高度指令を返す。StampFly 内部の既存 400 Hz 姿勢安定化ループは置き換えない。

最初の飛行目標は、明示 START 後に低高度で離陸し、固定カメラで機体マーカーを追跡しながら指定位置を保持し、明示 LAND で着陸することとする。

この文書は構成と責務を固定する。実装順と Luna Max 単位の作業は `issues/open/20260909-camera-stampfly-closed-loop.md` を正とする。

## 全体構成

```text
固定 Atom Cam 1
    │ RTSP / WebRTC
    ▼
Mac
    ├─ decoded-frame worker / freshness gate
    ├─ marker detector + calibration
    ├─ PoseObservation (position + yaw)
    ├─ mission supervisor
    ├─ outer position controller
    ├─ bounded control scheduler
    └─ flight-link adapter
          │
          ├─ 机上・safe hardware: USB / CF1
          └─ 自由飛行候補: USB ESP32 gateway → ESP-NOW
                         │
                         ▼
                     StampFly
                     ├─ existing 400 Hz attitude loop
                     ├─ motor control
                     ├─ command watchdog / ownership fence
                     ├─ ToF / IMU / altitude
                     └─ armed / grounded / telemetry
                         │
                         └──────────── telemetry ────────────→ Mac
```

Atom Cam と StampFly は映像データを直接やり取りしない。映像処理は Mac で完結し、StampFly には小さな制御指令だけを送る。

## 役割分担

### Atom Cam 1

- 室内に固定する。
- StampFly または同寸 fixture 上の識別マーカーを観測する。
- ローカル RTSP/WebRTC を低遅延候補とする。
- JPEG snapshot は 2026-09-08 の実測で約 0.203 fps、request p95 約 5.145 秒だったため閉ループ入力には使わない。
- カメラ再接続だけを mission READY / ARM 復帰の契機にしない。

### Mac

Mac は閉ループの外側制御を担当する。

1. decoder worker が bounded latest-frame として最新画像だけを保持する。
2. marker detector と versioned calibration から `PoseObservation` を生成する。
3. freshness、quality、room bounds、marker ID、reprojection error 等を fail closed で検証する。
4. mission target と pose の差を world frame で計算する。
5. yaw を使って world 誤差を StampFly body frame へ変換する。
6. saturation、slew-rate、anti-windup、explicit expiry を持つ roll / pitch / altitude intent を生成する。
7. scheduler が期限切れ intent を送信前に拒否する。
8. flight-link adapter が capability、ownership、sequence、TTL、ACK を保ったまま StampFly へ送る。

画像受信、画像処理、制御指令送信の時刻は分離し、heartbeat を送るだけで古い観測や古い control intent を新しく見せない。

### StampFly

StampFly は内側の高速制御と最後の safety boundary を担当する。

- 既存 400 Hz attitude stabilization を維持する。
- Mac は motor PWM を直接生成しない。
- command watchdog、claim/ownership fence、sequence、stale command rejection を維持する。
- ToF / IMU / altitude は value と validity / age を分離して返す。
- reconnect や通信復帰だけで ARM を復元しない。
- LAND completion は `grounded && !armed` の実 telemetry を必要とする。

## データフロー

### 1. Frame

`RTSP/WebRTC → DecodedFrame`

最低限、stream sequence、receive monotonic、decode-complete monotonic、connection generation を保持する。source PTS/DTS が取得できない backend では unknown のまま扱い、露光時刻として推測しない。

### 2. Vision observation

`DecodedFrame → PoseObservation`

既存 contract は `position_m`、`yaw_rad`、sequence、source/receive freshness、quality、valid/reason を持つ。座標系は `docs/vision-design.md` の `world_frd` / `body_frd` を正とする。

### 3. Position control

`PoseObservation + mission target → ControlIntent`

外側制御器は位置誤差から bounded な姿勢・高度指令を生成する。指令には generated time と valid-until を持たせ、同じ指令を heartbeat で延命しない。

### 4. Flight link

`ControlIntent / mission semantic action → StampFly`

机上試験は USB / CF1 を使う。自由飛行では USB ケーブルを機体につながないため、現時点の優先候補は Mac から USB 接続した ESP32 gateway を経由して ESP-NOW へ送る方式である。最終採用は gateway の latency / loss / reconnect 実測後に決める。

### 5. Telemetry feedback

`StampFly → HealthSnapshot / mission / controller`

altitude、range、IMU、armed、grounded、link ownership 等を Mac に戻す。数値が存在しても `valid=false` または stale なら制御・mission 進行に使わない。

## 二重ループ制御

```text
position target
      │
      ▼
Mac outer loop
position error → world/body transform → roll/pitch/altitude intent
      │
      ▼
StampFly inner loop
existing 400 Hz attitude stabilization
      │
      ▼
motor output
```

カメラの更新周期と StampFly の姿勢制御周期を同一にしない。Mac 側は観測が新しい場合だけ外側 intent を更新し、StampFly はその間も機体内部で姿勢安定化を継続する。

## Mission

初回対象 mission は以下とする。

```text
IDLE
  ↓ explicit START
READY
  ↓ capability + health + fresh pose
ARM
  ↓ explicit TAKEOFF(target altitude)
TAKEOFF
  ↓ airborne feedback
HOLD
  ↓ position target tracking
LAND
  ↓ grounded && !armed
COMPLETE
```

camera loss、stale pose、telemetry invalid、link loss、deadline miss、capability mismatch は mission fault として扱う。再接続・frame復帰・process restart だけで ARM / TAKEOFF / HOLD を自動再開しない。

## Safety boundary

- `camfly-safe` では全 motor PWM をゼロ固定する。
- software test → fake transport → `camfly-safe` zero-output hardware → no-prop → staged flight の順を崩さない。
- hardware acceptance を unit test で代用しない。
- vision invalid/stale 時に新しい非ゼロ intent を作らない。
- controller producer 停止時に最後の intent を延命しない。
- link reconnect 時に以前の session、ARM、TAKEOFF、ALT target を復元しない。
- USB と ESP-NOW の controller ownership を同時に成立させない。
- device-specific IP、MAC、host key、credential、raw private evidence を tracked file に残さない。

## 現在の実装状態

2026-09-09 時点で以下は software 実装済みである。

- bounded host scheduler と expiring intent
- mission FSM の explicit START / TAKEOFF / LAND semantics
- CF1 protocol hardening と 250 ms firmware watchdog
- zero-output `camfly-safe` dry-run path
- decoded-frame bounded adapter と synthetic tests
- pure `PoseObservation` / freshness / quality gate
- deterministic G1 dynamics simulator
- telemetry validity の software contract と parser

一方、以下は実機または後続 integration が未完了である。

- RTSP/WebRTC live qualification と exposure-to-receive 測定
- marker detector / calibration と live pose accuracy qualification
- metre altitude / TAKEOFF / LAND adapter
- ESP-NOW gateway / host transport / receiver と link qualification
- outer position controller の実装・G1評価
- live vision → controller → scheduler / flight adapter の runtime integration
- `camfly-safe` での camera-to-StampFly closed-loop zero-output qualification
- flight build、no-prop、G4/G5 staged flight

## 実装順

詳細は `issues/open/20260909-camera-stampfly-closed-loop.md` と `issues/open/README.md` を参照する。大きな統合作業を1回で実装せず、Luna Max は ready な子 issue を1件だけ選ぶ。
