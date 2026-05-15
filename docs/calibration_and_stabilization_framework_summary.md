# Calibration & stabilization framework — change summary

Seventh pass on the `deployment-refactor` branch. Phase shift:
**infrastructure → methodology**. The previous passes made the system
measurable; this one makes it systematically calibratable and
comparable. Edge runtime, deployment topology, CSV schemas, and the
`/verify/image` contract are unchanged.

Pair with [`EXPERIMENT_PRESETS.md`](EXPERIMENT_PRESETS.md) and the
prior per-pass summaries.

## 1. Files added

### Experiment orchestration (Task A)

| Path | Purpose |
|------|---------|
| `research/experiments/presets/threshold_sweep.json` | Post-hoc threshold-sweep preset (single capture, eight th_high candidates). |
| `research/experiments/presets/orientation_sweep.json` | Three-run sweep across `frontal` / `tilted` / `overhead`. |
| `research/experiments/presets/distance_sweep.json` | Six-run sweep from 0.5 m to 3.0 m. |
| `research/experiments/presets/lighting_sweep.json` | Four-run sweep across `bright` / `normal` / `dim` / `backlit`. |
| `research/experiments/presets/pad_attack_sweep.json` | Five-run sweep across attack types. |
| `research/experiments/presets/hybrid_routing_sweep.json` | Three-run sweep across `CLOUD_ROUTING` strategies. |
| `research/experiments/sweep_orchestrator.py` | Preset loader + per-run protocol expansion + post-capture aggregation. CLI `--list`, `--plan`, `--sessions`, `--out`. |

### Analysis tooling (Tasks B, C, E)

| Path | Purpose |
|------|---------|
| `research/analysis/session_aggregator.py` | Multi-session loader + side-by-side metric table + Markdown rendering. Optional `--group-by` (e.g. `protocol.orientation`). |
| `research/analysis/session_comparison.py` | Pairwise baseline-vs-modified diff with metric deltas + tag-set diff + Markdown. |
| `research/analysis/stability_score.py` | Weighted 0..1 composite from existing summary fields; reports per-signal contributions + `missing_signals`. |
| `research/analysis/calibration.py` | Decision-support helpers: `compare_confidence_distributions`, `recommend_match_thresholds`, `recommend_hysteresis_gap`, `recommend_routing_policy`, `pad_threshold_compare`, `operating_point_snapshot`. CLI `compare-distributions` / `recommend-thresholds` / `recommend-hysteresis` / `recommend-routing` / `pad-compare` / `operating-point`. |

### Cloud-side evaluation (Tasks D, E, F)

| Path | Purpose |
|------|---------|
| `cloud_backend/analytics/evaluation.py` | Research-grade wrappers over the event stream: `pad_confusion_matrix`, `orientation_robustness`, `thermal_performance_tradeoff`, `offload_efficiency`, `latency_distribution_comparison`. |

### Docs

| Path | Purpose |
|------|---------|
| `docs/EXPERIMENT_PRESETS.md` | Preset catalogue + workflow + schema. |
| `docs/calibration_and_stabilization_framework_summary.md` | This file. |

## 2. Files modified

| Path | Change |
|------|--------|
| `shared/contracts.py` | `PRESET_NAMES`, `PRESET_VERSION`, `EVALUATION_METRIC_KEYS`, `STABILITY_SCORE_WEIGHTS`, eight new endpoint paths (`AGGREGATE_SESSIONS_PATH`, `COMPARE_SESSIONS_PATH`, `EXPERIMENT_AGGREGATE_PATH_TEMPLATE`, `EVALUATION_*_PATH`). |
| `shared/schemas.py` | `PRESET_FIELDS`, `COMPARISON_FIELDS`, `STABILITY_SCORE_FIELDS`. |
| `shared/__init__.py` | Re-exports for all new names. |
| `cloud_backend/analytics/__init__.py` | Wires the `evaluation` submodule. |
| `cloud_backend/dashboard/api.py` | Eight new endpoints: `/api/aggregate/sessions`, `/api/compare/sessions`, `/api/experiments/{label}/aggregate`, `/api/evaluation/{pad,orientation,thermal,offload_efficiency,latency}`. |

No file moved. `edge/main.py`, `cloud/main.py`, `edge/cloud_client.py`,
`edge/offload_router.py`, `config/experiment_session.py`, the
`DIAG_COLUMNS` / `TELEMETRY_CSV_COLUMNS` / `attendance_log` headers, and
the `/verify/image` contract are unchanged.

## 3. Orchestration helpers added

