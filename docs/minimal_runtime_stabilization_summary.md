# Minimal runtime stabilization — change summary

Ninth pass, second on the `runtime-stabilization-phase` branch. Acts on
the confirmed findings from pass 8 (`docs/reference_experiment_analysis.md`)
by adding six env-driven stabilization knobs. **Every default value
preserves the historic runtime behaviour exactly.** Companion docs:
[`STABILIZATION_KNOBS.md`](STABILIZATION_KNOBS.md).

## 1. Runtime stabilizations implemented

| Area | What | How | Default |
|------|------|-----|---------|
| Orientation | Make three pose thresholds env-overridable | `config/settings.py` reads `ORIENTATION_OVERHEAD_TH`, `ORIENTATION_TILTED_TH`, `ORIENTATION_SMOOTHING_WINDOW` from env | `0.60 / 0.915 / 5` |
| YuNet | Configurable detector input resolution | `config/settings.py` exposes `YUNET_INPUT_W`, `YUNET_INPUT_H`; `edge/main.py` reads them in place of the historic `(640, 480)` literal | `640 / 480` |
| YuNet | Optional per-track bbox EMA smoothing after `find_best_face_match` | `BBoxEMASmoother` in `edge/stabilization.py`; applied in the per-track loop when `BBOX_EMA_ALPHA > 0` | `α = 0.0` (off) |
| Recognition | Optional per-track sim EMA before threshold comparison | `SimEMASmoother`; applied right after `pose_aware_match` when `SIM_EMA_ALPHA > 0` | `α = 0.0` (off) |
| Recognition | Match-persistence counter gating attendance log | `MatchPersistenceCounter`; gates the `LOG_RUNTIME.info("Attendance marked")` event when `MATCH_PERSISTENCE_FRAMES > 1` | `1` (current) |
| PAD | Spoof-streak smoother that downgrades a single SPOOF to UNCERTAIN | `PADSpoofStreakSmoother`; applied right after `liveness.assess_frame` when `PAD_SPOOF_STREAK_REQUIRED > 1` | `1` (current) |

`edge/main.py` instantiates each stabilizer and resets per-track state
in the existing `NO_MATCH` cleanup block. The `dbg["face_w"]`,
`dbg["face_h"]`, `dbg["sim"]`, and `dbg["lbl"]` fields reflect the
post-stabilization values so `diagnostic_log.csv` stays self-consistent.

## 2. Thresholds adjusted

**None as defaults.** The pass exposes the orientation thresholds and
the YuNet input as env overrides, but the default values are unchanged.
Operators apply changes via env vars per
[`STABILIZATION_KNOBS.md`](STABILIZATION_KNOBS.md):

```bash
ORIENTATION_OVERHEAD_TH=0.85 \
YUNET_INPUT_W=480 YUNET_INPUT_H=360 \
BBOX_EMA_ALPHA=0.30 SIM_EMA_ALPHA=0.30 \
MATCH_PERSISTENCE_FRAMES=2 PAD_SPOOF_STREAK_REQUIRED=2 \
python run.py
```

## 3. Smoothing added

| Smoother | State | Cost | Telemetry impact |
|----------|-------|------|------------------|
| `BBoxEMASmoother` | `dict[track_id] → (sx, sy, sw, sh)` | One multiply-add per dimension per frame | Smoothed bbox flows into crop / brightness / distance; `dbg["face_w"]` and `face_h` reflect smoothed values |
| `SimEMASmoother` | `dict[track_id] → float` | One multiply-add per frame | `dbg["sim"]` (and therefore `diagnostic_log.csv:sim`) reflects the value used for the threshold decision |
| `MatchPersistenceCounter` | `dict[track_id] → (identity, run_length)` | O(1) per matched frame | No new CSV columns; the `attendance_log.csv` row is delayed by `(N − 1)` frames |
| `PADSpoofStreakSmoother` | `dict[track_id] → int` | O(1) per frame | `dbg["lbl"]` reflects the smoothed label; the value flowing into the SPOOF / REAL decision branches |

