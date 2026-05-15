# Stabilization & experimentation phase — change summary

Fifth pass on the `deployment-refactor` branch. Goal: make the
already-running system **measurable, interpretable, and analytically
reproducible** before any tuning pass. Edge runtime, telemetry CSV
schemas, hybrid offload contract, and deployment topology are
untouched. Pair with [`stabilization_diagnostics`](STABILIZATION_DIAGNOSTICS.md),
[`EXPERIMENT_PROTOCOL`](EXPERIMENT_PROTOCOL.md), and the prior per-pass
summaries.

## 1. Files added

### Edge / research

| Path | Purpose |
|------|---------|
| `research/experiment_protocol.py` | `ExperimentProtocol` dataclass + CLI (`python -m research.experiment_protocol`) that writes `experiments/exp_<id>/config/experiment_protocol.json`. Optional sidecar — runtime ignores it. |
| `research/analysis/stabilization.py` | Eight-dimension offline summary from `diagnostic_log.csv`. CLI emits `summaries/stabilization.json`. Pandas-based. |
| `research/analysis/threshold_sweep.py` | Threshold what-if + hysteresis flip-flop counter from the same CSV. CLI emits `summaries/threshold_sweep.json`. |

### Cloud backend

| Path | Purpose |
|------|---------|
| `cloud_backend/analytics/stabilization.py` | Stabilization metrics over the JSONL event stream (orientation, confidence, PAD, thermal, bbox). Numpy-only. |
| `cloud_backend/analytics/calibration.py` | `threshold_sweep`, `hysteresis_count`, `confidence_distribution` helpers. |

### Docs

| Path | Purpose |
|------|---------|
| `docs/EXPERIMENT_PROTOCOL.md` | Schema, lifecycle, CLI examples. |
| `docs/STABILIZATION_DIAGNOSTICS.md` | Offline + cloud diagnostic surfaces. |
| `docs/stabilization_infrastructure_phase_summary.md` | This file. |

## 2. Files modified

| Path | Change |
|------|--------|
| `shared/contracts.py` | New endpoint paths (`METRICS_STABILIZATION_PATH`, `METRICS_ORIENTATION_PATH`, `METRICS_PAD_PATH`, `METRICS_THERMAL_PATH`, `METRICS_THRESHOLD_SWEEP_PATH`, `METRICS_CONFIDENCE_DISTRIBUTION_PATH`, `SESSION_PROTOCOL_PATH_TEMPLATE`, `SESSION_CATEGORY_PATH_TEMPLATE`), vocabularies (`ATTACK_TYPES`, `LIGHTING_LABELS`, `ORIENTATION_LABELS`, `MOUNTING_LABELS`, `MOVEMENT_LABELS`), `STABILIZATION_METRIC_KEYS`, `EXPERIMENT_PROTOCOL_VERSION`. |
| `shared/schemas.py` | `EXPERIMENT_PROTOCOL_FIELDS`, `SESSION_CATEGORY_FIELDS`. |
| `shared/__init__.py` | Re-exports for all new constants. |
| `cloud_backend/analytics/__init__.py` | Adds `stabilization` and `calibration` submodules to the public namespace. |
| `cloud_backend/schemas.py` | Adds optional `protocol: Dict` to `SessionStartRequest` (forward-compatible — older clients omit it). |
| `cloud_backend/experiments/registry.py` | Adds `categorize_session(metadata)` + `session_protocol()` / `session_category()` methods on `ExperimentRegistry`. |
| `cloud_backend/experiments/__init__.py` | Re-exports `categorize_session`. |
| `cloud_backend/dashboard/api.py` | Six new metric endpoints (stabilization, orientation, pad, thermal, threshold_sweep, confidence_distribution) plus `GET /api/sessions/{id}/{protocol,category}`. |
| `edge/telemetry_uploader.py` | `SessionPaths` gains `experiment_protocol` path. `build_session_start` reads the sidecar (if present) and passes it as `protocol` to the cloud. Backward-compatible. |

No file moved. `edge/main.py`, `cloud/main.py`, `edge/cloud_client.py`,
`edge/offload_router.py`, `config/experiment_session.py`, the
`DIAG_COLUMNS` / `TELEMETRY_CSV_COLUMNS` / `attendance_log` headers, and
the `/verify/image` contract are unchanged.

## 3. Telemetry fields added

All optional and sidecar — no edits to existing CSVs.

