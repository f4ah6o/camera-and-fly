# SILS camera/perception 閉ループ（simulator STATE → 実pixel → PoseObservation → outer controller → emu_vehicle）を実装する（Milestone B）

Status: completed-scope (Milestone B PASS — simulation-only)
Model: deepseek-v4.1-flash
Created: 2026-09-15
Updated: 2026-09-15
Kind: implementation
Luna-Ready: completed-scope
Branch: main

## Luna Max 着手契約

この issue は simulation-only。Milestone A (`20260914-stampfly-sils-true-closed-loop.md`) の `host/stampfly_sils.py`、`host/flight_sim.py`、`host/stampfly_sim.py` の挙動を変更しない。raw simulator STATE を controller に直接渡さず、偽 `PoseObservation` を注入せず、detector を bypass しない。実カメラ calibration / live qualification / flight build / ARM / 非ゼロ throttle は扱わない。外部 `/Users/fu2hito/src/stampfly_ecosystem` は read-only とし、変更しない。

## 概要

Milestone A は host → SILS RC → firmware/plant → structured STATE → host の反復閉ループを確立した（PASS）。本 issue はその上に narrowest な **camera/perception 閉ループ** を追加する:

```
simulator STATE
  → 決定論的 frame (実 grayscale pixel bytes)
  → detector (image moments) が pixel から production PoseObservation を生成
  → host/vision.py の freshness/quality gate
  → outer position controller (world_frd → body_frd, saturation, slew, TTL)
  → immutable で expiring な host/control_loop.ControlIntent
  → 既存 ControlScheduler / SilsControlAdapter
  → installed emu_vehicle
  → fresh STATE → 次 frame → 次 decision
```

実 detector/calibration の前提（`20260908-vision-detector-calibration.md`、`20260908-vision-live-qualification.md`）は未完了のため、決定論的な **simulation-only camera/perception seam** を追加する。これは生の pixel を消費し、同じ `PoseObservation` contract を emit するが、実カメラ calibration や精度 qualification を主張しない。

## 依存監査

| レイヤ | current（既存） | missing（未完了） | reusable（再利用） |
|---|---|---|---|
| frame source | `host/camera_stream.py` の `DecodedFrame` / `LatestDecodedFrameSlot` / `DecoderWorker`、`host/camera_worker.py` | 実 RTSP/WebRTC live qualification と exposure-to-receive 測定 | `DecodedFrame` contract をそのまま simulation frame に使用 |
| perception | `host/vision.py` の `PoseObservation` / gate。実 detector library は未選定 | `20260908-vision-detector-calibration.md`（detector/calibration）、`20260908-vision-live-qualification.md` | `PoseObservation` contract と `evaluate_observation` を変更なしで消費 |
| calibration / coordinate frame | `host/vision.py` の `world_frd` / `body_frd`、`docs/vision-design.md` | versioned calibration artifact loader、実測 transform/sign | 座標系定義と sign 規約を simulation camera model に流用 |
| PoseObservation | `host/vision.py`（実装済み・PASS） | 実 pose 精度 evidence | そのまま production contract として emit |
| outer controller | 本 issue の `host/sim_camera_perception.py::OuterPositionController`（simulation-only 最小実装） | `20260908-outer-position-controller.md`（実 gain、G1 評価） | `ControlIntent` の expiry/saturation semantics |
| ControlIntent | `host/control_loop.py`（実装済み・PASS） | なし | そのまま使用。heartbeat で延命しない |
| scheduler | `host/control_loop.py::ControlScheduler`（実装済み・PASS） | なし | そのまま使用。期限切れ intent は送信前に fault |
| SILS transport | `host/stampfly_sils.py::StampFlySimTransport` / `SilsControlAdapter`（Milestone A PASS） | なし | direct `emu_vehicle` argv/env/STATE parser をそのまま使用 |
| simulator state → camera feedback | 本 issue の `SimCameraModel` / `render_marker_frame`（simulation-only） | 実 camera が simulator state を観測する物理経路（実機 fixture / marker） | SILS の `SilsTelemetry` (metres/radians) を唯一の入力にする |

未完了 prerequisite は complete にしない。`Luna-Ready` は blocked のまま。

## アーキテクチャ