All four smoothers reset per-track state in the existing `NO_MATCH`
cleanup branch so state can't outlive a track.

## 4. Configuration hooks added

`config/settings.py` exposes:

- `ORIENTATION_OVERHEAD_TH`, `ORIENTATION_TILTED_TH`, `ORIENTATION_SMOOTHING_WINDOW` (now env-driven; defaults unchanged)
- `YUNET_INPUT_W`, `YUNET_INPUT_H`
- `BBOX_EMA_ALPHA`, `SIM_EMA_ALPHA`
- `MATCH_PERSISTENCE_FRAMES`, `PAD_SPOOF_STREAK_REQUIRED`

`config/experiment_session.py:_SETTINGS_SNAPSHOT_KEYS` is extended so
every new knob lands in `settings_snapshot.json`, keeping the
`session_comparison` tool's before/after diff useful.

`edge/main.py` logs an `INFO` line at startup naming every active
stabilizer so the runtime trace makes the active configuration
visible:

```
Stabilizers active: bbox_ema=0.30 sim_ema=0.30 match_persistence=2 pad_spoof_streak=2 yunet_input=(480,360)
```

(only logged when **any** knob is set to a non-default value).

## 5. Expected stabilization impact

From the pass-8 EMA simulations applied to reference data
(`research/analysis/yunet_stabilization`, `recognition_stabilization`):

| Knob | Reference impact |
|------|------------------|
| `BBOX_EMA_ALPHA=0.30` | −37 % width-step jitter, −42 % height-step jitter |
| `SIM_EMA_ALPHA=0.30` | −11 % per-track sim std; tightens decisions but the matched_rate at `th=0.70` drops 0.6 % → 0.0 % on the reference noise level |
| `MATCH_PERSISTENCE_FRAMES=2` | Suppresses one-frame matched flicker; in reference data only 31 MATCHED rows out of 12 746, so the delay cost is small |
| `PAD_SPOOF_STREAK_REQUIRED=2` | Damps the one-frame rigid-motion glitches that drive the 47.8 % threshold-boundary occupancy and the 0.20 PAD stability score |
| `ORIENTATION_OVERHEAD_TH=0.85` | Makes the OVERHEAD bucket reachable on the tested camera geometry (currently 0 OVERHEAD frames across every session) |
| `YUNET_INPUT_W/H=480/360` | ≈ −30 % `t_detect_ms` → ≈ +30 % FPS; small-face sensitivity degrades |

Each is a **starting point** for a `sweep_orchestrator threshold_sweep`
session, not a recommended permanent setting.

## 6. Unresolved runtime risks

- **Default behaviour is unchanged**, but operators applying multiple
  knobs at once will see compounded effects. The pass-7
  `session_comparison` tool is the recommended way to attribute
  observed deltas to specific knobs.
- **`BBOX_EMA_ALPHA > 0` changes the per-track crop**; recognition
  accuracy is sensitive to crop alignment. A misaligned smoothed crop
  could degrade sim scores. Worth verifying with a controlled
  baseline-vs-tuned comparison before adopting permanently.
- **`MATCH_PERSISTENCE_FRAMES > 1` delays "first attendance" by
  `(N − 1)` frames**. On the Pi at 5 FPS that's 0.4–0.6 s of added
  latency before the log entry appears. The 300 s cooldown is
  unaffected.
- **`PAD_SPOOF_STREAK_REQUIRED > 1` slightly widens the spoof-decision
  window**. With `N = 2`, a real spoof attack takes 2 frames to register
  instead of 1 — at 5 FPS that's a 200 ms response delay.
- **`YUNET_INPUT_W/H` below 320×240 risks missing small faces** (e.g.
  a subject at 3 m). The brief's `MIN_DISTANCE`/`MAX_DISTANCE` and the
  empirical `face_w * face_h` floor are unchanged, so distance-based
  rejections still fire — but the detector itself may not see the face.

