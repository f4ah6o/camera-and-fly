# installed StampFly Ecosystem SILS を camera-and-fly の実閉ループへ接続する（Milestone A）

Status: open
Model: deepseek-v4.1-flash
Created: 2026-09-14
Updated: 2026-09-15
Kind: implementation
Luna-Ready: yes
Branch: main

## Luna Max 着手契約

この issue は simulation-only。`host/stampfly_sim.py`（既存の片方向 batch wrapper）と `host/flight_sim.py`（既存の決定論的 plant）は挙動変更しない。新規の simulation-only interactive transport とその fake tests、必要な index/README/CHANGES のみを触る。実機 serial/USB/ARM/takeoff/motor は一切扱わない。外部 `/Users/fu2hito/src/stampfly_ecosystem` は通常 read-only とし、2026-09-15 の real verification では official `sf sils build --target vehicle` が生成する `simulator/sils/build/` 以下の build artifact だけを許可した。tracked upstream source は変更しない。

## 概要

既存の `sf sim headless -> .sflog.zip` は「片方向の batch evidence」であり、command → plant → telemetry の閉ループではない。本 issue は installed SILS の emulator process seam（`emu_vehicle` の stdin `rc`/`quit` 行入力と stdout `STATE ...` 行出力。upstream は `arm`/`land`/`disarm` も受けるが、本 transport は一切送らない）へ接続し、narrowest robust vertical slice として Milestone A を実装する。upstream `sf sils fly` は `isatty()` ガードのため pipe seam には使えない。

- Milestone A（本 issue）: host → SILS RC → firmware/plant → structured STATE → host の反復閉ループ。telemetry の freshness/monotonicity/schema を fail-closed に検証する。
- Milestone B（対象外）: camera → vision → pose → outer controller の閉ループ。simulator state が実際に vision の観測へ影響する場合のみ成立し、本 issue では主張しない。

## 実 installed seam の証拠（source 由来）

以下は installed StampFly Ecosystem source から確認済みの証拠。作業後の独立検証でも `/Users/fu2hito/src/stampfly_ecosystem` を read-only で再読取し、source marker 検証が PASS することを確認した。推測ではない。

- `simulator/sils/README.md`: `sf sils fly` は未改変の real `emu_vehicle` を `SILS_EMU_REALTIME=1` + `SILS_EMU_RC_STDIN=1` で起動する。
- `lib/sfcli/commands/sils.py::run_fly()`: `sys.stdin.isatty()` を検査し、非対話 terminal では exit 1 + `[ERROR] `sf sils fly` requires an interactive terminal` で失敗する。stdin は raw keyboard input であり pipe ではない。よって `sf sils fly` 自身はこの transport の pipe seam ではない。**upstream がどのように emu を起動するかの evidence** ではある。
- `lib/sfcli/commands/sils.py::run_fly()` が起動する実 process seam（非 Windows）: `bd=_build_dir()` = `<root>/simulator/sils/build`、`exe=bd/'emu_vehicle'`、model=`<root>/simulator/sils/models/stampfly.xml`、env=`os.environ` + `SILS_EMU_REALTIME=1` + `SILS_EMU_RC_STDIN=1`、argv=`[str(exe), str(model), str(int(duration * 1e6))]`。
- `simulator/sils/devices/rc_stdin.cpp`: stdin は行指向で `rc <roll> <pitch> <yaw> <throttle>`、`arm`、`land`/`disarm`、`quit` を受け付ける。ADC 入力は 0..4095 に clamp、center 2048。`_fly_adc(0)` は throttle を含む全4軸を neutral（ADC ~2048）へ写し、rc_stdin 初期 `g_thr` は `kAdcCentre`。
- `simulator/sils/devices/rc_stdin.cpp` は保持した stick 値を 50 Hz で `sils::inject_rc()` へ渡し、`scenario_inject.cpp::inject_rc()` は実 firmware と同じ宛先形式の 14-byte ControlPacket を作って host ESP-NOW seam (`sils_espnow_deliver`) へ注入する。
- `simulator/sils/CMakeLists.txt` の `emu_vehicle` target は `${EMU_VEHICLE_SRCS}`（vehicle firmware sources）、`virtual_board.cpp`、`scenario_inject.cpp`、`rc_stdin.cpp`、`plant/plant.cpp` を同一 executable にリンクする。
- `simulator/sils/emu/emu_main.cpp`: realtime モードは ~30 Hz で次を出力する。
  `STATE t=%.3f alt=%.3f roll=%.2f pitch=%.2f yaw=%.2f mode=%s:%s%s vbatt=%.2f`
  `t` は virtual scheduler time（秒）で、authoritative な monotonic simulator timestamp。STATE 値は real firmware が publish する estimate/system_mode/power topic 由来。`on_advance` は `sils_board_step_plant(dt)` で plant physics を進めた後に RC stdin tick / STATE 出力を行う。`roll/pitch/yaw` は名前に単位が無いが **degrees**（`e.* * kRad2Deg`）で出力される。
