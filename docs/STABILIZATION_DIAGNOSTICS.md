# Stabilization diagnostics

Reference for the offline and cloud-side metric helpers introduced in
pass 5. Pair with [`TELEMETRY.md`](TELEMETRY.md) for raw CSV / JSONL
schemas and [`EXPERIMENT_PROTOCOL.md`](EXPERIMENT_PROTOCOL.md) for
session annotations.

The brief required eight stabilization dimensions: orientation,
temporal confidence, detection persistence, blur + geometry, bounding
box, recognition drift, PAD temporal, and offload triggers. All eight
are computed offline from `diagnostic_log.csv` and (a subset) at runtime
from the cloud event stream.

## Two consumption paths

| Use case | Module |
|----------|--------|
| Offline, per-session, against a local `experiments/exp_<id>/`  | `research.analysis.stabilization`, `research.analysis.threshold_sweep` (pandas-based) |
| Cloud, on the ingested JSONL events for one or many sessions | `cloud_backend.analytics.stabilization`, `cloud_backend.analytics.calibration` (numpy-only) |

Both compute the same conceptual metrics; the cloud path is a subset
because it operates on the event-stream projection (each diagnostic row
becomes one event with `fields={...}`).

## Offline driver

```bash
# Eight-dimension stabilization summary -> summaries/stabilization.json
python -m research.analysis.stabilization \
    --session experiments/exp_20260516_120000

# Threshold-sweep what-if -> summaries/threshold_sweep.json
python -m research.analysis.threshold_sweep \
    --session experiments/exp_20260516_120000 \
    --th-high-min 0.55 --th-high-max 0.90 --steps 21 --mid-offset 0.15
```

`stabilization.json` shape:

```jsonc
{
  "diagnostic_csv": "experiments/exp_.../diagnostics/diagnostic_log.csv",
  "rows": 12450,
  "orientation_stability": {
    "n_tracks": 7,
    "mode_flip_rate_mean": 0.062,
    "orient_ratio_std_mean": 0.041,
    "per_track": [{ "track_id": 1, "frames": 1832, "mode_flip_count": 12,
                     "mode_flip_rate": 0.0065, "orient_ratio_std": 0.028 }, ...]
  },
  "confidence_stability": { "n_tracks": 7, "sim_std_mean": 0.073,
                            "per_track": [...] },
  "detection_persistence": { "n_tracks": 7, "mean_active_fraction": 0.93,
                             "per_track": [...] },
  "bbox_stability": { "n_tracks": 7, "area_cv_mean": 0.084, "per_track": [...] },
  "recognition_drift": { "n_identities": 4, "max_abs_slope": 0.0021,
                         "per_identity": [...] },
  "blur_geometry_quality": { "n": 12450,
                             "blur": { "n": 12450, "mean": 142.3, ... },
                             "face_area": { ... },
                             "distance_m": { ... } },
  "pad_temporal": { "overall": { "real_fraction": 0.91, "spoof_fraction": 0.06,
                                  "uncertain_fraction": 0.03, "n": 12450 },
                    "per_track": [...] },
  "offload_trigger": { "n": 12450, "offload_trigger_count": 184,
                       "offload_trigger_rate": 0.0148,
                       "outcome_counts": { "success": 167, "timeout": 12,
                                            "skipped_circuit_breaker": 5 },
                       "agreement_n": 167, "agreement_rate": 0.892,
                       "rtt_ms": { "mean": 38.7, "p95": 64.2, ... } },
  "thermal": { "n": 12450, "mean": 61.4, "p95": 73.1,
               "threshold_c": 75.0, "over_threshold_rate": 0.018 }
}
```

`threshold_sweep.json` shape:

```jsonc
{
  "diagnostic_csv": "...",
  "rows": 12450,
  "match_threshold_sweep": [
    { "th_high": 0.55, "th_mid": 0.40, "n": 12450,
      "matched_count": 9120, "offload_count": 1832, "below_threshold_count": 1498,
      "matched_rate": 0.732, "offload_rate": 0.147, "below_threshold_rate": 0.120 },
    ...
  ],
  "offload_threshold_sweep": [...],
  "hysteresis": { "n_tracks": 7, "overall_flip_rate": 0.083,
                   "per_track": [...] },
  "sim_distribution": { "n": 12450, "mean": 0.74, "histogram": {...} },
  "live_conf_distribution": { ... }
}
```

## Cloud-side endpoints

All hit the JSONL event store. They accept `?session_id=` and/or
`?experiment_label=`; with no scope they aggregate across all stored
sessions (capped at 50k events).

| Endpoint | Returns |
|----------|---------|
| `GET /api/metrics/stabilization` | Bundled orientation + confidence + PAD + thermal + bbox. |
| `GET /api/metrics/orientation` | Mode flip rate + orient_ratio std per track. |
| `GET /api/metrics/pad` | Overall + per-track REAL / SPOOF / UNCERTAIN fractions. |
| `GET /api/metrics/thermal?threshold_c=75` | CPU temp percentiles + over-threshold rate. |
| `GET /api/metrics/threshold_sweep?th_high_min=&th_high_max=&steps=&mid_offset=` | Synthetic decision counts under different match thresholds. |
| `GET /api/metrics/confidence_distribution?key=sim&bins=20` | Percentile block + histogram for any numeric `fields` key. |
| `GET /api/sessions/{id}/protocol` | Raw experiment-protocol sidecar. |
| `GET /api/sessions/{id}/category` | Derived canonical category. |
| `GET /api/metrics/agreement` *(pass 4)* | Edge/cloud agreement rate. |
| `GET /api/metrics/offload` *(pass 4)* | Offload-outcome distribution. |
| `GET /api/metrics/latency?key=cloud_rtt_ms` *(pass 4)* | Latency percentiles. |

Metric-key vocabulary lives in `shared.contracts.STABILIZATION_METRIC_KEYS`.

## What this does **not** do

- Plot anything. The brief explicitly scopes this pass to "infrastructure
  and data readiness, not final plots".
- Modify the edge runtime or the `diagnostic_log.csv` schema. Both
  helpers compute on the existing fields.
- Aggregate across files automatically — the dashboard endpoints expose
  cross-experiment views, but multi-session JSON exports are still a
  caller responsibility.
- Persist computed summaries. Each call recomputes from the source.
  `summaries/stabilization.json` is the only on-disk projection and is
  produced by the offline CLI on demand.