| Helper | Where | What |
|--------|-------|------|
| `sweep_orchestrator.plan_runs(preset)` | offline | Turns a preset into a list of `PlannedRun` (sweep_value, protocol overrides, env overrides, suggested operator command). |
| `sweep_orchestrator.render_plan(preset, plan)` | offline | Markdown checklist for the operator. |
| `sweep_orchestrator.aggregate_after_capture(preset, sessions, out_dir)` | offline | Runs per-session analyzers + cross-session aggregation. |
| `session_aggregator.aggregate_sessions(dirs, sweep_dimension, comparison_metric_path, group_by)` | offline | 18 standard metric columns + dotted-path comparison metric + optional grouping. |
| `session_comparison.compare(baseline, modified)` | offline | 16 metric rows with delta + rel_change + tag-set diff. |

## 4. Calibration tooling added

All decision-supporting only — none of these helpers write back into
the runtime config or modify thresholds in place.

| Helper | Output | Use |
|--------|--------|-----|
| `compare_confidence_distributions(sessions, key)` | Side-by-side percentile blocks for `sim` / `live_conf`. | Confirm session-to-session consistency before sweeping. |
| `recommend_match_thresholds(session, target_matched_rate, target_offload_rate)` | Chosen `th_high` + candidate list. | Pick a threshold from a single threshold-sweep capture. |
| `recommend_hysteresis_gap(session, target_max_flip_rate)` | Current + proposed `mid_offset`. | Decide whether to widen the gap when adjacent-frame flips are high. |
| `recommend_routing_policy(sessions, max_offload_rate)` | Best-by-agreement strategy + per-session rows. | Used after `hybrid_routing_sweep`. |
| `pad_threshold_compare(sessions)` | Per-attack-type PAD label fractions. | Confusion-style table for the PAD-attack sweep. |
| `operating_point_snapshot(session)` | Snapshot of active thresholds + observed outcomes. | Attach to a calibration write-up so changes are versioned. |

## 5. Aggregation + comparison helpers added

| Endpoint | Computes |
|----------|----------|
| `GET /api/aggregate/sessions?ids=a&ids=b&ids=c` | One stabilization + quality_tag block per session id. |
| `GET /api/compare/sessions?baseline=a&modified=b` | Five-metric diff: event_count, offload rate, orientation flip rate, thermal p95, tag count. |
| `GET /api/experiments/{label}/aggregate` | Aggregated rows for every session under the given experiment label. |

The offline counterparts are richer (18-metric table, 16-metric diff)
because they read the full `stabilization_report.json` bundle that the
edge writes; the cloud endpoints summarise from the JSONL event store
to stay fast.

## 6. Research evaluation pathways added

| Endpoint | Returns |
|----------|---------|
| `GET /api/evaluation/pad?session_id=&experiment_label=&attack_type=` | `pad_confusion_matrix` — per-attack REAL/SPOOF/UNCERTAIN counts and fractions. |
| `GET /api/evaluation/orientation` | Per-mode sim mean/std/p95 + `robustness_score`. |
| `GET /api/evaluation/thermal?threshold_c=75` | Thermal p95 + FPS mean + correlation + over-threshold rate. |
| `GET /api/evaluation/offload_efficiency` | Success / total + RTT percentiles + agreement rate. |
| `GET /api/evaluation/latency` | Percentile blocks for every per-stage timing field. |

The offline equivalents already exist as
`research.analysis.threshold_sweep` (for matched/offload curves) and
`research.analysis.stabilization` (for the rest). The cloud wrappers
exist so dashboards can render the same shapes without re-running
pandas.

## 7. Comparison workflows added

- **Pairwise** — `research.analysis.session_comparison.compare(a, b)`
  emits a Markdown table of 16 metrics with absolute and relative
  deltas, plus the symmetric difference of triggered quality tags
  ("tags resolved" / "tags introduced").
- **Grouped** — `research.analysis.session_aggregator.aggregate_sessions(...,
  group_by='protocol.orientation')` produces both the side-by-side row
  table and a `grouping` map (`group_label → [session_ids]`).
- **Trend** — the sweep orchestrator's `aggregate_after_capture` output
  contains every per-session bundle plus the aggregator's table, so a
  notebook can plot the comparison metric vs sweep value with no
  additional file plumbing.

## 8. Dashboard-ready experiment summaries

All eight new endpoints return JSON shaped for direct consumption by a
future dashboard:

- `/api/aggregate/sessions` and `/api/experiments/{label}/aggregate`
  share the same row schema (`session_id`, `experiment_label`,
  `category`, `event_count`, `stabilization`, `quality_tags`) so a
  table widget can render either response without conditional logic.
- `/api/compare/sessions` returns a flat list of `{metric, value_a,
  value_b, delta}` rows — drop-in for a diff component.
