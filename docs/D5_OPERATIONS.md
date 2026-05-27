# D5 Track 5 — Operations & production hardening

Production hardening only: configuration, health, observability, graceful recovery, and demo runbooks. No changes to recognition, eligibility, decisions, or surveillance ML.

## Environment profiles

| Profile | File | Typical use |
|---------|------|-------------|
| `development` | `deployment/env/development.env` | Local dev, verbose logs |
| `demo` | `deployment/env/demo.env` | Classroom demo |
| `production` | `deployment/env/production.env` | Deployed server |

Set profile before start:

```bash
export HYBRID_PROFILE=demo   # or development | production
source deployment/common/load_profile.sh
```

Optional host overrides (not committed): `deployment/env/local.env`.

Python loads the same files at import via `cloud_backend.system.settings.load_settings()`. Application code reads tunables through `get_settings()` — avoid new scattered `os.environ.get` calls.

## Virtual environments

| Component | Venv | Requirements |
|-----------|------|--------------|
| Cloud backend | `cloud/.venv` | `cloud/requirements.txt` |
| Surveillance (laptop) | repo root or `surveillance/.venv` | `surveillance/requirements-surveillance.txt` |

```bash
cd cloud && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Run order (demo)

1. **Database** — PostgreSQL reachable; migrations applied per your existing setup.
2. **Cloud backend** — from repo root:

   ```bash
   bash deployment/cloud/run_backend.sh
   ```

3. **Verify health** — `curl -s http://localhost:8000/health | jq`
4. **Surveillance** (optional presence) — separate terminal, repo root:

   ```bash
   source deployment/common/load_profile.sh
   python -m surveillance.run
   ```

5. **Dashboard** — open `http://localhost:8000/dashboard/attendance` (polls attendance + health + config).

6. **Edge recognition** — your existing edge → `POST /attendance/recognition/events`.

## Health & config APIs

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Composite status, profile, attendance + surveillance summary |
| `GET /health/attendance` | DB connectivity, in-memory pipeline counts |
| `GET /health/surveillance` | External runtime hints, presence session count |
| `GET /system/config` | Safe tunables (no secrets) |

Example:

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/health/attendance
curl -s http://localhost:8000/system/config
```

`status: degraded` when the database is unreachable; process stays up.

## Observability

Structured single-line logs (`event=... key=value`) for:

- `recognition_ingested`
- `presence_ingested`
- `evidence_generated` / `eligibility_generated`
- `decision_generated`
- `finalization_frozen` / `finalization_generated`
- `report_generated`

No external telemetry in this track.

## Failure handling

| Condition | Behavior |
|-----------|----------|
| Empty presence | Health shows `presence_sessions: 0`; pipelines stay `ok` |
| DB unavailable | `/health` → `degraded`; `SQLAlchemyError` → HTTP 503 on DB routes; report/evidence return empty lists where applicable |
| Empty report | `GET /attendance/report` → `{"total":0,"lectures":[]}` |

Restart the backend: in-memory presence/evidence/finalization caches reset; DB-backed recognition logs persist.

## Rollback

Track 5 is isolated under `cloud_backend/system/`, `deployment/env/`, and docs.

```bash
git checkout HEAD -- cloud_backend/system deployment/env deployment/common docs/D5_OPERATIONS.md
git checkout HEAD -- cloud_backend/server.py deployment/cloud/run_backend.sh
# Revert any attendance/*.py logging/settings wiring if needed
```

Remove router mount in `server.py` if rolling back only system routes.

## Known limitations

- Surveillance runs as a separate process; `/health/surveillance` reports configuration, not live camera status.
- Evidence/eligibility/decision/state/finalization caches are in-memory and lost on restart.
- `GET /system/config` never exposes DB passwords or API keys.
- Demo dashboard does not add new UI pages; it polls existing APIs plus health/config.

## Validation checklist

```bash
# 1. Compile
python -m compileall cloud_backend/system cloud_backend/attendance -q

# 2. Start backend
HYBRID_PROFILE=demo bash deployment/cloud/run_backend.sh

# 3. Health + config (another terminal)
curl -s http://localhost:8000/health
curl -s http://localhost:8000/health/attendance
curl -s http://localhost:8000/system/config

# 4. Restart recovery — stop/start uvicorn; /health should return 200

# 5. Report empty OK
curl -s http://localhost:8000/attendance/report
```
