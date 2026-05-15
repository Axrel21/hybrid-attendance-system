# Experiment presets

Each JSON preset under `research/experiments/presets/` describes one
named sweep type: what varies between runs, what stays fixed, how long
each run should be, which offline analyzers to run afterwards, and
which metric to highlight in the cross-session comparison.

Run inventory (`python -m research.experiments.sweep_orchestrator --list`):

| Preset | Sweep dimension | Values | Per-run duration |
|--------|-----------------|--------|------------------|
| `threshold_sweep` | `th_high` *(post-hoc, no protocol variation)* | 8 thresholds 0.55–0.90 | 300 s (single capture) |
| `orientation_sweep` | `orientation` | `frontal`, `tilted`, `overhead` | 120 s |
| `distance_sweep` | `distance_m` | 0.5, 1.0, 1.5, 2.0, 2.5, 3.0 | 60 s |
| `lighting_sweep` | `lighting` | `bright`, `normal`, `dim`, `backlit` | 90 s |
| `pad_attack_sweep` | `attack_type` | `none`, `print`, `screen_replay`, `video_replay`, `mask_paper` | 120 s |
| `hybrid_routing_sweep` | `CLOUD_ROUTING` *(env var)* | `threshold`, `hysteresis`, `adaptive` | 180 s |

## Workflow

1. **Plan** — print the operator runbook for one preset:

```bash
python -m research.experiments.sweep_orchestrator --preset distance_sweep --plan
```

   The output is a Markdown checklist: one section per planned run with
   the suggested capture command, env vars (e.g. `CLOUD_ROUTING=...`),
   and the `research.experiment_protocol` invocation to tag the
   resulting session with its sweep value.

2. **Capture** — for each row in the plan, run `python run.py` with the
   suggested env vars, then tag the new `experiments/exp_<id>/` with
   the suggested `--attack-type` / `--distance` / `--lighting` /
   `--orientation` / `--mounting` / `--movement` flags.

3. **Aggregate** — once all captures complete, point the orchestrator at
   them:

```bash
python -m research.experiments.sweep_orchestrator \
    --preset distance_sweep \
    --sessions experiments/exp_001/ experiments/exp_002/ ...
```

   The output JSON lives under `experiments/sweep_<preset>/sweep_<preset>.json`
   and contains the per-session report bundles plus the cross-session
   `aggregation` table (see `research.analysis.session_aggregator`).

## Preset schema

| Field | Required | Notes |
|-------|----------|-------|
| `preset` | yes | Must match the filename stem. |
| `preset_version` | yes | Currently `"1.0"`. |
| `description` | yes | One-paragraph description of the sweep. |
| `sweep_dimension` | yes | The protocol field or env-var that varies. |
| `sweep_values` | yes | List of values; orchestrator generates one planned run per value. |
| `recommended_duration_s` | yes | Hint to the operator; not enforced. |
| `fixed_protocol` | yes | Protocol fields that stay the same across runs. |
| `fixed_env` | no | Env vars that stay the same. `<sweep_value>` placeholder is substituted at plan time. |
| `analysis_pipeline` | yes | List of analyzer short names (`stabilization`, `runtime_diagnostics`, `threshold_sweep`, `quality_gates`). |
| `comparison_metric` | yes | Dotted path into the per-session bundle; used by the aggregator and dashboard. |
| `notes` | no | Free-form operator hints. |

Vocab for sweep dimensions: `attack_type`, `distance_m`, `lighting`,
`orientation`, `mounting`, `movement`, `CLOUD_ROUTING`, `th_high`. Adding
a new dimension is a one-line change in `sweep_orchestrator.plan_runs`.

## Adding a new preset

1. Drop a new JSON file under `research/experiments/presets/<name>.json`
   that conforms to the schema above.
2. Add the preset name to `shared.contracts.PRESET_NAMES`.
3. (Optional) Add the dimension's slot in `plan_runs` if it's a new
   axis.
4. Document the preset here.

Soft validation only — orchestrator accepts unknown sweep dimensions and
forwards them verbatim to the protocol writer.