```
host/sim_camera_perception.py
  SimCameraModel                # simulation-only downward pinhole + marker mount model
  render_marker_frame           # STATE -> actual grayscale pixel bytes (DecodedFrame)
  detect_pose                   # image-moment detector -> host.vision.PoseObservation
  SimulatedDownwardCamera       # latest-value simulation frame source
  OuterControllerConfig /
  OuterPositionController       # world_frd error -> body_frd -> bounded expiring ControlIntent
  SimStateSource (Protocol)     # read_telemetry/is_ready/clock (StampFlySimTransport satisfies)
  SimVisionControlRuntime       # S0 -> F0 -> P0 -> C0 -> S1 -> F1 -> P1 -> C1 + fault latch
  LoopIteration / SimCameraLoopResult  # bounded JSON/JSONL evidence
  run_sils_camera_smoke         # real installed emu_vehicle entrypoint
  main()                        # resolve / camera-smoke
```

`host/vision.py`、`host/control_loop.py`、`host/mission.py`、`host/stampfly_sils.py` は変更しない。本 issue は新規 module と test、issue のみを追加する。

## 安全境界

- 起動は Milestone A の許可済み direct emu argv のみ。`sf sils fly` は起動しない。`shell=True` / `os.system` / `os.popen` を使わない。
- first command は Milestone A 契約どおり `rc 2048 2048 2048 2048`（transport.start()）。
- `arm` / `land` / `disarm` wire command を送らない。serial/USB device を開かない。hardware discovery / flash を行わない。
- `SilsControlAdapter` により throttle は常に 0.0（ADC center）、stick は `max_stick` bounded。mode は ANGLE/MANUAL のみ。
- stale/invalid/no-result/duplicate/rollback/generation-rollover の後で新規非ゼロ intent を生成しない。invalid 時は explicit zero intent（明示 safe stop）を publish する。
- duplicate/rollback frame sequence、generation rollover、simulator STATE timeout/process exit は fault-latch し、`scheduler.fault()` と best-effort recenter で停止する。
- 古い intent を heartbeat で延命しない。新規 intent は新規の fresh pose からのみ生成する。
- reconnect/restart で旧 mission/control state を自動復元しない。runtime は毎回 `MissionSupervisor` を新規生成し、operator START を要求する。
- `calibration_valid=False`、`capabilities=frozenset()`、`armed=False` のまま。mission は PREFLIGHT に留まり ARM しない。

## simulation camera/perception contract

- 入力: `SilsTelemetry`（`sim_time`, `altitude_m`, `roll_rad`, `pitch_rad`, `yaw_rad`, `receive_sequence`, `received_monotonic`）。角度は transport 境界で degrees→radians 済み。
- 出力: `host.camera_stream.DecodedFrame`（`gray`, `width×height`, `received_monotonic`, `decode_complete_monotonic`, `decoder_generation`）。
- 検出: 閾値以上の pixel の image moments から marker centroid / 面積 / intensity-ramp 方向を測定し、`PoseObservation(sequence, received_monotonic, source_timestamp_monotonic, x_m, y_m, z_m, yaw_rad, marker_count, reprojection_error_px, confidence, world_frame, body_frame)` を生成する。
- 決定論: 同一 STATE・同一 frame_id で同一 bytes。frame_id/generation は sub-threshold watermark として pixel に含め、連続 frame の bytes が異なることを保証する。watermark は threshold 以下なので pose 測定に影響しない。
- これは実 fiducial library / 実 calibration ではない。`provider=stampfly_ecosystem`, `evidence_kind=simulation`, `simulation=true`, `flight_qualified=false`。

## 座標・時刻・freshness semantics

- `world_frd`: +X forward, +Y right, +Z down。`body_frd` 同符号。`sim_camera_downward`: 固定下向き pinhole。image u→world +Y、v→world +X。
- marker mount model（simulation-only）: `x_m = lever_arm·sin(pitch)`, `y_m = lever_arm·sin(roll)`, `z_m = -(altitude + marker_altitude_offset_m)`, yaw は image 内 intensity ramp。水平位置が SILS STATE に無いための明示的 simulation 仮定であり、実機の主張ではない。
- 時刻: frame capture (`received_monotonic`) と decode complete (`decode_complete_monotonic`) を分離。pose の source は capture、receive は decode complete。
- `ControlIntent.generated_monotonic` / `valid_until_monotonic` は controller が一度だけ設定し、scheduler は書き換えない。
- sequence: frame_id / perception sequence / command sequence / upstream `receive_sequence` / `sim_time` を evidence に別々に記録。duplicate/rollback は fail closed。
- freshness は heartbeat で延長しない。stale pose は gate で invalid → explicit zero intent。

