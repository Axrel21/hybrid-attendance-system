# Runtime stabilization knobs

Six env-driven settings expose the minimal, opt-in stabilizers added in
pass 9. **Every default value preserves the historic runtime behaviour
exactly** — flip a knob to try a stabilization; set it back to revert.

All knobs are captured in `experiments/exp_<id>/config/settings_snapshot.json`
so each session pins exactly which stabilizers were active.

## Orientation thresholds

`config/settings.py` exposes the three orientation parameters as env
overrides; defaults preserve historic behaviour.

| Knob | Default | Range | Behaviour |
|------|---------|-------|-----------|
| `ORIENTATION_OVERHEAD_TH` | `0.60` | (0, 2) | Below this → OVERHEAD. Reference analysis shows the empirical orient_ratio floor is ~0.80, so the default classifies zero frames as OVERHEAD on the tested camera. Try `0.85`–`0.90` to make the bucket reachable. |
| `ORIENTATION_TILTED_TH` | `0.915` | (0, 2) | Between OVERHEAD_TH and this → TILTED; above this → FRONTAL. The default is already calibrated against the existing data (~p50 of valid ratios). |
| `ORIENTATION_SMOOTHING_WINDOW` | `5` | ≥1 (frames) | Majority-vote window over `mode_raw`. Reference shows 4–7 % disagreement between raw and smoothed at the default. Raise to `7`–`9` for fewer single-frame mode flips. |

Example:

```bash
ORIENTATION_OVERHEAD_TH=0.85 ORIENTATION_TILTED_TH=1.00 \
ORIENTATION_SMOOTHING_WINDOW=7 \
python run.py
```

## YuNet input resolution

| Knob | Default | Range | Behaviour |
|------|---------|-------|-----------|
| `YUNET_INPUT_W` | `640` | ≥64 | YuNet input width. Lower values reduce `t_detect_ms` ≈ proportionally. |
| `YUNET_INPUT_H` | `480` | ≥64 | YuNet input height. |

Reference: `t_detect_ms` is 62 % of frame time at 640×480 (~118 ms on
Pi). Halving the input → ~halving detection cost. Small-face
sensitivity degrades; not safe to drop blindly below 320×240.

Example:

```bash
YUNET_INPUT_W=480 YUNET_INPUT_H=360 python run.py
```

## BBox EMA smoothing

| Knob | Default | Range | Behaviour |
|------|---------|-------|-----------|
| `BBOX_EMA_ALPHA` | `0.0` (disabled) | [0, 1] | Per-track EMA over `(x, y, w, h)` applied *after* `find_best_face_match` so detection accuracy is unaffected. Reference simulation: α=0.30 reduces width-step jitter 37 %, height 42 %. Costs one frame of lag. |

The smoothed bbox feeds the crop / brightness / distance reads
downstream, so `dbg["face_w"]` / `face_h` reflect smoothed values in
`diagnostic_log.csv`. Telemetry is self-consistent.

Example:

```bash
BBOX_EMA_ALPHA=0.30 python run.py
```

## Similarity EMA

| Knob | Default | Range | Behaviour |
|------|---------|-------|-----------|
| `SIM_EMA_ALPHA` | `0.0` (disabled) | [0, 1] | Per-track EMA over the recognition `sim` score before threshold comparison. The logged `sim` column reflects the smoothed value. Reference: α=0.30 collapses sim-std by 11 %. |

Example:

```bash
SIM_EMA_ALPHA=0.30 python run.py
```

## Match persistence

| Knob | Default | Range | Behaviour |
|------|---------|-------|-----------|
| `MATCH_PERSISTENCE_FRAMES` | `1` (current behaviour) | ≥1 | Minimum consecutive MATCHED frames for the same identity before an `Attendance marked: …` log row is written and the attendance CSV gains a row. The `dbg["decision"] = "MATCHED"` still records every frame for diagnostics. |

Reference shows 0.24 % of frames hit MATCHED. Raising to `2`–`3`
suppresses single-frame flicker without affecting cooldown-based
deduplication.

Example:

```bash
MATCH_PERSISTENCE_FRAMES=3 python run.py
```

## PAD spoof streak

| Knob | Default | Range | Behaviour |
|------|---------|-------|-----------|
| `PAD_SPOOF_STREAK_REQUIRED` | `1` (current behaviour) | ≥1 | Minimum consecutive SPOOF labels from the liveness engine before the pipeline accepts the SPOOF verdict. While the streak is below threshold, the label is downgraded to UNCERTAIN, suppressing false-positive rejections from one-frame rigid-motion glitches. |

Reference PAD stability score is 0.20 (high hysteresis). Try `2`–`3` to
damp single-frame spoof transients.

Example:

```bash
PAD_SPOOF_STREAK_REQUIRED=2 python run.py
```

## Cadence

Already configurable via the existing `TARGET_LATENCY_MS` knob — leave
at `0` to let the loop run as fast as possible (current behaviour);
set positive to enforce a minimum frame interval in headless mode.

## Recommended starting points (from reference analysis)

| Goal | Knob change |
|------|-------------|
| Make `OVERHEAD` a real bucket | `ORIENTATION_OVERHEAD_TH=0.85` |
| Reduce detection latency ~25 % | `YUNET_INPUT_W=480 YUNET_INPUT_H=360` |
| Damp bbox jitter (cropping) | `BBOX_EMA_ALPHA=0.30` |
| Reduce sim-driven flicker | `SIM_EMA_ALPHA=0.30 MATCH_PERSISTENCE_FRAMES=2` |
| Damp single-frame SPOOF rejections | `PAD_SPOOF_STREAK_REQUIRED=2` |

Always change **one knob at a time** so the resulting
`stabilization_report.json` cleanly attributes the delta to the change.

## Reproducibility

Every knob is recorded in
`experiments/exp_<id>/config/settings_snapshot.json` under
`settings_module.<KNOB_NAME>`. The `research.analysis.session_comparison`
tool's "modified vs baseline" diff will pick up the change.
