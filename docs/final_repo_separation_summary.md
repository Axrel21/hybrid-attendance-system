# Final repository separation summary

Third (and intentionally final) pass on the `deployment-refactor` branch.
Promotes `shared/` to a real cross-process contracts package, adds
`deployment/common/` helpers, ships `shared/` in both bundles, and
documents the conceptual layout in `docs/REPOSITORY_LAYOUT.md`.

Pair with the prior summaries:

- Pass 1 (initial layout move): `docs/MIGRATION.md` body.
- Pass 2 (operational stabilization): `docs/refactor_change_summary.md`.
- This pass: this file.

## 1. Files moved

None. The "operational stabilization" policy (no `edge/runtime/` split,
no `edge_runtime/` / `cloud_backend/` renames, no `enrollment/` relocate)
is preserved.

## 2. Files added

| Path | Purpose |
|------|---------|
| `shared/__init__.py` | Re-exports the stable contract names. |
| `shared/contracts.py` | HTTP endpoints (`VERIFY_IMAGE_PATH`, `HEALTH_PATH`, …), multipart and metadata field names, `VERIFICATION_RESPONSE_FIELDS`, embedding-dim invariants (`ARCFACE_EMBEDDING_DIM = 512`, `MOBILEFACENET_EMBEDDING_DIMS = (128, 192)`), defaults (`DEFAULT_JPEG_QUALITY = 85`, `DEFAULT_TIMEOUT_S = 2.0`, `DEFAULT_CLOUD_PORT = 8000`), `CONTRACT_VERSION = "1.0"`, dim guards. |
| `shared/schemas.py` | Lazy `get_diag_columns()` / `get_telemetry_csv_columns()` accessors plus verbatim `ATTENDANCE_CSV_COLUMNS` and the JSONL `EXPERIMENT_INDEX_FIELDS` tuple. |
| `deployment/common/README.md` | Describes the three cross-cutting deploy helpers. |
| `deployment/common/verify_manifests.sh` | Dry-runs both bundles and fails fast on a malformed manifest or a leak (e.g. `cloud/` in the Pi bundle). CI-safe. |
| `deployment/common/package_pi.sh` | Builds `dist/attendance_pi_<utc>.tar.gz` from `PI_BUNDLE.txt`. Tolerant of missing gitignored runtime artefacts in a dev clone (rsync `--ignore-missing-args`) but WARNs explicitly when models / `known_faces.json` are absent. |
| `deployment/common/package_cloud.sh` | Same shape, for the ArcFace server. Excludes `cloud/.venv/`, `cloud/gallery/`, `cloud/enrollment_images/`. |
| `docs/REPOSITORY_LAYOUT.md` | Single page mapping conceptual subsystems (verification / telemetry / dashboard / analytics / experiments / reports / edge runtime / shared / deployment) to current homes, with a topology diagram and "what stays stable" guarantees. |
| `docs/final_repo_separation_summary.md` | This file. |

## 3. Files modified

| Path | Change |
|------|--------|
| `shared/README.md` | Replaced the README-only placeholder with a description of `shared.contracts` and `shared.schemas`, including the deployment-side caveat that `get_diag_columns()` is lazy and will `ImportError` on hosts without the edge runtime stack. |
| `deployment/pi/PI_BUNDLE.txt` | Adds the `shared/` directory between `config/` and `deployment/pi/`. |
| `deployment/cloud/CLOUD_BUNDLE.txt` | Adds `shared/` between `cloud/.gitignore` and `requirements_cloud.txt`. |
| `README.md` | Quick-link rows for `shared/README.md`, `deployment/common/README.md`, and `docs/REPOSITORY_LAYOUT.md`. |
| `docs/MIGRATION.md` | Appended a "Third-pass operational separation" section enumerating every addition / modification / intentional non-change. |
| `docs/VALIDATION.md` | Appended §12 covering `shared/` import, lazy schema access, `verify_manifests.sh`, and tarball builds. |

## 4. Imports updated

None across module boundaries in production code. `shared.__init__`
re-exports from `shared.contracts` and `shared.schemas` only.

No edge or cloud module was changed to consume `shared.contracts` in
this pass — the runtime continues to hard-code the wire strings exactly
as it did before. Adopting the constants is a future, coordinated update
(noted under "Deferred cleanup").

## 5. Compatibility layers added

- `shared/__init__.py` is a real Python package. Existing scripts that
  do not reference it are unaffected; new tooling can pull contracts
  from one canonical place.
- `shared.schemas.get_diag_columns()` / `get_telemetry_csv_columns()`
  defer the `edge.main` / `edge.telemetry` imports, so importing
  `shared` does not pull in cv2 / TFLite. The edge-runtime stack
  remains optional for shared consumers.

## 6. Deployment workflow changes

- New, optional helper to sanity-check the bundles in CI:

```bash
bash deployment/common/verify_manifests.sh
```

  Verifies both dry-runs emit a plan, asserts critical paths are
  included (`run.py`, `edge/`, `config/`, `deployment/pi/`, `shared/`
  for Pi; `cloud/`, `shared/`, `requirements_cloud.txt` for cloud),
  and asserts forbidden paths are absent.

- New, optional helper to build transferable tarballs for offline
  deploys:

```bash
bash deployment/common/package_pi.sh        # → dist/attendance_pi_<utc>.tar.gz
bash deployment/common/package_cloud.sh     # → dist/arcface_server_<utc>.tar.gz
```

  Tarballs nest a single top-level directory (`attendance/` and
  `arcface_server/` respectively) mirroring the layout expected on the
  target host.

- The existing `bash deployment/pi/deploy_pi.sh` and
  `bash deployment/cloud/deploy_cloud.sh` flows are unchanged. They
  pick up `shared/` automatically via the updated manifests.

