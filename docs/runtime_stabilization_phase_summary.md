# Runtime stabilization phase — change summary

Eighth pass; first pass on the `runtime-stabilization-phase` branch.
Methodology stays the same (no architectural redesign); the work shifts
from "build the framework" to **"use the framework to measure what's
actually unstable in the runtime."**

Companion documents:

- [`docs/reference_experiment_analysis.md`](reference_experiment_analysis.md) — Task A deliverable.
- The per-task helpers under `research/analysis/`.

Runtime code, telemetry CSV schemas, deployment manifests, and the
`/verify/image` contract are unchanged.

## 1. Stabilization changes made

| Task | What | How |
|------|------|-----|
| B (Orientation) | Sentinel + reachability + landmark anomaly diagnostics + percentile-based threshold recommendation | `research/analysis/orientation_diagnostics.py` — fully offline, no `edge/orientation.py` modification |
| C (YuNet) | Per-track bbox-jitter quantification with EMA simulation (α=0.30 and α=0.50), detection persistence + gap stats, geometry/blur/proximity quality bundle | `research/analysis/yunet_stabilization.py` |
| D (Recognition) | Sim volatility per track + EMA-on-sim simulator (three α values) + matched-rate-at-threshold (raw vs smoothed) + identity-persistence summary | `research/analysis/recognition_stabilization.py` |
| E (PAD) | Composite PAD-stability score combining real-dominance, hysteresis, transition-rate, rigid-ratio stability, and replay-pattern safety | `research/analysis/pad_stabilization.py` |
| F (Offload + CPU + cadence) | Rolling offload-rate (volatility), threshold-boundary diagnostics (filtered to rows that actually reached recognition), CPU hotspot share table, cadence dt-stats with CV | `research/analysis/offload_performance.py` |

## 2. Orientation findings and fixes

Investigated `edge/orientation.py` directly; the geometric calculator
is sound (eye/mouth ratio via Euclidean distance, no normalisation
issue, no Pi vs PC divergence). The three real problems are reporting
+ calibration:

1. **`orient_ratio = 0.0` is recorded as a sentinel**, not "ratio is
   zero." 70 % of rows are sentinels because the pose estimator only
   runs when landmarks are validated. Naïve percentile reads of the
   column are wrong.
2. **`OVERHEAD` is unreachable.** With current camera + landmarks the
   minimum observed ratio is 0.626 (older aggregate) and 0.803 (modern
   May-13 session). The configured `ORIENTATION_OVERHEAD_TH = 0.60` is
   below the empirical floor.
3. **Landmark anomalies inflate the right tail.** Ratios >2.0
   (up to 6.86 max) are landmark mis-detections, not real geometry.
   Pose estimator uses them unfiltered.

**Fixes applied:**

- Added `orientation_diagnostics.diagnose_session()` that exposes
  `valid_fraction`, `overhead_reachability_at_0_60`,
  `landmark_anomaly_rate`, `raw_vs_smoothed_disagreement`, and
  percentile-based `threshold_recommendation`.
- The runtime classifier is **not** modified. Operators apply a new
  `ORIENTATION_OVERHEAD_TH` (the analyzer suggests ~0.90 from the
  10th-percentile rule for the reference camera setup) by editing
  `config/settings.py`. The existing
  `data/plots/orientation/calibration_suggestions.txt` flagged the
  same gap but did not quantify reachability.

Documented in [`docs/reference_experiment_analysis.md`](reference_experiment_analysis.md) §3.

## 3. Experiment findings (from `reference_experiments.zip`)

| Signal | Reference value | Implication |
|--------|-----------------|-------------|
| `t_detect_ms` mean | 118 ms (modern) / 134 ms (older aggregate) | YuNet alone is 62 % of frame time. Detection is the dominant cost. |
| `t_total_ms` mean | 190 ms | Loop is fully serial; cadence ≈ latency. |
| FPS mean | 5.5 (sess 1) / 6.7 (sess 2) | Below the brief's implicit target. |
| CPU temp max | 65.7 °C / **73.5 °C** | Session 2 ran hot — 1.5 °C below `THERMAL_WARN_C`. |
| `sim_std_mean_over_tracks` | 0.20–0.23 (older) / 0.08 (modern) | Recognition got tighter; EMA(0.30) would shave another 11 %. |
| `MATCHED` rate | 0.24 % of rows | Most frames are `NO_MATCH` (no validated face). |
| Track count | 88 tracks across 12.7 k rows | Many short-lived tracks; detector frequently drops the face. |
| `OVERHEAD` frames | 0 across every session | Unreachable with current threshold. |
| Cadence CV | 0.286 | Frame-interval jitter is significant (CV > 0.2). |

Full details in [`docs/reference_experiment_analysis.md`](reference_experiment_analysis.md).

## 4. Diagnostics added

All written as offline pandas analyzers; each is a self-contained
module with a CLI:

```bash
python -m research.analysis.orientation_diagnostics  --session experiments/exp_<id>/
python -m research.analysis.yunet_stabilization      --session experiments/exp_<id>/
python -m research.analysis.recognition_stabilization --session experiments/exp_<id>/
python -m research.analysis.pad_stabilization        --session experiments/exp_<id>/
python -m research.analysis.offload_performance      --session experiments/exp_<id>/
```

