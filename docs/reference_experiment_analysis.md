# Reference experiment archive — analysis findings

Source: `reference_experiments.zip` (9.8 MB, 272 files). Unpacked
out-of-tree to `/tmp/ref_exp/`. The archive contains a mix of older
pre-experiment-session aggregated CSVs under `results/data/` plus two
modern per-session directories
(`results/exp_20260513_{123725, 124012}/`) produced by the current
pipeline. All captures predate Track 2 — none of the diagnostic CSVs
carry the `cloud_*` columns, so the analysis below covers edge-only
behaviour.

This document is the **Task A** deliverable for the runtime
stabilization phase. It is meant to be read alongside the per-task
helpers added in the same pass (see
`docs/runtime_stabilization_phase_summary.md`).

---

## 1. Inventory at a glance

| Source | Rows | Notes |
|--------|------|-------|
| `results/data/diagnostic_log.csv` | 12 746 | Aggregated; `experiment_label` is null everywhere. 88 tracks, 3 identities. |
| `results/data/telemetry_log.csv` | 2 434 | Aggregated frame-level perf. |
| `results/data/attendance_log.csv` | 415 | Matched-event log. |
| `results/exp_20260513_123725/diagnostics/diagnostic_log.csv` | 5 849 | Pi run, `CAMERA_BACKEND=libcamera_subprocess`. |
| `results/exp_20260513_124012/diagnostics/diagnostic_log.csv` | 6 209 | Same hardware, runs hotter (max 73.5 °C). |
| `results/exp_20260513_*/summaries/report_*.json` | 2 | Auto-generated post-run JSON. |
| `results/data/plots/orientation/*.png` | 7 (top) + per-label dirs | Already produced by `analyze_orientation.py`. |

Note: the aggregated `results/data/` CSV mixes runs that pre-date
EXPERIMENT_LABEL adoption; `experiment_label` is null for every row.

---

## 2. Latency — YuNet dominates

| Source | mean | p95 | p99 | peak |
|--------|------|-----|-----|------|
| `latency_ms` (per-track, exp_123725) | 142 ms | 220 ms | — | — |
| `t_total_ms` (telemetry, exp_123725) | 190 ms | 254 ms | 409 ms | 424 ms |
| `t_detect_ms` (telemetry, exp_123725) | **118 ms** | 125 ms | 127 ms | 212 ms |

Stage-share of total latency (from telemetry):

| Stage | Share | Mean |
|-------|-------|------|
| `t_detect_ms` | **62 %** | 118 ms |
| `t_tracks_ms` (per-track loop wrapper) | 29 % | 55 ms |
| `t_embed_max_ms` (peak per frame; included in tracks) | 18 % | 34 ms |
| `t_post_ms` | 5 % | 10 ms |
| `t_overlay_ms` | 4 % | 8 ms |
| `t_liveness_max_ms` | 3 % | 6 ms |
| `t_capture_ms` | 0 % | 0.5 ms |

**Bottom line:** YuNet detection alone consumes ~62 % of every frame.
The brief target of ~120 ms total is unachievable without addressing
YuNet. Recognition (embed + match) is essentially free; PAD is cheap.

`t_total_ms` ≈ `dt_ms` ≈ 190 ms — the loop is fully serial, no I/O
wait. Cadence instability ≈ latency instability.

---

## 3. Orientation — known calibration gap, plus a quiet bug

### 3.1 OVERHEAD is structurally unreachable

Across the aggregated CSV (3 749 frames with a valid orient_ratio):

| metric | value |
|--------|-------|
| `orient_ratio` min | **0.626** |
| 5th percentile | 0.886 |
| 50th percentile | 1.063 |
| 95th percentile | 1.78 |
| 99th percentile | 2.82 |
| max | 6.86 |

The configured `ORIENTATION_OVERHEAD_TH = 0.60` is **below the minimum
observed ratio**. Across every captured session, zero frames classify
as `OVERHEAD`. The existing `data/plots/orientation/calibration_suggestions.txt`
already flags this: *"OVERHEAD_TH unchanged (0.600); insufficient
samples (overhead n=0, tilted n=612)"*.

In the modern May-13 sessions the minimum ratio is **0.803** — even
further from reachability. With these landmarks + cameras the
`OVERHEAD` bucket is effectively a documentation hint, not a runtime
classification.

### 3.2 `orient_ratio = 0.0` is a sentinel, not a real value

`orient_ratio` is stored as `0.0` in the diagnostic CSV whenever the
pose estimator did not run that frame (no `landmarks` matched the
track). In the aggregated CSV **70.6 %** of rows have `orient_ratio =
0.0`. A naive percentile read of the column reports `median = 0.0`,
which is meaningless.

**Implication for downstream analysis:** every orientation analyzer
must filter on `mode_raw.notna()` or `orient_ratio > 0` before
computing thresholds. The diagnostic helper added in pass 8
(`research/analysis/orientation_diagnostics.py`) does this and exposes
a `valid_fraction` to make the data loss visible.

### 3.3 Landmark anomalies inflate the right tail

Ratios above ~2.0 are physically implausible (the eye-mouth vertical
span cannot exceed twice the inter-ocular distance unless landmarks
are severely mis-placed). At 99th percentile = 2.82 and max = 6.86,
the upper tail is dominated by YuNet landmark mis-detections rather
than real face geometry. The pose estimator uses these ratios
unfiltered.

The new diagnostic reports `landmark_anomaly_rate` (fraction of valid
frames with ratio outside `[0.30, 2.0]`).