## 7. Git workflow recommendations

- Treat each pass on this branch as a logical commit:
  - Pass 1: layout move + shims.
  - Pass 2: operational stabilization (`requirments.txt` forwarder,
    `experiments/index.jsonl`, deployment manifests).
  - Pass 3 (this pass): `shared/`, `deployment/common/`, documentation.
- Squash within a pass; preserve the boundary between passes so the
  per-pass rationale in `docs/MIGRATION.md` lines up with `git log`.
- `dist/` (tarball output) is the only new ignorable byproduct. The
  repo's existing `.gitignore` already excludes `*.tar.gz`; add an
  explicit `/dist/` line in a future pass if multi-format outputs are
  introduced.
- Do not commit anything generated under `experiments/exp_*/` or
  `experiments/index.jsonl` — the existing rules in `.gitignore`
  already cover the CSVs (`*.csv`) but the JSONL file is not currently
  excluded. If a session runs locally before review, drop
  `experiments/index.jsonl` from the staged change explicitly.

## 8. Validation performed

| Check | Result |
|-------|--------|
| `python3 -m compileall -q shared config edge cloud research experiments run.py preprocess_dataset.py analyze_*.py test_env.py` | exit 0 |
| `python3 -c "import shared; from shared import VERIFY_IMAGE_PATH, METADATA_FIELDS, …"` plus constant assertions | OK; all 18 re-exports resolve, contract version `1.0`, ArcFace dim 512, MobileFaceNet dims `(128, 192)` |
| Lazy edge import: `from shared.schemas import get_diag_columns; get_diag_columns()` on a host without cv2 | Defers as expected; raises `ImportError("No module named 'cv2'")` only on call, not on import |
| `bash deployment/common/verify_manifests.sh` | Both bundles verified, including the new `shared/` presence and the no-leak assertions |
| `bash deployment/common/package_pi.sh /tmp/dist` on a dev clone | Builds 68 KB tarball; WARNs about missing gitignored `data/known_faces.json` and `models/*`; tarball contains `attendance/{run.py, edge/, config/, shared/, deployment/pi/, requirements_pi.txt}`; no `cloud/`, `research/`, `dataset_*`, `experiments/exp_*/` paths |
| `bash deployment/common/package_cloud.sh /tmp/dist` | Builds 21 KB tarball with `arcface_server/{cloud/, shared/, requirements_cloud.txt}`; no `edge/`, `config/`, `run.py`, `research/` |
| Updated `deploy_pi.sh` dry-run | Lists `config/`, `edge/`, `shared/`, `deployment/pi/`, `run.py`, `requirements_pi.txt`; excludes `__pycache__/` and `*.pyc` |
| Updated `deploy_cloud.sh` dry-run | Lists `cloud/{main, gallery, arcface_verifier, enroll_gallery, requirements, README, .gitignore}.py`, `shared/`, `requirements_cloud.txt`; excludes `__pycache__/`, `*.pyc`, `cloud/.venv/`, `cloud/gallery/`, `cloud/enrollment_images/` |

Runtime behavior was not exercised end-to-end (no cv2 / InsightFace /
camera on this dev host) — that is by design. The pass changes no
runtime code paths; static, import, manifest, and tarball checks are
sufficient to verify it.

## 9. Unresolved risks / issues

- **Wire-format constants are not yet consumed by the runtime.**
  `cloud/main.py` and `edge/cloud_client.py` still hard-code
  `"/verify/image"`, the multipart field names, and the metadata
  schema. A future coordinated pass should import these from
  `shared.contracts` so the contract version becomes load-bearing.
  Until then, `shared/contracts.py` is documentation that happens to be
  Python.
- **`shared.schemas.get_diag_columns()` raises `ImportError` cloud-side**
  unless the edge stack is also installed. Cloud aggregation code that
  needs the CSV column list must either (a) install the edge runtime
  requirements, (b) read the CSV header at runtime, or (c) call the
  function inside a `try` block. Documented in `shared/README.md` and
  `shared/schemas.py`.
- **`experiments/index.jsonl` is not gitignored.** If a session runs
  during a review cycle, the JSONL grows. Either add `/experiments/`
  to `.gitignore` in a future pass or drop the file from each commit
  manually.

## 10. Intentionally deferred cleanup

| Item | Why deferred |
|------|--------------|
| Rename `edge/` → `edge_runtime/` and `cloud/` → `cloud_backend/verification/` | Would break every `from edge.X import Y` and the `cd cloud && uvicorn main:app` workflow. Scope of change far exceeds operational benefit. |
| Create empty `cloud_backend/{telemetry,dashboard,analytics,experiments,reports}/` placeholder packages | Empty packages add confusion without delivering function. Their conceptual mapping is documented in `docs/REPOSITORY_LAYOUT.md` and they should be created with their first real service. |
| Migrate `cloud/main.py` and `edge/cloud_client.py` to use `shared.contracts` constants | Requires a coordinated edge + cloud release; this pass keeps `shared/contracts.py` as the single source of truth doc-side without forcing a cross-component rewrite. |
| Move `enrollment/` under `research/enrollment/` | External automation may type `python -m enrollment.enroll`. Wait for an audit cycle. |
| Drop `requirments.txt` (typo) entirely | Keep one more cycle to absorb external muscle memory. |
| Retire `data/experiment_sessions.jsonl` in favour of `experiments/index.jsonl` | `research/experiments/orientation_launcher.py` still writes it. Update both writer and any external readers in one pass. |
| Promote `experiments/index.jsonl` to a queryable cloud-side aggregation service | Out of scope; the JSONL is the hook that makes such a service trivial when it lands. |
| Add `/dist/` to `.gitignore` and `experiments/index.jsonl` to a tracked-exclude list | Trivial follow-up; skipped to keep this pass diff-only. |
