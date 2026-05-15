# Hybrid Edge–Cloud Facial Recognition (research platform)

Telemetry-driven embedded AI pipeline: **YuNet + MobileFaceNet** on the edge, optional **ArcFace** verification in the cloud via **JPEG face crops** (never cross-model embedding comparison).

## Quick links

| Topic | Doc |
|-------|-----|
| Edge runtime | [edge/README.md](edge/README.md) |
| Cloud API / gallery | [cloud/README.md](cloud/README.md) |
| Shared `config/` | [shared/README.md](shared/README.md) |
| Pi vs server deploy | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| Layout migration | [docs/MIGRATION.md](docs/MIGRATION.md) |
| Post-refactor checks | [docs/VALIDATION.md](docs/VALIDATION.md) |
| Research scripts | [research/README.md](research/README.md) |

## Run

```bash
pip install -r edge/requirements-edge.txt   # Pi / edge
python run.py
```

Cloud (separate host): see `cloud/README.md`.