## 7. Deferred real-world validations

- Apply each knob on a controlled
  `python -m research.experiments.sweep_orchestrator --preset threshold_sweep`
  run and diff with `session_comparison`.
- Live edge → cloud round-trip with `BBOX_EMA_ALPHA=0.30` and
  `SIM_EMA_ALPHA=0.30` to confirm the offload path is unchanged.
- PAD spoof-streak validation against a tagged spoof dataset (no
  ground truth available in the reference archive).
- Long-run thermal validation with `YUNET_INPUT_W/H=480/360` —
  reducing detection cost should also reduce sustained CPU temp, but
  needs a multi-minute run on the Pi.
- A "tuned profile" recipe that combines the conservative starting
  points (e.g. `BBOX_EMA_ALPHA=0.30 SIM_EMA_ALPHA=0.20 MATCH_PERSISTENCE_FRAMES=2`)
  and is validated end-to-end on the Pi. Deferred so the operator can
  pick combinations from observed evidence rather than my guess.

## 8. Files added / modified

### Added

| Path | Purpose |
|------|---------|
| `edge/stabilization.py` | Four pure-Python stabilizer classes (BBoxEMASmoother, SimEMASmoother, MatchPersistenceCounter, PADSpoofStreakSmoother). |
| `docs/STABILIZATION_KNOBS.md` | Env-var reference with behaviour, ranges, defaults, recommended starting points. |
| `docs/minimal_runtime_stabilization_summary.md` | This file. |

### Modified

| Path | Change |
|------|--------|
| `config/settings.py` | Six new env-driven knobs + three existing orientation constants converted to env-readable form. All defaults preserve historic behaviour. |
| `config/experiment_session.py` | `_SETTINGS_SNAPSHOT_KEYS` extended with the six new knob names. |
| `edge/main.py` | Reads `settings.YUNET_INPUT_W/H` in the YuNet init (instead of `(640, 480)` literal); instantiates the four stabilizers; applies them at narrow integration points (post-match bbox smoothing, post-liveness PAD smoothing, post-`pose_aware_match` sim smoothing, MATCHED-branch persistence gating); resets per-track state in the existing `NO_MATCH` cleanup block. ≈ 30 net new lines. |

No CSV schema changed. `DIAG_COLUMNS`, `TELEMETRY_CSV_COLUMNS`, and the
`attendance_log` header are identical to pass-8. Deployment manifests,
shared contracts, cloud_backend, and the offload contract are
untouched.

## 9. Validation performed

| Check | Result |
|-------|--------|
| `compileall` across the full tree | exit 0 |
| `from config import settings` with no env vars set: every new knob at the documented default (`OVERHEAD_TH=0.60`, `BBOX_EMA_ALPHA=0.0`, `MATCH_PERSISTENCE_FRAMES=1`, …) | OK |
| Env overrides (`ORIENTATION_OVERHEAD_TH=0.85 BBOX_EMA_ALPHA=0.30 …`) → `importlib.reload(settings)` picks them up | OK |
| `BBoxEMASmoother(0.0).smooth(...)` is a pass-through; `BBoxEMASmoother(0.30)` reproduces `0.3 * raw + 0.7 * prev` exactly | OK |
| `SimEMASmoother(0.30)` reproduces the same formula on a single float | OK |
| `MatchPersistenceCounter(3)`: run only crosses the threshold after 3 frames; identity change resets the run | OK |
| `PADSpoofStreakSmoother(3)`: first two SPOOFs return `UNCERTAIN`, third returns `SPOOF`, REAL resets the streak | OK |
| AST scan of `edge/main.py:DIAG_COLUMNS` → 52 columns, first/last identical to pass-8 | OK |
| `init_experiment_session(tmpdir)` → `settings_snapshot.json` contains every new key with the expected default value | OK |
| `verify_manifests.sh` both bundles | OK |

Live runtime / camera / cloud round-trip validation is deferred per the
brief.