### 3.4 Smoothing already helps

`raw_vs_smoothed_disagreement_rate = 0.0426` — the
`ORIENTATION_SMOOTHING_WINDOW = 5` majority vote changes ~4 % of
frame classifications. Smoothing is doing real work; do not disable.

---

## 4. Recognition — high volatility, dominated by short tracks

Aggregated CSV decision distribution:

| Decision | Count | % |
|----------|-------|-----|
| `NO_MATCH` | 8 997 | 70.6 |
| `OUT_OF_RANGE` | 1 169 | 9.2 |
| `ANALYZING` | 1 057 | 8.3 |
| `BUFFERING` | 366 | 2.9 |
| `UNCERTAIN` | 334 | 2.6 |
| `REJECTED_LIVENESS` | 300 | 2.4 |
| `OFFLOAD_TO_CLOUD` | 221 | 1.7 |
| `BELOW_THRESHOLD` | 162 | 1.3 |
| `UNKNOWN` | 109 | 0.9 |
| `MATCHED` | **31** | **0.24** |

**0.24 %** of frames produce a `MATCHED` decision. The vast majority
are either `NO_MATCH` (track in frame, no face validated this frame),
`OUT_OF_RANGE` (distance check failed), or in the warm-up states
(`BUFFERING` / `ANALYZING`).

Per-session summary JSON reports `sim_std_mean_over_tracks` ≈ 0.20–0.23.
This is high enough that any single-frame threshold decision is
inherently fragile — the new `recognition_stabilization` simulator
shows that an EMA of `sim` with α=0.3 collapses per-track std by
~40–60 % at the cost of a few frames of lag.

88 tracks across 12 746 rows ≈ 145 frames per track on average. 69
tracks (78 %) never see any identity at all (every row is `NO_MATCH` /
`OUT_OF_RANGE`); 17 tracks see exactly one identity; 2 tracks see two.
**Identity flicker is rare; the dominant pattern is "many short-lived
tracks" rather than "one track with shifting identity."**

---

## 5. PAD — works, but rare events

| Label | Count | % of total | % of frames with `lbl` set |
|-------|-------|-----------|---------------------------|
| REAL (within `decision != NO_MATCH`) | 318 (sess 1) / 224 (sess 2) | — | — |
| SPOOF (within `decision != NO_MATCH`) | 292 (sess 1) / 285 (sess 2) | — | — |
| `REJECTED_LIVENESS` (decision-level) | 300 | 2.4 % | — |
| `UNCERTAIN` (decision-level) | 334 | 2.6 % | — |

PAD is active on every detected face. The hysteresis flip rate is
moderate and matches what the pass-6 `pad_hysteresis` diagnostic
already exposes. There's nothing in the reference data that suggests
PAD is broken — just that REAL/SPOOF transitions are sometimes
adjacent-frame, indicating the temporal window could be widened a
hair if false rejects matter.

---

## 6. CPU + thermal

| Session | CPU temp mean | max | Cadence p50 | p99 dt |
|---------|---------------|-----|--------------|--------|
| exp_123725 | 60.2 °C | 65.7 °C | 165 ms (≈ 6 fps) | 409 ms |
| exp_124012 | **69.5 °C** | **73.5 °C** | 139 ms (≈ 7 fps) | — |

Session 2 runs significantly hotter despite being the same hardware
and same protocol — likely a back-to-back run that didn't let the SoC
cool off. Stays under the `THERMAL_WARN_C = 75` threshold but only by
1.5 °C in the second session. The brief's "high CPU utilization"
complaint is corroborated by stage-share: detection + tracks ≈ 91 %
of every frame.

---

## 7. Stabilization candidates (input to the per-task work)

| Priority | Issue | What pass 8 adds |
|----------|-------|------------------|
| B | OVERHEAD never fires; `orient_ratio = 0.0` sentinel; landmark anomalies dominate the right tail | `orientation_diagnostics` analyzer: valid-fraction, OVERHEAD reachability score, landmark anomaly rate, percentile-based threshold recommendation. No runtime change. |
| C | YuNet bbox jitter, ~75 % of frames have no validated face → many short tracks | `yunet_stabilization` analyzer: per-track EMA bbox + jitter quantification + persistence summary + geometry-quality block. |
| D | `sim_std_mean_over_tracks` ≈ 0.20–0.23; 0.24 % MATCHED rate | `recognition_stabilization` simulator: EMA on `sim` and quantify volatility reduction. |
| E | PAD label flips on adjacent frames; no composite stability score | `pad_stabilization`: composite stability score combining `pad_temporal` + `spoof_transitions` + `pad_hysteresis`. |
| F | YuNet = 62 % of latency, embed = 18 %, no cadence summary | `offload_performance`: routing stability + threshold-boundary diagnostics + CPU hotspot share + cadence stats. |

All five are **measurement and visibility** layers. The runtime is
unchanged.

---

## 8. What this pass deliberately does **not** do

- **Modify `edge/orientation.py`.** The geometric formula is sound;
  the OVERHEAD threshold is a calibration question, not a code bug.
- **Modify YuNet runtime parameters.** Reducing the input resolution
  or skipping frames is the natural latency win, but it changes the
  detection behaviour and is out of scope per the "measurement before
  optimization" rule.
- **Touch the PAD heuristics.** Hysteresis flip-rate is high but PAD
  output is consistent with the existing tuning; this pass surfaces
  it, the next pass can act on it.
- **Auto-tune any thresholds.** The `recommend_*` helpers from pass 7
  remain the human-in-the-loop bridge.
