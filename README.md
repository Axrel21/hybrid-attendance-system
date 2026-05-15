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
| Stabilization diagnostics | [docs/STABILIZATION_DIAGNOSTICS.md](docs/STABILIZATION_DIAGNOSTICS.md) |
| Runtime diagnostics | [docs/RUNTIME_DIAGNOSTICS.md](docs/RUNTIME_DIAGNOSTICS.md) |
| Soft quality gates | [docs/QUALITY_GATES.md](docs/QUALITY_GATES.md) |
| Experiment protocol metadata | [docs/EXPERIMENT_PROTOCOL.md](docs/EXPERIMENT_PROTOCOL.md) |
| Repository layout (current vs target) | [docs/REPOSITORY_LAYOUT.md](docs/REPOSITORY_LAYOUT.md) |
| Cross-process contracts | [shared/README.md](shared/README.md) |
| Composite cloud backend | [cloud_backend/README.md](cloud_backend/README.md) |
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

Cloud (separate host): see `cloud/README.md` for the verification-only
server, or `cloud_backend/README.md` for the composite backend
(verification + telemetry + dashboard + WebSocket).

```bash
# Composite backend with all routers (requires cloud/requirements.txt):
bash deployment/cloud/run_backend.sh --host 0.0.0.0 --port 8000
```

Post-session telemetry upload (separate process; Pi keeps running offline):

```bash
python -m edge.telemetry_uploader \
    --session experiments/exp_<id>/ \
    --cloud http://cloud-host:8000
```