- `lib/sfcli/commands/sils.py`: `_fly_parse_state()` がこの `STATE` channel を、`_fly_stdout_reader()` が log 出力と分離して parse する。ここが実 interactive seam。
- HUD には upstream の explicit sequence field が無い。upstream が供給していない sequence を捏造しない。local receive sequence は transport metadata としてのみ保持し、upstream `t` の strict monotonicity と host receive time/staleness を別途検証する。

### 直接 emu_vehicle を採用する理由

`sf sils fly` は `isatty()` ガードのため `subprocess.PIPE` では動かない。upstream source から確認できる安定 executable/model/env path と argv shape を使い、`sf sils fly` が内部で起動する `emu_vehicle` を直接 `shell=False` で起動する。起動前に built executable/model の存在と、read-only source の seam marker（`SILS_EMU_RC_STDIN`、`rc` line format、unique `STATE t=` format）を検証し、不一致なら protocol を推測せず `SilsUnsupportedBuild` で fail closed する。`ecosystem_root` は explicit `--root`/`--ecosystem-root`、`STAMPFLY_ECOSYSTEM_ROOT`、狭い default `~/src/stampfly_ecosystem` の順で解決し、存在確認と解決 source を diagnostics に明示する。`sf` 自体は不要で、serial/USB/hardware path は持たない。

## アーキテクチャ

```
host/stampfly_sils.py
  SilsProcess (Protocol)            # write_line / read_line / poll / terminate / kill / wait / close_streams
  PopenSilsProcess                  # subprocess.Popen(shell=False, stdin/stdout/stderr=PIPE), selectors, nonblocking
  SilsRootResolution / resolve_sils_root / verify_sils_source_markers   # explicit/env/default root + artifact + seam marker 検証
  SilsInvocation / build_sils_emu_argv / sils_emu_env
  RcCommand                         # ADC 0..4095, center 2048; serialize -> "rc r p y t"; safe()==neutral()
  SilsTelemetry / parse_state_line  # strict schema, finite checks, mode colon check, degrees -> radians
  StampFlySimTransport              # start / send_rc / read_telemetry / step / close + fail-closed state machine
  SilsHealthConfig / telemetry_to_health   # 既存 mission.HealthSnapshot への翻訳（並行 safety model を作らない）
  SilsControlAdapter                # 既存 control_loop.ControlTransport protocol 実装（throttle 0 -> center、ARM 無し）
  SilsClosedLoopDriver              # ControlScheduler + MissionSupervisor を回す simulation-only driver
  main()                            # resolve (read-only, default) / smoke (explicit start)
```

### 安全境界

- 起動は許可された direct emu argv（`[<root>/simulator/sils/build/emu_vehicle, <root>/simulator/sils/models/stampfly.xml, <duration_us>]`）のみ。`sf sils fly` は起動しない。`shell=True` / `os.system` / `os.popen` を使わない。
- 起動前に built executable/model の存在と seam marker を検証し、不一致は `SilsUnsupportedBuild` で fail closed。
- serial/USB device を開かない。`host/stampfly.py` の ARM/disarm/control を呼ばない。
- 送信する RC は `rc ...` 行のみ。`arm`/`land`/`disarm` は絶対に送らない。`start()` は最初に non-arming safe center `rc 2048 2048 2048 2048` を送る。`close()` は `quit` のみを送る。
- `RcCommand.safe()` は upstream neutral（全4軸 center 2048）。normalized throttle `0.0` は ADC center 2048 に写す。driver は非 ARM の bounded stick perturbation のみ。
- 例外: process exit / broken stdin / timeout / stale / malformed / missing telemetry は fault latch → 以降の送信を拒否。

### fail-closed 検証

