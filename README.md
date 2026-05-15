# Hybrid Edge–Cloud Facial Recognition (research platform)

Telemetry-driven embedded AI pipeline: **YuNet + MobileFaceNet** on the edge, optional **ArcFace** verification in the cloud via **JPEG face crops** (never cross-model embedding comparison).

## Quick links

| Topic | Doc |
|-------|-----|
| Edge runtime | [edge/README.md](edge/README.md) |
| Cloud API / gallery | [cloud/README.md](cloud/README.md) |
| Shared `config/` | [shared/README.md](shared/README.md) |
| Pi vs server deploy | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| Selective Pi deploy | [deployment/pi/PI_BUNDLE.txt](deployment/pi/PI_BUNDLE.txt) + [deploy_pi.sh](deployment/pi/deploy_pi.sh) |
| Selective cloud deploy | [deployment/cloud/CLOUD_BUNDLE.txt](deployment/cloud/CLOUD_BUNDLE.txt) + [deploy_cloud.sh](deployment/cloud/deploy_cloud.sh) |
| Telemetry ownership | [docs/TELEMETRY.md](docs/TELEMETRY.md) |
| Stabilization analysis | [docs/STABILIZATION_ANALYSIS.md](docs/STABILIZATION_ANALYSIS.md) |
| Repository layout (current vs target) | [docs/REPOSITORY_LAYOUT.md](docs/REPOSITORY_LAYOUT.md) |
| Cross-process contracts | [shared/README.md](shared/README.md) |
| Cross-cutting deploy helpers | [deployment/common/README.md](deployment/common/README.md) |
| Layout migration | [docs/MIGRATION.md](docs/MIGRATION.md) |
| Post-refactor checks | [docs/VALIDATION.md](docs/VALIDATION.md) |
| `data/` artifacts | [data/README.md](data/README.md) |
| Research scripts | [research/README.md](research/README.md) |

## Run

```bash
pip install -r edge/requirements-edge.txt   # Pi / edge
python run.py
```

Cloud (separate host): see `cloud/README.md`.
