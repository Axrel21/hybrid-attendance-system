# Experiment-protocol metadata

Per-session reproducibility sidecar introduced in pass 5. Lives at:

```
experiments/exp_<id>/config/experiment_protocol.json
```

alongside the existing `settings_snapshot.json`. The edge runtime does
not read it; it is purely metadata. The edge telemetry uploader picks
it up (if present) and forwards it as the `protocol` sub-dict of the
session-start payload, so it lands in cloud-side
`cloud_storage/sessions/<id>/metadata.json`.

## Writing a protocol

```bash
python -m research.experiment_protocol \
    --session experiments/exp_20260516_120000 \
    --attack-type print \
    --distance 2.0 \
    --lighting normal \
    --orientation frontal \
    --mounting tripod_eye_level \
    --movement static \
    --dataset-label classroom_pilot_03 \
    --operator nikhil \
    --target-identities student_001,student_002,student_003 \
    --notes "phone photo, 1080p screen, 50cm from camera"
```

Use `--allow-unknown` to bypass vocabulary checks when piloting a new
attack-type or mounting that isn't in `shared.contracts` yet.

Validate an existing file:

```bash
python -m research.experiment_protocol \
    --validate experiments/exp_20260516_120000/config/experiment_protocol.json
```

## Schema

Source of truth: `research.experiment_protocol.ExperimentProtocol`
dataclass. Mirrored in `shared.schemas.EXPERIMENT_PROTOCOL_FIELDS`.

| Field | Type | Notes |
|-------|------|-------|
| `protocol_version` | str | Always `"1.0"` in this pass; bump when the wire shape changes. |
| `session_id` | str | Matches `experiments/exp_<id>/`. Auto-filled if omitted. |
| `experiment_label` | str | Free-text — mirrors `EXPERIMENT_LABEL` env var when set. |
| `attack_type` | str \| null | One of `ATTACK_TYPES`: `none`, `print`, `screen_replay`, `video_replay`, `mask_paper`, `mask_silicone`, `mask_resin`, `deepfake`, `occlusion`, `other`. |
| `distance_m` | float \| null | Standing distance in meters. Validated against (0, 20). |
| `lighting` | str \| null | One of `LIGHTING_LABELS`: `bright`, `normal`, `dim`, `backlit`, `side_lit`, `uneven`, `outdoor_sunny`, `outdoor_cloudy`. |
| `orientation` | str \| null | One of `ORIENTATION_LABELS`: `frontal`, `tilted`, `overhead`, `side`, `mixed`. |
| `mounting` | str \| null | One of `MOUNTING_LABELS`: `tripod_eye_level`, `tripod_overhead`, `wall_mount`, `ceiling_mount`, `desk_clip`, `handheld`, `other`. |
| `movement` | str \| null | One of `MOVEMENT_LABELS`: `static`, `slow_walk`, `fast_walk`, `approach`, `retreat`, `lateral`, `rotation`, `mixed`. |
| `dataset_label` | str \| null | Free-text identifier for the data collection batch. |
| `operator` | str \| null | Who ran the session. Defaults to `$USER`. |
| `target_identities` | list[str] | Expected identity labels — used downstream for FAR/FRR labeling. |
| `environment` | str \| null | Free-text (`"classroom"`, `"lab"`, `"outdoor_courtyard"`, ...). |
| `notes` | str \| null | Free-text. |
| `recorded_at` | str | ISO-8601 timestamp the protocol was committed. |

Unknown / new fields are dropped on load (forward-compatible).

## Categorization

`cloud_backend.experiments.registry.categorize_session(metadata)` derives
a canonical short key from the protocol:

```
{orientation}_{attack_class}_{lighting}_{distance_bucket}
```

- `attack_class` = `"genuine"` if `attack_type == "none"`, else
  `attack_type`. `"unknown"` if missing.
- `distance_bucket` = `"close"` (<1.0 m), `"mid"` (1.0–2.5 m),
  `"far"` (>2.5 m), `"unknown"` otherwise.

The category is served at `GET /api/sessions/{id}/category` and is
intended for dashboard grouping (no UI in this pass).

## Lifecycle

```
operator         research/experiment_protocol.py
   │
   ▼
experiments/exp_<id>/config/experiment_protocol.json
   │
   ▼  python -m edge.telemetry_uploader --session ... --cloud ...
   │
   ▼
cloud_storage/sessions/<id>/metadata.json    (under "protocol" sub-dict)
   │
   ▼
GET /api/sessions/<id>/protocol              (raw protocol JSON)
GET /api/sessions/<id>/category              (derived category)
```
