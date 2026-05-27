# Soft quality gates

Tags raised by the post-session analyzers when a session deviates from
the default operating envelope. They are **soft** — they never reject a
run, abort the pipeline, or hide data. Use them to:

- Sort sessions in a dashboard by `severity` to find the worst frames
  first.
- Drop or include sessions in a comparative analysis based on tag
  presence.
- Decide when stabilization fixes are warranted vs when the data was
  fine but the operator's intuition was off.

Vocab lives in `shared.contracts.QUALITY_TAGS`. Default thresholds in
`shared.contracts.QUALITY_GATE_DEFAULTS`. Override per-tag thresholds
via the CLI (`--threshold KEY=VALUE`).

## Tag catalogue

| Tag | Source signal | Default `warn` / `alert` | What it usually means |
|-----|---------------|--------------------------|-----------------------|
| `unstable_camera` | `bbox_stability.area_cv_mean` (`gt`) | 0.30 / 0.60 | Subject moving, mount slipping, or autofocus oscillation. |
| `excessive_blur` | `blur_geometry_quality.blur.p50` (`lt`, lower = blurrier) | 80.0 / 40.0 | Motion blur, smudged lens, or focus loss. |
| `low_light` | session-wide `brightness` median (`lt`) | 60.0 / 30.0 | Insufficient illumination — YuNet and PAD degrade. |
| `excessive_proximity` | fraction of frames within 0.5 m of `MIN_DISTANCE` (`gt`) | 0.15 / 0.30 | Subject too close to the camera; depth and area estimates unstable. |
| `unstable_tracking` | `detection_persistence.mean_active_fraction` (`lt`) | 0.70 / 0.40 | Tracks losing the face, frequent re-detect. |
| `thermal_warning` | `thermal.over_threshold_rate` (`gt`) | 0.05 / 0.20 | CPU running hot — risk of throttle / FPS drop. |
| `low_confidence_run` | mean `sim` across tracks (`lt`) | 0.65 / 0.50 | Recognition is grinding; potentially enrollment drift or pose mismatch. |
| `frequent_spoof_flips` | `pad_hysteresis.overall_flip_rate` (`gt`) | 0.05 / 0.15 | PAD label oscillating; thresholds may be miscalibrated for the session. |
| `excessive_offload` | `offload_trigger.offload_trigger_rate` (`gt`) | 0.20 / 0.40 | Mid-confidence band hits too often; edge thresholds may be too tight. |
| `identity_flicker` | `identity_flicker.max_distinct` (`gt`) | 2 / 4 | A track is being assigned different identities frame-to-frame. |
| `orientation_unstable` | `orientation_stability.mode_flip_rate_mean` (`gt`) | 0.10 / 0.25 | Frame is bouncing between FRONTAL/TILTED/OVERHEAD too often. |
| `high_offload_failure` | non-`success` rate within `outcome_counts` (`gt`) | 0.20 / 0.50 | Cloud reachability or latency issue dominating the offload band. |

`gt` = larger value is worse; `lt` = smaller value is worse. Each gate
has both a `warn` and an `alert` threshold; the more severe wins.

## CLI

Offline (Pi or dev machine):

```bash
python -m research.analysis.quality_gates \
    --session experiments/exp_<id>/

# Override one threshold for an outdoor session
python -m research.analysis.quality_gates \
    --session experiments/exp_<id>/ \
    --threshold brightness_p50_alert=20.0 \
    --threshold brightness_p50_warn=40.0
```

Output: `experiments/exp_<id>/summaries/quality_tags.json` with shape:

```jsonc
{
  "session_id": "exp_20260516_120000",
  "diagnostic_csv": "...",
  "rows": 12450,
  "tags": [
    { "tag": "excessive_offload", "severity": "warn", "value": 0.34,
      "threshold": 0.20, "detail": "fraction of frames triggering cloud offload" },
    ...
  ],
  "tag_count": 4,
  "by_severity": { "info": 0, "warn": 3, "alert": 1 },
  "thresholds": { "bbox_area_cv_warn": 0.30, ... }
}
```

## Cloud-side equivalent

`cloud_backend.analytics.quality.evaluate(events)` mirrors the offline
helper over the JSONL event stream. Exposed via:

```
GET /api/metrics/quality_tags?session_id=...&experiment_label=...
GET /api/sessions/{session_id}/quality_tags
```

Tag shape is identical. The cloud uses the same
`QUALITY_GATE_DEFAULTS` (overridable in code for now; no API for
overrides yet — deferred).

## Adding a new tag

1. Add the tag name to `shared.contracts.QUALITY_TAGS` and the
   threshold keys to `QUALITY_GATE_DEFAULTS`.
2. Compute the input value in either `research.analysis.runtime_diagnostics`
   or `research.analysis.stabilization` (whichever is more natural).
3. Append the corresponding `_eval_pair` call in
   `research.analysis.quality_gates.evaluate_metrics` and in
   `cloud_backend.analytics.quality.evaluate`.
4. Document the gate in this file.

Soft semantics only — never reject a frame or session based on a tag.
