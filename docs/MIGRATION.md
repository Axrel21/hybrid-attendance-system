# Repository reorganization (migration notes)

This document summarizes a **non-destructive** layout change focused on deployment boundaries and research-tool isolation. **Runtime semantics** (telemetry schemas, hybrid offload API, experiment session paths) are unchanged.

## What moved

| Before | After |
|--------|--------|
| `analyze_orientation.py` (monolithic) | Implementation: `research/analysis/orientation.py` — root file is a **shim** |
| `analyze_pi_perf.py` | `research/analysis/pi_perf.py` + root shim |
| `analyze_diag.py` | `research/analysis/liveness_diag_print.py` + root shim |
| `analyze_results.py` | `research/analysis/attendance_latency_offload.py` + root shim |
| `preprocess_dataset.py` | `research/dataset_preprocess.py` + root shim |
| `test_env.py` | `research/tools/smoke_dev_env.py` + root shim |
| `experiments/run_orientation_experiment.py` | Implementation: `research/experiments/orientation_launcher.py` — `experiments/` entry remains for `python -m experiments.run_orientation_experiment` |
| `deployment/attendance.service`, `pi_setup.sh`, `OPENCV_GUI_*.md`, `validate_opencv_gui.py` | `deployment/pi/` |

## What did **not** move (import stability)

- `edge/*.py` — unchanged paths (no `edge/runtime/` split) to avoid mass import churn.
- `config/` — remains at repo root (see `shared/README.md`).
- `run.py` — primary edge entry point.
- `cloud/*.py` — server layout unchanged aside from `gallery.py` bugfix (indentation).
- Per-run outputs still under `experiments/exp_<timestamp>/` via `config/experiment_session.py`.

## Dependency files

- **Edge:** canonical `edge/requirements-edge.txt`; `requirements_pi.txt` → `-r edge/requirements-edge.txt`.
- **Cloud:** canonical `cloud/requirements.txt`; root `requirements_cloud.txt` → `-r cloud/requirements.txt`.

## Path reference updates

- OpenCV Pi doc and errors now cite `deployment/pi/OPENCV_GUI_RASPBERRY_PI.md`.
- `run.py` references `deployment/pi/attendance.service`.

## For integrators

- Old commands (`python analyze_orientation.py`, `python preprocess_dataset.py`, `-m experiments.run_orientation_experiment`) **keep working** from repo root.
- New style: `python -m research.analysis.orientation` etc.

See `docs/DEPLOYMENT.md` for what to copy to the Pi vs the server.