- `/api/evaluation/*` responses always wrap into the existing
  `MetricResponse` Pydantic model so dashboards reuse one renderer.

`shared.contracts.EVALUATION_METRIC_KEYS` documents the ten canonical
metric names so a UI legend / metric picker doesn't have to hard-code
strings.

## 9. Validation performed

| Check | Result |
|-------|--------|
| `compileall` across the full tree | exit 0 |
| `shared` import: 6 preset names, 10 evaluation keys, stability weights sum to 1.0 | OK |
| All six preset JSON files parse and report the expected sweep dimensions | OK |
| `sweep_orchestrator.plan_runs('distance_sweep')` produces 6 `PlannedRun`s with correctly slotted protocol args | OK |
| `cloud_backend.analytics.evaluation` on 60 synthetic events: orientation robustness=1.000 (uniform sim across modes), thermal/fps correlation=-1.000 (perfectly anti-correlated by design), offload success rate=0.500, latency 4 non-empty rows, PAD confusion 1 row n=60 | OK |
| Empty-input safety for every cloud evaluation function | OK |
| Multi-session aggregator on 3 synthetic sessions: 3 rows, markdown 9 lines | OK |
| `session_comparison.compare` on two sessions: 16 metric rows | OK |
| `stability_score.compute_score` on synthetic report: 0.972, 6 components, 1 missing signal (`offload_success_rate` — expected, no offloads in synthetic data) | OK |
| `calibration.recommend_match_thresholds(target=0.50)` → `th_high=0.75` from the synthetic sweep | OK |
| `recommend_hysteresis_gap` widens the gap when the flip rate is over target | OK |
| `pad_threshold_compare` lines up REAL/SPOOF fractions per attack type | OK |
| `operating_point_snapshot` captures the session id + observed outcomes | OK |
| `verify_manifests.sh` both bundles pass | OK |
| `package_cloud.sh` tarball is 49 KB and contains all 6 `cloud_backend/analytics/*.py` modules including the new `evaluation.py` | OK |

Live runtime / camera / cloud round-trip validation is **deferred** per
the phase brief.

## 10. Experiment infrastructure changes

- **Presets are data, not code.** New sweep types are JSON drops into
  `research/experiments/presets/`. The orchestrator discovers them
  automatically via `list_presets()`.
- **Sweep value slotting is dimension-aware.** `plan_runs` knows which
  sweep dimensions map to protocol fields (e.g. `distance_m`) vs env
  vars (`CLOUD_ROUTING`) vs purely-offline knobs (`th_high`).
- **Aggregator path semantics support `[]`** — a dotted path of
  `runtime_diagnostics.orientation_vs_confidence.per_mode[].sim_mean`
  expands a list-of-dicts into `{label → value}` for direct display.
- **Stability score is descriptive only.** Operators sort by it, they
  don't act on it blindly. The `components` field always exposes the
  per-signal contributions so the score is auditable.

## 11. Unresolved stabilization risks

- **`recommend_hysteresis_gap` proposes a single linear bump.** It
  doesn't search the threshold-sweep curve for the gap that minimises
  flips — that's the next layer. Operators should still inspect the
  `match_threshold_sweep` block before applying.
- **`recommend_routing_policy` uses `agreement_rate` as the tiebreaker.**
  Other tradeoffs (RTT, success rate, accuracy on attack samples) are
  surfaced but not part of the ranking. Document the chosen objective
  in the calibration write-up.
- **`pad_confusion_matrix`** groups by the session-level `attack_type`,
  not per-event protocol. The cloud-side ingest doesn't push protocol
  per event today; if a future pass adds it, the helper can switch to
  per-event grouping without an API break.
- **`STABILITY_SCORE_WEIGHTS` are guesses.** They sum to 1.0 and are
  validated by the smoke test, but the relative weights are research-
  grade. Pressure-test on real sessions and adjust per project.
- **`/api/aggregate/sessions` walks each session's full event store.**
  Acceptable at research scale; add pagination / streaming once
  sessions go past a few hundred thousand events.

## 12. Deferred runtime validations

- Live camera capture → sweep orchestration → aggregator pipeline on
  real hardware.
- Threshold recommendation against a real `threshold_sweep` capture.
- Routing-policy comparison against three real `hybrid_routing_sweep`
  sessions.
- PAD confusion matrix against a tagged spoof dataset.
- Notebook bridge: a small `examples/` notebook that consumes
  `sweep_<preset>.json` and produces the canonical research plots.
  Hooked deliberately to a later pass so this pass stays
  infrastructure-only.
- API-side gate / threshold override (today the cloud uses
  `QUALITY_GATE_DEFAULTS` directly; query-param overrides are
  trivial to add).
