# `shared/` — cross-cutting contracts

`shared/` is **not** a runtime node. It is the dependency-light home for
constants and lazy accessors that both the edge runtime and the cloud
backend (and any future dashboard / aggregation tooling) must agree on.

## What lives here

| Module | Contents |
|--------|----------|
| `shared.contracts` | HTTP endpoint paths (`/verify/image`, `/health`, ...), multipart and metadata field names, `VerificationResponse` field tuple, embedding-dim invariants (`ARCFACE_EMBEDDING_DIM = 512`, etc.), defaults (`DEFAULT_JPEG_QUALITY`, `DEFAULT_TIMEOUT_S`, `DEFAULT_CLOUD_PORT`), and the contract version tag. |
| `shared.schemas` | Lazy accessors `get_diag_columns()` / `get_telemetry_csv_columns()` that fetch from `edge.main` / `edge.telemetry` on demand, plus a verbatim copy of `ATTENDANCE_CSV_COLUMNS` and the JSONL field tuple `EXPERIMENT_INDEX_FIELDS`. |

`shared/__init__.py` re-exports the stable names so downstream code can
`from shared import VERIFY_IMAGE_PATH, METADATA_FIELDS, ...` without
descending into the submodules.

## What does NOT live here

- ArcFace, YuNet, MobileFaceNet, TFLite, or any other inference code.
- FastAPI handlers or routers.
- Dashboards, plotting, telemetry aggregation services.
- Anything that imports `cv2`, `tflite_runtime`, `tensorflow`,
  `insightface`, `pandas`, or `matplotlib` at module top level.

If a change would force this rule to be broken, the new code belongs in
`edge/`, `cloud/`, or a future `cloud_backend/*` subpackage — not here.

## How `config/` relates to `shared/`

`config/settings.py` still lives at the repository root, *not* under
`shared/`. The rationale is the same as in
[`docs/MIGRATION.md`](../docs/MIGRATION.md): `edge.main`, `run.py`, and
research tooling already do `from config import settings`, and moving
those modules would force a coordinated rename across every consumer.
`shared/` covers the **cross-process** contracts (HTTP, CSV schemas,
embedding-dim invariants); `config/` covers the **in-process** runtime
tunables for the edge.

## Deployment

`shared/` ships in both bundles:

- `deployment/pi/PI_BUNDLE.txt` — listed alongside `edge/`, `config/`.
- `deployment/cloud/CLOUD_BUNDLE.txt` — listed alongside `cloud/`.

Because `shared.schemas` imports `edge.main` *lazily*, calling
`get_diag_columns()` on a cloud host without the edge stack installed
will raise `ImportError` at the moment of the call. Cloud-side code that
needs the CSV schema must either install the edge runtime requirements
or rely on the JSONL `EXPERIMENT_INDEX_FIELDS` constant that is defined
verbatim in `shared.schemas`.
