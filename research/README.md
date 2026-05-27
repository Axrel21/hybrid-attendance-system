# Research tooling

Offline analysis, dataset preprocessing, and tagged experiment launchers. **Not required** on a minimal Raspberry Pi edge bundle (see `docs/DEPLOYMENT.md`).

## Layout

| Path | Purpose |
|------|---------|
| `research/analysis/orientation.py` | Orientation threshold / pose telemetry figures |
| `research/analysis/pi_perf.py` | Pi performance / latency / thermal plots |
| `research/analysis/liveness_diag_print.py` | Printed PAD / liveness summary |
| `research/analysis/attendance_latency_offload.py` | Quick latency + offload-rate stats |
| `research/dataset_preprocess.py` | Raw → aligned WEBP for enrollment |
| `research/experiments/orientation_launcher.py` | Tagged live capture (`EXPERIMENT_LABEL`) |
| `research/tools/smoke_dev_env.py` | Ad-hoc camera / YuNet / TFLite smoke test |

## Backward-compatible shims (repo root)

`analyze_*.py`, `preprocess_dataset.py`, `test_env.py` forward to the modules above so existing notes and scripts keep working.

## Running

```bash
python analyze_orientation.py --diag path/to/diagnostic_log.csv
python -m research.analysis.pi_perf --help
python preprocess_dataset.py --raw dataset_raw --out dataset_processed
python -m experiments.run_orientation_experiment my_label --notes "..."
```

Imports `config.settings` where needed (`orientation` plots) via repo root on `sys.path`.