- root: explicit `--root`/`--ecosystem-root` -> `STAMPFLY_ECOSYSTEM_ROOT` -> `~/src/stampfly_ecosystem`。解決 source と存在確認を diagnostics に明示。
- artifacts: `emu_vehicle` (exists + executable) と `stampfly.xml` (exists) を起動前に検証。
- seam markers: read-only source の `SILS_EMU_RC_STDIN`、`rc` line format、unique `STATE t=` format を検証。
- schema: `STATE` の key set/順序/値域を regex で厳格に parse。malformed は fault。角度は degrees -> radians 変換。
- monotonicity: upstream `t` は strict 増加を要求。duplicate / non-advancing は fault。
- receive sequence: local transport metadata として単調増加を保持（upstream 由来とは扱わない）。
- staleness: `received_monotonic` と injectable clock による host receive age。`stale_timeout` 超過で fault。
- process/pipe: `poll()` の exit 検知、stdin write の OSError/BrokenPipe 検知、read timeout。

### reconnect / restart 方針

upstream の reconnect/restart semantics をこの環境で検証できないため、自動 reconnect は実装しない。fault は latch され、`close()` で bounded に終了する。再実行は新しい transport instance を明示的に作る。これは「deterministic で安全と確認できない限り fail closed」の方針に従う。

## 受け入れ条件

- [x] installed seam が source 証拠（上記）に基づいて文書化されている（推測でない）。`sf sils fly` は pipe seam ではなく upstream の起動 evidence、直接 `emu_vehicle` が実 process seam と明記。
- [x] simulation transport に hardware serial/USB/ARM 経路が無い。
- [x] direct argv の exact shape、source marker fail-closed、missing emu/model fail-closed を test。
- [x] upstream `t` monotonicity + local receive sequence/staleness を fail-closed に検証。
- [x] missing/malformed/stale telemetry → safe fault/stop。
- [x] subprocess loss/broken pipe/timeout → safe fault/stop。
- [x] deterministic fake tests（parser degrees->radians、command serialization/range、monotonic t regression、duplicate/non-advancing、stale/missing、malformed、process exit、broken pipe、timeout、close、provenance）。
- [x] real SILS integration smoke: **PASS**。installed `/Users/fu2hito/src/stampfly_ecosystem/simulator/sils/build/emu_vehicle` を使用し、4 iterations で `STATE` receive sequence 1→5、sim time 0.000→0.132 s、bounded roll ADC 2559 を各 iteration で送信、各送信後の fresh STATE から次 decision を再計算した。fault 0。
- [x] real SILS bounded extension: **PASS**。同じ non-arming 条件で20 iterations、receive sequence 1→21、sim time 0.000→0.660 s、command count 21、fault 0。roll/pitch/altitude の物理変化は観測されず、姿勢変化を PASS 根拠としては主張しない。
- [x] Milestone B（camera/perception 閉ループ）を主張しない。
- [x] `host/flight_sim.py` intact。
- [x] telemetry-validity 関連の既存ロジックを弱めない。
- [x] provenance は全箇所 `provider=stampfly_ecosystem`、`evidence_kind=simulation`、`simulation=true`、`flight_qualified=false`。

## テスト計画

`host/tests/test_stampfly_sils.py`（deterministic fake + temp root fixtures）:

- `parse_state_line` の valid / malformed / mode / finite / degrees->radians（STATE roll=90 → pi/2）。
- `RcCommand` の range・serialization・neutral/safe（safe==neutral, throttle center）・normalized 変換（throttle 0 → center）。
- direct emu argv の exact shape（`emu_vehicle`, `stampfly.xml`, duration_us）で `sf sils fly` を起動しない。
- root 解決（explicit/environment/default）と、missing emu / missing model / non-executable emu の fail closed。
- seam marker 検証の fail closed。
- transport: start が最初に `rc 2048 2048 2048 2048` を送る、receive sequence 増加、t monotonic。
- duplicate / non-advancing `t` → fault。
- malformed STATE / missing telemetry / stale / read timeout（<=60）→ fault。
- process exit / broken stdin（broken pipe）→ fault。
- `close()` が `quit` のみを送り、bounded wait 後に terminate/kill。
- provenance が simulation-only を明示。
- source scan で `shell=True` / serial / `arm`/`land`/`disarm` command が無い。
- `telemetry_to_health` が freshness/battery/bounds を翻訳し、armed を捏造しない。
- `SilsControlAdapter` が throttle 0 以外と過大 stick を拒否し、normalized→ADC 変換。
- `SilsClosedLoopDriver` が 2 iteration 以上で、iteration 2 の command が iteration 1 後の STATE から計算されることを示す。

実 SILS smoke:

```
.venv/bin/python -m host.stampfly_sils --root /Users/fu2hito/src/stampfly_ecosystem --json smoke --iterations 4
```

既存 suite:

```
.venv/bin/python -m unittest host.tests.test_stampfly_sim -v
.venv/bin/python -m unittest discover -s host/tests -v   # AtomCam localhost bind は sandbox 依存
git diff --check
git status --short --branch
```

## 証拠・実行記録

2026-09-14〜15 の verification で以下を実行した。

- [x] `.venv/bin/python -m unittest host.tests.test_stampfly_sils -v`: **PASS 55/55**。
- [x] `.venv/bin/python -m unittest host.tests.test_stampfly_sim -v`: **PASS 33/33**。
- [x] host suite excluding AtomCam localhost fixture: **PASS 168/168**（15 modules）。
- [ ] `host.tests.test_atomcam`: テスト本体は開始前に fixture bind が `PermissionError: [Errno 1] Operation not permitted` で失敗。StampFly simulation regression として扱わない。
- [x] `git diff --check`: **PASS**。
- [x] official build path確認: installed `sf sils build --help` と `lib/sfcli/commands/sils.py::run_build()` を読み、`sf sils build --target vehicle` → `emu_vehicle` が official CMake host-SILS build で、macOS では flash/hardware/toolchain-install path を通らないことを確認。
- [x] human-run build: `source setup_env.sh && sf sils build --target vehicle`: **PASS**。`simulator/sils/build/emu_vehicle` exists + executable、model exists。
- [x] installed source marker verification against `/Users/fu2hito/src/stampfly_ecosystem`: **PASS**。direct emu argv、model path、`SILS_EMU_REALTIME=1`、`SILS_EMU_RC_STDIN=1`、RC stdin format、STATE format が current installed source と一致。
- [x] external `/Users/fu2hito/src/stampfly_ecosystem` `git status --short --branch`: `## main...origin/main`（変更なし）。
- [x] `resolve`: **PASS** (`found=true`, `emu_executable=true`, `model_exists=true`, reason=`artifacts_present`)。
- [x] real SILS smoke 4 iterations: **PASS**。first transport command は実装契約どおり `rc 2048 2048 2048 2048`、その後 bounded roll command `rc 2559 2048 2048 2048`。real STATE は `t=0.000, 0.033, 0.066, 0.099, 0.132` と strict に進み、local receive sequence 1→5。各 command 後の fresh STATE を使って次の `roll_error=0.0500` decision を再計算。process/telemetry/scheduler fault なし。
- [x] real SILS smoke 20 iterations: **PASS**。sim time 0.000→0.660 s、receive sequence 1→21、command count 21、fault 0。全 iteration で `mission_state=PREFLIGHT`, `health_telemetry_valid=true`。roll は 0.0 rad のままで `attitude_changed=false`。ARM していないため、roll command による物理姿勢変化は観測されなかった事実をそのまま記録する。
- [x] source-level end-to-end seam再確認: RC line → `sils::inject_rc()` → ESP-NOW ControlPacket → linked vehicle firmware → virtual board/MuJoCo plant step → firmware estimate/system-mode/power topics → `STATE`。これと live fresh STATE/next-decision 証拠により Milestone A の real vehicle/control loop を確認。
- [x] provenance: `provider=stampfly_ecosystem`, `evidence_kind=simulation`, `simulation=true`, `flight_qualified=false`; ARM/land/disarm/serial/USB/hardware path 未使用。

deterministic fake の feedback 検証に加え、installed `emu_vehicle` の live process でも command → real firmware/plant scheduler → fresh STATE → host mission/control → next command の一巡以上を確認したため、**Milestone A は COMPLETE / PASS** とする。non-arming PREFLIGHT のままなので姿勢変化そのものは観測されておらず、そこを追加の plant-response evidence としては主張しない。Milestone B（camera/perception closed loop）は未達のまま。

## 対象外

- Milestone B（camera / vision / perception 閉ループ）
- ARM / takeoff / motor / 実機 flight
- USB flood / G3 / flight qualification
- `/Users/fu2hito/src/stampfly_ecosystem` の変更
- `sf` の install / download / build / flash / serial discovery

## 注記

関連: [20260914-stampfly-ecosystem-simulation-sils](20260914-stampfly-ecosystem-simulation-sils.md)（片方向 batch wrapper）、[20260909-vision-control-runtime-integration](20260909-vision-control-runtime-integration.md)（Milestone B 側の runtime boundary）、[20260909-camera-stampfly-closed-loop](20260909-camera-stampfly-closed-loop.md)。全体計画: [20260908-flight-roadmap](20260908-flight-roadmap.md)。
