# Edge runtime stabilization readiness — change summary

Sixth pass on the `deployment-refactor` branch. Goal: get the existing
runtime ready for **future** controlled experiments — interpretable
signals, soft quality tagging, and a one-shot report bundler — without
modifying any edge-runtime code path or CSV schema.

Pair with [`QUALITY_GATES.md`](QUALITY_GATES.md),
[`RUNTIME_DIAGNOSTICS.md`](RUNTIME_DIAGNOSTICS.md), and prior per-pass
summaries.

## 1. Files added

| Path | Purpose |
|------|---------|
| `research/analysis/runtime_diagnostics.py` | Gap-filling diagnostics for YuNet (proximity, missed-detection, unstable-track), recognition (identity flicker, track summary, orientation/distance vs sim), PAD (rigid ratio temporal, spoof transitions, replay-pattern, hysteresis). |
| `research/analysis/quality_gates.py` | Soft quality-tag evaluator: `evaluate_metrics(stabilization, runtime, thresholds)` + `evaluate_session(session_dir)`. Writes `summaries/quality_tags.json`. |
| `research/analysis/stabilization_report.py` | Combined bundler: runs every offline analyzer plus the protocol loader and emits one `stabilization_report.json` + Markdown. |
| `cloud_backend/analytics/quality.py` | Cloud-side mirror of the gate evaluator over the JSONL event stream. |
| `docs/QUALITY_GATES.md` | Tag catalogue + threshold reference + CLI usage. |
| `docs/RUNTIME_DIAGNOSTICS.md` | Function-by-function reference. |
| `docs/runtime_stabilization_readiness_summary.md` | This file. |

## 2. Files modified

| Path | Change |
|------|--------|
| `shared/contracts.py` | `QUALITY_TAGS` (12 tag names), `QUALITY_SEVERITIES` (`info`/`warn`/`alert`), `QUALITY_GATE_DEFAULTS` (24 threshold keys), `METRICS_QUALITY_TAGS_PATH`, `SESSION_QUALITY_TAGS_PATH_TEMPLATE`. Added `Dict` to typing imports. |
| `shared/schemas.py` | `QUALITY_TAG_FIELDS` (5-field row shape). |
| `shared/__init__.py` | Re-exports for all new names. |
| `cloud_backend/analytics/__init__.py` | Wires the `quality` submodule. |
| `cloud_backend/dashboard/api.py` | Two new endpoints: `GET /api/metrics/quality_tags` and `GET /api/sessions/{id}/quality_tags`. |

No file moved. `edge/main.py`, `cloud/main.py`, `edge/cloud_client.py`,
`edge/offload_router.py`, `config/experiment_session.py`, the
`DIAG_COLUMNS` / `TELEMETRY_CSV_COLUMNS` / `attendance_log` headers, and
the `/verify/image` contract are unchanged.

## 3. Diagnostics added

### YuNet (Task A)

- `proximity_diagnostics` — fraction of frames near MIN/MAX bounds, full distance percentile block.
- `missed_detection_diagnostics` — NO_MATCH / OUT_OF_RANGE / decision distribution.
- `unstable_track_diagnostics` — short-lived tracks flagged with frame counts.

### Recognition (Task B)

- `identity_flicker` — distinct identities per track + max across tracks.
- `track_recognition_summary` — per-track sim mean/std/p95, dominant identity, recognised fraction.
- `orientation_vs_confidence` — sim by orientation mode.
- `distance_vs_confidence` — sim by distance bucket.

### PAD/Liveness (Task C)

- `rigid_ratio_temporal` — per-track mean/std/p95 of rigid_ratio.
- `spoof_transitions` — REAL ↔ SPOOF ↔ UNCERTAIN transition matrix per track.
- `replay_pattern_diagnostics` — area_var and blur statistics, with sub-threshold rates as still-frame proxies.
- `pad_hysteresis` — adjacent-frame label flip rate, per track and overall.

### Orientation & geometry (Task D)

- Covered by `orientation_vs_confidence` + `distance_vs_confidence` from runtime_diagnostics, plus the existing `orientation_stability` from pass 5.

## 4. Telemetry additions

All optional, sidecar JSON only. **No CSV schema changes.**

| Path | Writer | Contents |
|------|--------|----------|
| `experiments/exp_<id>/summaries/runtime_diagnostics.json` | `research.analysis.runtime_diagnostics` CLI | YuNet + recognition + PAD + orientation gap-fillers. |
| `experiments/exp_<id>/summaries/quality_tags.json` | `research.analysis.quality_gates` CLI | List of triggered tags + severity + thresholds. |
| `experiments/exp_<id>/summaries/stabilization_report.json` | `research.analysis.stabilization_report` CLI | One-shot bundle (protocol + stabilization + runtime + threshold sweep + tags). |
| `experiments/exp_<id>/summaries/stabilization_report.md` | Same CLI (unless `--no-md`) | Short human-readable summary — tag table + headline metrics + sweep snapshot. |

## 5. Quality tags added

Twelve tags, three severities, twenty-four configurable thresholds. See
[`QUALITY_GATES.md`](QUALITY_GATES.md) for the catalogue. Highlights:

- Capture quality: `unstable_camera`, `excessive_blur`, `low_light`, `excessive_proximity`.
- Tracking: `unstable_tracking`, `identity_flicker`.
- Recognition: `low_confidence_run`.
- PAD: `frequent_spoof_flips`.
- Hybrid: `excessive_offload`, `high_offload_failure`.
- Orientation: `orientation_unstable`.
- Thermal: `thermal_warning`.

Severity escalates `warn` → `alert`. Override per-tag thresholds via
`--threshold KEY=VALUE` on the CLI; the cloud side reuses
`shared.contracts.QUALITY_GATE_DEFAULTS` directly (API override is
deferred).

## 6. Reporting additions

`research.analysis.stabilization_report` produces a single Markdown +
JSON pair that:

- Quotes the protocol sidecar (attack type, distance, lighting, ...) at
  the top so the dashboard / report reader knows what the session was
  trying to do.
- Lists every triggered quality tag in a table with severity, value,
  threshold, and detail.
- Reports six headline metrics (active fraction, PAD breakdown, offload
  rate, thermal p95, mode-flip rate, track count).
- Includes a six-point match-threshold sweep snapshot.

The combined report is the cheapest way to triage a session — open one
Markdown file, see all of it.

## 7. APIs added

```
GET /api/metrics/quality_tags?session_id=&experiment_label=
GET /api/sessions/{session_id}/quality_tags
```

Both return the same payload shape as the offline JSON (`tags`,
`tag_count`, `by_severity`) so dashboards can render the offline file
or the cloud response identically.

## 8. Observability improvements

- Tags carry **evidence** — every tag includes the metric value and the
  threshold it crossed, not just a flag. Operators don't have to dig
  through other files to understand why.
- All offline summaries are JSON — they round-trip cleanly into the
  cloud event store via the existing uploader without schema changes
  (they appear as files in `experiments/exp_<id>/summaries/` and can be
  uploaded as plain telemetry events if a future pass adds that wiring).
- Vocabulary lives in one place: `shared.contracts` for tags +
  thresholds, `shared.schemas` for row shapes. Dashboards built later
  can render any new tag automatically by reading those constants.
- The Markdown report is intentionally Pi-readable: works fine over SSH
  with no plotting stack installed; pandas + numpy are the only deps
  (both already pinned for the edge bundle).

## 9. Validation performed

| Check | Result |
|-------|--------|
| `compileall` across shared / config / edge / cloud / cloud_backend / research / experiments | exit 0 |
| `shared` import: 12 new tag names, 24 thresholds, 5-field tag row shape | OK |
| `runtime_diagnostics.diagnose_session` on 30-row synthetic CSV | proximity close_fraction=0.167, identity_flicker max_distinct=2, distance bins=5, spoof_transitions total=14, pad_hysteresis flip_rate=0.519 |
| `quality_gates.evaluate_session` on stress CSV (low light + close + blurry + alternating PAD) | 7 tags raised: 6 alert + 1 warn — covers `excessive_blur`, `excessive_proximity`, `low_light`, `low_confidence_run`, `thermal_warning`, `excessive_offload`, `high_offload_failure` |
| `cloud_backend.analytics.quality.evaluate` on equivalent event stream | 7 tags raised (parity with offline after adding `excessive_blur` to the cloud helper) |
| `stabilization_report.build_report` on 50-row synthetic CSV + protocol sidecar | report rows=50, 3 tracks, 1 warn tag, protocol attack_type='print' surfaced; Markdown renders 38 lines |
| `verify_manifests.sh` | Both bundles OK |
| `package_cloud.sh` tarball | 45 KB; includes all 5 `cloud_backend/analytics/*.py` modules (`calibration`, `metrics`, `quality`, `stabilization`, `__init__`) |

Live runtime / camera / cloud round-trip validation is **deferred** per
the brief.

## 10. Unresolved stabilization risks

- **`QUALITY_GATE_DEFAULTS` are research-grade guesses.** They are
  liberal (tag aggressively); operators should tune them against the
  first batch of real sessions. Override via the CLI `--threshold`
  flag; persist project-specific defaults in a future pass.
- **`replay_pattern_diagnostics` is heuristic.** It treats
  `avg_area_var < 100` and `avg_blur < 80` as "still frame" proxies.
  Lacks ground-truth labels until a tagged spoof dataset exists.
- **`identity_flicker.max_distinct` does not know about cooldowns.**
  An identity legitimately re-appearing after a 5-minute cooldown
  counts as flicker. Acceptable at research scale; revisit when
  cooldown semantics matter.
- **Cloud-side gate overrides** are not exposed via the API. Today the
  cloud `evaluate()` always uses `QUALITY_GATE_DEFAULTS`. Adding a
  query-param-driven override is straightforward and deferred.
- **`stabilization_report.md` snapshot** truncates the threshold sweep
  to ~6 points. Useful for triage; the full sweep is in the JSON.

## 11. Deferred runtime validations

- Live camera capture → CSV → analyzer → tag pipeline against a real
  session.
- Real-world threshold calibration once `QUALITY_GATE_DEFAULTS` have
  been pressure-tested.
- Tagged spoof dataset for `replay_pattern_diagnostics` ground truth.
- Cross-session correlation: do `unstable_camera` tags cluster with
  specific mounting types reported in the protocol sidecar?
- Live WebSocket consumer that subscribes to the quality-tag endpoint.
- Optional automatic invocation of `stabilization_report` after each
  `edge.telemetry_uploader` run.