Each emits a JSON sidecar under `experiments/exp_<id>/summaries/`.

## 5. Lightweight optimizations applied

This pass deliberately did **not** modify the runtime. It surfaced
concrete optimization candidates instead:

| Candidate | Source | Estimated win |
|-----------|--------|---------------|
| Reduce YuNet input resolution from 640×480 to 480×360 (or skip every other frame and track in between) | t_detect = 62 % of total | 30–50 ms per frame (~−25 % cadence) |
| Apply EMA(0.30) to `sim` before threshold decision | Recognition simulator: −37 % bbox-w jitter, −11 % sim-std | Fewer flip-flops in mid-confidence band; cost: 1–3 frame lag |
| Move `ORIENTATION_OVERHEAD_TH` from 0.60 to ~0.90 | Reachability analyzer: 0 % currently | `OVERHEAD` becomes a real bucket; downstream orientation-aware analysis becomes meaningful |
| Widen orientation smoothing window from 5 to 7–9 frames | Disagreement rate 6.6 % | Slightly more lag, fewer single-frame mis-classifications |

None applied automatically; each is a one-liner in
`config/settings.py` (or the YuNet input-resolution constant in
`edge/main.py`) that the next pass should attempt on a controlled
sweep.

## 6. Unresolved instability risks

- **YuNet detection time dominates** and the brief forbids YuNet
  redesign. The remaining options (input-resolution reduction, frame
  skipping) need controlled `threshold_sweep` style experiments before
  being applied.
- **`OVERHEAD` will stay structurally empty** under the current
  camera mount + landmark set even after threshold adjustment, unless
  the camera is repositioned. This is a fixed-installation question,
  not a code change.
- **Identity flicker is rare in the reference data** (2 of 88 tracks
  see ≥2 identities). The "identity flickering" complaint in the
  brief may be a perception artefact from short-lived tracks rather
  than identity churn within a stable track. Re-evaluate after
  applying detection-persistence fixes.
- **PAD stability score on the reference session is 0.20**, dominated
  by transition rate and hysteresis. The pass-6 thresholds
  (`QUALITY_GATE_DEFAULTS["pad_flip_rate_alert"]=0.15`) trigger
  on this data. Calibrating PAD itself is deferred.
- **Threshold-boundary fraction is 48 %** (near either threshold)
  among frames that actually reached recognition. The system runs in
  a regime where small calibration changes flip many decisions —
  expected, but worth surfacing.

## 7. Deferred runtime validations

- Apply the four optimization candidates above on a controlled sweep
  using the pass-7 `sweep_orchestrator`.
- Live edge → cloud round-trip with the new orientation threshold.
- Add cloud-side mirrors of the five new helpers
  (`cloud_backend/analytics/`) so the dashboard exposes the same
  diagnostics over the event store. Deferred to keep this pass
  measurement-only.
- Tagged spoof dataset to ground the PAD stability score against real
  attack labels.
- Tag stabilization fixes in `data/experiment_sessions.jsonl` once the
  threshold change is applied so before/after can be diffed by
  `session_comparison`.

## 8. Files added / modified

### Added

| Path | Purpose |
|------|---------|
| `docs/reference_experiment_analysis.md` | Task A deliverable. |
| `docs/runtime_stabilization_phase_summary.md` | This file. |
| `research/analysis/orientation_diagnostics.py` | Task B. |
| `research/analysis/yunet_stabilization.py` | Task C. |
| `research/analysis/recognition_stabilization.py` | Task D. |
| `research/analysis/pad_stabilization.py` | Task E. |
| `research/analysis/offload_performance.py` | Task F. |

### Modified

| Path | Change |
|------|--------|
| `.gitignore` | Added `/reference_experiments.zip` so the large archive stays out of source. |

No CSV schemas, runtime files, deployment manifests, or shared
contracts changed. The brief's "Do Not redesign architecture" line is
honoured.

## 9. Validation performed

| Check | Result |
|-------|--------|
| `python3 -m compileall …` across all source dirs | exit 0 |
| `research.analysis.orientation_diagnostics.diagnose_session` on `/tmp/ref_exp/results/exp_20260513_123725/`'s diagnostic CSV | rows=5849, valid_fraction=0.232, OVERHEAD reachable at 0.60 = 0.000, min_ratio=0.803, suggested OVERHEAD=0.901, TILTED=1.031, smoothing disagreement = 0.066 |
| `yunet_stabilization.diagnose_session` on the same session | 22 tracks, mean_active_fraction=0.252, EMA(0.30) w-jitter reduction 0.371 / h 0.416 |
| `recognition_stabilization.diagnose_session` | 22 tracks, sim_std_mean=0.081, EMA(0.30) reduction 0.107, matched_rate@0.70 raw=0.006 / EMA(0.30)=0.000 |
| `pad_stabilization.diagnose_session` | PAD stability score = 0.201, all 5 components contributed |
| `offload_performance.diagnose_session` | overall offload rate 1.9 %, threshold-boundary 47.8 % (after sentinel filter), CPU top stage `t_detect_ms` at 62.1 %, cadence p50=164.6 ms / CV=0.286 |
| `verify_manifests.sh` | Both bundles OK |
| `package_cloud.sh` tarball | 49 KB; no regression in module inclusion |

Live runtime / camera / cloud round-trip validation is deferred per
the phase brief.