### `experiments/exp_<id>/config/experiment_protocol.json` (new)

Source of truth: `research.experiment_protocol.ExperimentProtocol`.
Mirrored as `shared.schemas.EXPERIMENT_PROTOCOL_FIELDS`. Fifteen fields
covering attack-type, distance, lighting, orientation, mounting,
movement, dataset, operator, target identities, environment, notes,
plus `protocol_version` / `session_id` / `experiment_label` /
`recorded_at` bookkeeping.

### `experiments/exp_<id>/summaries/stabilization.json` (new, CLI-generated)

Eight-dimension stabilization summary plus thermal block. Shape
documented in `STABILIZATION_DIAGNOSTICS.md`.

### `experiments/exp_<id>/summaries/threshold_sweep.json` (new, CLI-generated)

Match-threshold + offload-threshold sweeps, hysteresis flip-flops,
confidence distribution.

### Cloud-side session metadata (additive)

`SessionStartRequest.protocol: Dict[str, Any] | None` lands in
`cloud_storage/sessions/<id>/metadata.json` under the `protocol` key
when the edge uploader finds an `experiment_protocol.json` sidecar.

## 4. Diagnostics added

### Offline (research) — pandas

`research.analysis.stabilization` exports:

- `orientation_stability(df)` — per-track mode flip rate + ratio std.
- `confidence_stability(df, window=30)` — rolling std of `sim`.
- `detection_persistence(df)` — active fraction + max contiguous run.
- `bbox_stability(df)` — `face_w*face_h` coefficient of variation.
- `recognition_drift(df)` — per-identity similarity slope.
- `blur_geometry_quality(df)` — percentile blocks for `avg_blur`,
  face area, distance.
- `pad_temporal_summary(df)` — overall + per-track REAL/SPOOF/UNCERTAIN.
- `offload_trigger_summary(df)` — offload rate + outcome distribution +
  agreement rate + RTT percentiles.
- `thermal_summary(df, threshold_c=75)` — CPU temp percentiles.
- `summarize_session(diagnostic_csv)` — bundle of the above.

`research.analysis.threshold_sweep`:

- `match_threshold_sweep`, `offload_threshold_sweep`,
  `hysteresis_diagnostics`, `confidence_distribution`,
  `sweep_session`.

### Online (cloud) — numpy

`cloud_backend.analytics.stabilization`:

- `orientation_stability`, `confidence_stability`, `pad_temporal`,
  `thermal_stats`, `bbox_stability`, `stabilization_summary`.

`cloud_backend.analytics.calibration`:

- `confidence_distribution`, `threshold_sweep`, `hysteresis_count`.

All accept empty input and return `{"n": 0, ...}` instead of raising.

## 5. APIs added

| Method | Path |
|--------|------|
| GET | `/api/metrics/stabilization` |
| GET | `/api/metrics/orientation` |
| GET | `/api/metrics/pad` |
| GET | `/api/metrics/thermal?threshold_c=` |
| GET | `/api/metrics/threshold_sweep?th_high_min=&th_high_max=&steps=&mid_offset=` |
| GET | `/api/metrics/confidence_distribution?key=&bins=` |
| GET | `/api/sessions/{id}/protocol` |
| GET | `/api/sessions/{id}/category` |

All read-only, scoped via `?session_id=` and `?experiment_label=`. No
authentication added — research scale, intentional. Add an auth proxy
before any non-LAN deployment.

## 6. Analytics pathways added

```
edge.main → diagnostic_log.csv          (existing schema, unchanged)
                │
                ▼
   research.analysis.stabilization      (offline, pandas)
   research.analysis.threshold_sweep
                │
                ▼
   experiments/exp_<id>/summaries/stabilization.json
   experiments/exp_<id>/summaries/threshold_sweep.json
                │
                ▼
   edge.telemetry_uploader              (existing, unchanged)
                │  + experiment_protocol.json sidecar (new)
                ▼
   cloud_backend storage
                │
                ▼
   cloud_backend.analytics.stabilization  (online, numpy)
   cloud_backend.analytics.calibration
                │
                ▼
   /api/metrics/*    /api/sessions/{id}/{protocol,category}
```

## 7. Infrastructure changes