## 受け入れ条件

実装チェック（このセッションで追加、code review 可）:

- [x] raw STATE → pixel frame → detector → `PoseObservation` の seam を追加（STATE 直結や pose 注入をしない）。
- [x] `host/vision.PoseObservation` と `evaluate_observation` を変更せず消費。
- [x] world/body/camera 座標と metres/radians を明示。degrees は upstream 境界で変換済み。
- [x] expiring bounded `ControlIntent` を既存 `ControlScheduler` / `SilsControlAdapter` へ接続。
- [x] first command `rc 2048 2048 2048 2048` を維持。ARM/land/disarm/serial/USB 無し。
- [x] fault matrix の deterministic tests を追加（下記 test matrix）。
- [x] duplicate/rollback/generation rollover/STATE fault で latch + safe stop。
- [x] stale/invalid/no-result で explicit zero intent、旧非ゼロ intent の延命なし。
- [x] restart で旧 command sequence / mission state を復元しない。
- [x] `host/flight_sim.py` / Milestone A 既存挙動 intact。

実行・実測チェック:

- [x] `.venv/bin/python -m unittest host.tests.test_sim_camera_perception -v`: **PASS 31/31**。
- [x] `.venv/bin/python -m unittest host.tests.test_stampfly_sils -v`: **PASS 55/55**（Milestone A regression）。
- [x] `.venv/bin/python -m unittest host.tests.test_stampfly_sim -v`: **PASS 33/33**。
- [x] `.venv/bin/python -m unittest host.tests.test_vision -v`: **PASS 5/5**。
- [x] AtomCam fixture を除く host suite: **PASS 199/199**（16 modules）。
- [x] real installed `emu_vehicle` camera-smoke: **PASS**。4 iterations / fault 0 / `STOPPED`。
- [x] `SILS_EMU_REALTIME=1` / `SILS_EMU_RC_STDIN=1` を設定する既存 Milestone A transport で direct `emu_vehicle` を実行し、first command は `rc 2048 2048 2048 2048` のまま。
- [x] bounded JSONL evidence: `artifacts/20260915-stampfly-sils-camera-perception-smoke.jsonl`（4 lines / 6598 bytes）。
- [x] S0→F0→P0→C0→fresh S1→F1→P1→C1 を sequence / simulator `t` / frame fingerprint / wire sequence で実測。
- [x] `git diff --check`: **PASS**。

## test matrix（deterministic fake、実行 PASS）

| Case | 期待 |
|---|---|
| render→detect round trip | x/y/z/yaw が pixel から復元（simulation tolerance） |
| successive frames differ / pose stable | bytes は異なる、pose はほぼ同一 |
| watermark sub-threshold | watermark pixel ≤ threshold（pose 非干渉） |
| malformed frame (format/dims/length) | `MalformedFrameError` → zero intent |
| malformed pose (nonfinite) | production `PoseObservation` contract が constructor boundary で reject |
| empty marker | `DetectorNoResult` → zero intent |
| frame missing | `frame_missing` → zero intent |
| camera producer stall | `camera_stall` → zero intent |
| stale frame/pose | gate invalid → zero intent |
| frame sequence duplicate / strict rollback | fault latch、追加 command なし、recenter |
| frame generation change | fault latch、追加 command なし、recenter |
| detector no-result | explicit zero intent |
| controller world→body transform | known vector で sign 検証 |
| controller saturation | bounded ≤ max_output、saturated flag |
| controller slew | Δ ≤ slew_rate·dt |
| out-of-bounds pose | intent 返さず zero |
| intent generated/valid_until | 生成時に固定、heartbeat で不変 |
| intent expiry | scheduler fault latch |
| simulator STATE timeout | `state_next:SilsTimeout` latch |
| simulator process exit | `state_next:SilsProcessExit` latch |
| initial STATE failure | `state_initial:...` latch |
| restart | command sequence 1 から、mission PREFLIGHT、復元なし |
| fake emu integration | first command `rc 2048 2048 2048 2048`、arm/land/disarm 無し、S1>C0 |
| module source scan | shell/serial/ARM surface 無し |

## 実カメラ境界（real-camera boundary）

本 issue の camera/perception は **simulation-only** である。以下は未完了で、本 issue の対象外:

- 実 Atom Cam 1 の live stream 採用と exposure-to-receive 測定
- marker detector library の選定 / version 固定
- versioned calibration artifact（intrinsics/extrinsics）と実測 sign test
- 実 pose 精度・dropout・latency qualification
- `camfly-safe` zero-output hardware qualification

したがって本 issue の完了（仮に tests が PASS しても）は実カメラ calibration 済み・flight-ready を意味しない。

## 実行記録（2026-09-15）

- 実装: `host/sim_camera_perception.py`、`host/tests/test_sim_camera_perception.py`、本 issue を追加。
- 既存変更の保全: 既存 tracked file は編集していない（README index 更新を除く）。Milestone A の `host/stampfly_sils.py` 等は未変更。
- deterministic tests: new camera/perception tests **31/31 PASS**、Milestone A `test_stampfly_sils` **55/55 PASS**、`test_stampfly_sim` **33/33 PASS**、`test_vision` **5/5 PASS**。AtomCam localhost fixture を除く host suite は **199/199 PASS**。
- real SILS integration: installed `/Users/fu2hito/src/stampfly_ecosystem/simulator/sils/build/emu_vehicle` を direct 起動する Milestone A transport を再利用し、4-iteration camera-smoke は **PASS**。`faults=[]`, `final_state=STOPPED`, `mission_state=PREFLIGHT`, `flight_qualified=false`。
- causal evidence iteration 1→2: `S0(t=0.000, receive=1)` → `F0(frame=1, fingerprint=ec938024361f0adf, generated_from_sim_t=0.000)` → `P0(perception=1, valid=true)` → `C0(intent=1, wire=1, pitch=0.05, throttle=0)` → fresh `S1(t=0.033, receive=2)` → `F1(frame=2, fingerprint=c865586eda417638, generated_from_sim_t=0.033)` → `P1(perception=2, valid=true)` → `C1(intent=2, wire=2, pitch=0.05, throttle=0)`。
- continued evidence: simulator `t` は `0.000 → 0.033 → 0.066 → 0.099 → 0.132`、receive sequence は `1 → 5`、frame/perception/command sequence は `1 → 4`。4 frame fingerprint は全て異なる。pose age は約 2.6–3.4 ms、全 observation valid、controller/scheduler/process fault 0。
- physical plant response: **not observed**。PREFLIGHT / non-arming のため telemetry pose は静止し、frame byte 差には sub-threshold frame identity watermark も含む。したがって「command が物理機体を動かした」とは主張しない。検証したのは command 後の fresh real STATE が次の camera frame / perception / decision に使われる data/control feedback loop である。
- evidence: `artifacts/20260915-stampfly-sils-camera-perception-smoke.jsonl`。各 row に simulator/receive sequence、frame source `sim_t`、frame/perception sequence、pose/age/validity、controller decision、intent deadline、wire command sequence、next STATE、next frame/perception linkage、fault/drop reason、simulation-only provenance を保存する。
- Milestone A の PASS は維持。実 detector/calibration/live-camera qualification issue は未完了のままで、complete 扱いにしない。

## 判定

**Milestone B: COMPLETE / PASS (simulation-only)**。actual pixel frame → perception → production `PoseObservation` → bounded expiring controller intent → existing scheduler/SILS adapter → real installed `emu_vehicle` → fresh STATE → next pixel frame/perception/decision を実測した。これは real Atom Cam / real calibration / flight qualification の完了を意味しない。それらの unfinished prerequisite issue は open のまま維持する。

## 対象外

- 実機 camera / calibration / live qualification
- ARM / takeoff / motor / flight build / flight qualification
- `host/stampfly_sils.py` / `host/flight_sim.py` の挙動変更
- `/Users/fu2hito/src/stampfly_ecosystem` の変更

## 注記

関連: [20260914-stampfly-sils-true-closed-loop](20260914-stampfly-sils-true-closed-loop.md)（Milestone A, PASS）、[20260909-vision-control-runtime-integration](20260909-vision-control-runtime-integration.md)、[20260908-outer-position-controller](20260908-outer-position-controller.md)、[20260908-vision-detector-calibration](20260908-vision-detector-calibration.md)、[20260908-vision-live-qualification](20260908-vision-live-qualification.md)、[20260909-camera-stampfly-closed-loop-safe-qualification](20260909-camera-stampfly-closed-loop-safe-qualification.md)。全体計画: [20260908-flight-roadmap](20260908-flight-roadmap.md)。
