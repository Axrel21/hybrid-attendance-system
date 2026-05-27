# Runtime diagnostics — reference

Function-by-function reference for the gap-filling diagnostics added in
pass 6. Pair with [`STABILIZATION_DIAGNOSTICS.md`](STABILIZATION_DIAGNOSTICS.md)
(eight-dimension stabilization summary) and
[`QUALITY_GATES.md`](QUALITY_GATES.md) (soft tag evaluator).

These helpers consume the existing `diagnostic_log.csv` schema only —
no edge-runtime code changes were required.

## YuNet observability

| Function | Output | What it shows |
|----------|--------|---------------|
| `proximity_diagnostics(df, ...)` | `{close_fraction, far_fraction, out_of_range_fraction, distance_stats}` | Fraction of frames near `MIN_DISTANCE` or `MAX_DISTANCE`, plus percentile distance block. Drives `excessive_proximity` tag. |
| `missed_detection_diagnostics(df)` | `{no_match_rate, out_of_range_rate, decision_distribution}` | How often the detector returned `NO_MATCH` or `OUT_OF_RANGE` per session. |
| `unstable_track_diagnostics(df, min_frames_for_stable)` | `{unstable_count, unstable_rate, unstable_tracks: [...]}` | Tracks shorter than the stability threshold — interpret as detector instability or framing churn. |

The existing eight-dimension stabilization summary in `research.analysis.stabilization`
already covers bounding-box stability (`bbox_stability`),
blur/geometry quality (`blur_geometry_quality`), and detection
persistence (`detection_persistence`); use those alongside the new
helpers for the full YuNet picture.

## Recognition observability

| Function | Output | What it shows |
|----------|--------|---------------|
| `identity_flicker(df)` | `{per_track: [{distinct_identities, identity_counts}], max_distinct}` | How many distinct identities each track was assigned. A stable track should see 1. Drives `identity_flicker` tag. |
| `track_recognition_summary(df)` | `{per_track: [{sim_mean, sim_std, sim_p95, dominant_identity, dominant_fraction, recognised_fraction}]}` | Per-track recognition snapshot. `recognised_fraction` is the share of frames with sim ≥ 0.65. |
| `orientation_vs_confidence(df)` | `{per_mode: [{mode, sim_mean, sim_std, sim_p50, sim_p95}]}` | Mean / std of similarity grouped by orientation mode. Useful for FRONTAL-vs-TILTED-vs-OVERHEAD analysis. |
| `distance_vs_confidence(df, bins)` | `{per_bin: [{lo_m, hi_m, sim_mean, sim_std, n}]}` | Recognition quality as a function of distance bucket. |

## PAD/Liveness observability

| Function | Output | What it shows |
|----------|--------|---------------|
| `rigid_ratio_temporal(df)` | `{per_track: [{mean, std, p95}]}` | Track-level `rigid_ratio` distribution; useful when investigating motion-PAD calibration. |
| `spoof_transitions(df)` | `{per_track: [{flip_count, flip_rate, transitions: {"REAL->SPOOF": N, ...}}], total_transitions}` | Per-track PAD-label transition matrix. Replay attacks often produce REAL→SPOOF→REAL hysteresis. |
| `replay_pattern_diagnostics(df)` | `{area_var: {...}, area_var_below_100_rate, blur: {...}, blur_below_80_rate}` | Frame-freeze proxies — low `avg_area_var` and low `avg_blur` together suggest still-frame replay. |
| `pad_hysteresis(df)` | `{per_track: [{flip_rate}], overall_flip_rate}` | Adjacent-frame `lbl` flip rate. Drives `frequent_spoof_flips` tag. |

## Top-level

`diagnose_session(diagnostic_csv)` runs every helper and returns the
bundled dict. CLI:

```bash
python -m research.analysis.runtime_diagnostics \
    --session experiments/exp_<id>/
```

Writes `experiments/exp_<id>/summaries/runtime_diagnostics.json`.

## Combined report

`research.analysis.stabilization_report` is the one-shot driver that
calls `stabilization.summarize_session` + `runtime_diagnostics.diagnose_session`
+ `threshold_sweep.sweep_session` + `quality_gates.evaluate_metrics`,
loads the protocol sidecar if present, and writes a single
`stabilization_report.json` plus an optional Markdown.

```bash
python -m research.analysis.stabilization_report \
    --session experiments/exp_<id>/
ls experiments/exp_<id>/summaries/
# stabilization.json, runtime_diagnostics.json, threshold_sweep.json,
# quality_tags.json, stabilization_report.json, stabilization_report.md
```