- **Storage**: `cloud_storage/` schema unchanged. The `protocol`
  sub-dict simply becomes a new key inside `metadata.json`. Old
  sessions without a sidecar continue to work; their
  `/api/sessions/{id}/protocol` returns
  `{"session_id": ..., "protocol": null, "detail": "session has no
  experiment_protocol.json sidecar"}`.
- **Bundles**: no change. `cloud_backend/` was already in
  `CLOUD_BUNDLE.txt`; the new submodule files are picked up
  automatically because the four subpackage directories
  (`analytics/`, `dashboard/`, `experiments/`, `telemetry/`) are
  explicitly listed.
- **Dependencies**: no change. The offline analyzers use the already-pinned
  `pandas` and `numpy` (`edge/requirements-edge.txt`). Cloud analytics
  use `numpy` (already in `cloud/requirements.txt`).

## 8. Schema changes

- **Existing CSV schemas**: unchanged. `DIAG_COLUMNS`,
  `TELEMETRY_CSV_COLUMNS`, `attendance_log` header all identical to
  pass-4 state.
- **New JSON sidecars**: documented above.
- **Pydantic**: `SessionStartRequest.protocol` is the only model change
  — additive optional field.

## 9. Deployment implications

- Cloud bundle now ships two extra `cloud_backend/analytics/*.py`
  modules. `verify_manifests.sh` continues to pass; the tarball grows
  from 36 KB to 42 KB.
- New canonical workflow (developer choice — none of this is required
  for the edge runtime to function):

  ```bash
  # 1. Annotate the session
  python -m research.experiment_protocol \
      --session experiments/exp_<id>/ --attack-type print ...

  # 2. Offline analysis (no cloud required)
  python -m research.analysis.stabilization --session experiments/exp_<id>/
  python -m research.analysis.threshold_sweep --session experiments/exp_<id>/

  # 3. Upload (cloud backend running)
  python -m edge.telemetry_uploader \
      --session experiments/exp_<id>/ --cloud http://cloud:8000

  # 4. Inspect online
  curl http://cloud:8000/api/metrics/stabilization?session_id=exp_<id>
  curl http://cloud:8000/api/sessions/exp_<id>/protocol
  curl http://cloud:8000/api/sessions/exp_<id>/category
  ```

- No deployment-script or systemd-unit changes needed.

## 10. Unresolved risks

- **`STABILIZATION_METRIC_KEYS` is documentation, not enforcement.**
  The actual key names in the JSON payloads are produced by the
  per-function code; if a future change renames a key the constant
  must be updated in lockstep.
- **Vocabulary drift.** `ATTACK_TYPES` / `LIGHTING_LABELS` etc. are
  closed sets only in the CLI (`--allow-unknown` bypasses). Operators
  can write arbitrary strings; categorization will still work but the
  dashboard grouping degrades.
- **Recognition drift slope** (`per_identity.sim_slope_per_frame`) uses
  row-index as time. If the diagnostic CSV is sparse for one identity
  (long gaps between observations), the slope is biased. Documented
  but not corrected — needs a real-time-axis fit when sessions get
  longer.
- **Threshold sweep is point-wise**, not joint. It treats `sim` as if
  it were the only variable; in practice the recognition stack also
  applies adaptive thresholds via `PipelineController.get_adaptive_threshold`.
  The sweep is therefore a **simulated lower bound** on what threshold
  tuning could achieve — useful as a starting point, not as a final
  calibration.
- **`/api/metrics/*` with no scope walks every session.** Capped at 50k
  events (pass-4 limit) but still grows with storage. Add pagination
  before any deployment with >100 sessions.

## 11. Deferred validation items

- Live edge → cloud round-trip with a populated `experiment_protocol.json`.
- Real-data validation that the eight stabilization dimensions
  correlate with the issues the brief flagged (motion blur,
  unstable confidence, drift). Synthetic tests in this pass only
  confirm numerical correctness.
- ROC / EER analysis with target-identity labels from the protocol
  sidecar. Infrastructure is ready (`per_identity` summary in
  `recognition_drift`, `target_identities` field in the protocol);
  joining them is a notebook task.
- Auth proxy in front of `/api/*`. Open-by-design at research scale.
- WebSocket consumer for live stabilization (the `/ws/telemetry` hub
  exists; a stabilization-aware client subscriber is future work).
- Optional inline runtime hook in `edge/main.py` to compute a small
  set of stabilization signals in the hot path. Deliberately deferred:
  the post-session pipeline is sufficient for the current phase.
