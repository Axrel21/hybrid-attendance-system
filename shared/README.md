# Shared infrastructure (edge + research)

This repo keeps cross-cutting configuration at the **repository root** under `config/` (not under a `shared/` Python package) so existing imports stay stable:

| Path | Purpose |
|------|---------|
| `config/settings.py` | Runtime tunables, telemetry flags, orientation thresholds, camera modes |
| `config/experiment_session.py` | Per-run `experiments/exp_<timestamp>/` layout, `EXPERIMENT_ROOT` env |
| `config/logging_setup.py` | `runtime.log` / `debug.log` / console filters |

**Why here:** `edge/main.py`, `run.py`, and research tooling all use `from config import …`. Moving these modules would require touching every consumer and risks breaking Pi Conda workflows.

**Cloud:** The FastAPI app does **not** import `config/`; it uses local defaults and environment. Edge metadata in offload requests uses `EXPERIMENT_LABEL` / frame ids from the edge process only.

For deployment boundaries, see `docs/DEPLOYMENT.md` and `edge/README.md`.
