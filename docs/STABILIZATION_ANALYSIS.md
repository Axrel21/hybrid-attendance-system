# Second-pass stabilization analysis

Status snapshot of `deployment-refactor` immediately before the second-pass
stabilization changes. Use this doc as the operational reference; pair it
with [TELEMETRY.md](TELEMETRY.md) for emitter/consumer details and
[refactor_change_summary.md](refactor_change_summary.md) for the actual
diff applied in this pass.

---

## 1. Runtime ownership map

| Owner | Paths | Imports across boundary? |
|-------|-------|--------------------------|
| **Edge runtime (Pi or dev PC)** | `run.py`, `edge/*.py`, `config/*.py` | `edge.* → config.*`, `edge.* → edge.*` only |
| **Cloud runtime (server host)** | `cloud/main.py`, `cloud/arcface_verifier.py`, `cloud/gallery.py`, `cloud/enroll_gallery.py` | None outside `cloud/` (bare imports require `cwd=cloud/`) |
| **Shared infra (edge + research)** | `config/settings.py`, `config/experiment_session.py`, `config/logging_setup.py` | Consumed by `edge/*`, `research/*`, `experiments/run_orientation_experiment.py` |
| **Research / dev tooling** | `research/analysis/*`, `research/experiments/orientation_launcher.py`, `research/dataset_preprocess.py`, `research/tools/smoke_dev_env.py`, `enrollment/enroll.py` | `research/dataset_preprocess.py → edge.align`; `research/experiments/orientation_launcher.py → edge.main`, `config.settings`; `research/analysis/orientation.py → config.settings` |
| **Backward-compatible shims (repo root)** | `analyze_diag.py`, `analyze_orientation.py`, `analyze_pi_perf.py`, `analyze_results.py`, `preprocess_dataset.py`, `test_env.py`, `experiments/run_orientation_experiment.py` | All forward to `research/*` |
| **Pi deployment assets** | `deployment/pi/attendance.service`, `deployment/pi/pi_setup.sh`, `deployment/pi/OPENCV_GUI_RASPBERRY_PI.md`, `deployment/pi/validate_opencv_gui.py` | `validate_opencv_gui.py → edge.opencv_highgui` |
| **Per-run experiment outputs** | `experiments/exp_<timestamp>/...` | Written by `edge.main` and `edge.experiment_report` via `config.experiment_session` |

**Critical invariant (unchanged):** `edge/*` produces MobileFaceNet
embeddings. `cloud/*` produces ArcFace embeddings. The two never compare
directly — offload uses JPEG face crops via
`edge/cloud_client.py` ↔ `cloud/main.py /verify/image`.

---

## 2. Dependency boundaries

Five requirements-style files exist on `deployment-refactor`:

| File | Role | Status |
|------|------|--------|
| `edge/requirements-edge.txt` | Canonical Pi/edge pins (`tflite-runtime`, `picamera2`, ARM-safe `opencv-python-headless`) | Keep |
| `cloud/requirements.txt` | Canonical cloud pins (`fastapi`, `insightface`, `onnxruntime-gpu`) | Keep |
| `requirements_pi.txt` | Forwarder: `-r edge/requirements-edge.txt` | Keep |
| `requirements_cloud.txt` | Forwarder: `-r cloud/requirements.txt` | Keep |
| `requirments.txt` *(typo)* | Stale standalone: pins `tensorflow-cpu==2.13.0` (no ARM64 wheel) | **Footgun — second-pass: convert to forwarder** |

**Import boundaries (verified):**

- `cloud/*` is fully isolated. Imports only stdlib, third-party,
  `arcface_verifier`, `gallery` (bare).
- `edge/*` is fully isolated from `cloud/`, `research/`, `enrollment/`.
- `research/*` legitimately depends on `edge.align` (deterministic 5-point
  similarity transform, shared with runtime) and `edge.main` (orientation
  launcher reuses the real pipeline class).

---

## 3. Telemetry ownership

See [TELEMETRY.md](TELEMETRY.md) for the canonical reference. Summary:

- **Per-run artifacts** (edge): live under `experiments/exp_<timestamp>/`,
  with subdirs `telemetry/`, `diagnostics/`, `debug_frames/`, `plots/`,
  `summaries/`, `logs/`, `config/`. Created by
  `config.experiment_session.init_experiment_session`.
- **Cross-session log** (research): `data/experiment_sessions.jsonl`
  appended by `research/experiments/orientation_launcher.py` only.
- **Static analysis outputs** (research, legacy default): `data/plots/...`
  used by older `analyze_*` script defaults; modern post-run reports go
  into `experiments/<id>/plots/`.

Schemas of record: `edge.main.DIAG_COLUMNS`,
`edge.telemetry.TELEMETRY_CSV_COLUMNS`. CSV header auto-rotation lives in
`_rotate_diag_if_schema_changed` (`edge/main.py`) and
`rotate_if_schema_changed` (`edge/telemetry.py`).

---

## 4. Deployment risk inventory

Issues still present going into this pass:

1. **`requirments.txt` stale standalone** — `pip install -r requirments.txt`
   on a Pi pulls a non-ARM `tensorflow-cpu` wheel and fails; on x86 it
   silently bypasses the canonical edge pins.
2. **`data/` ownership ambiguity** — same directory holds the runtime
   artifact `data/known_faces.json`, the legacy cross-session log
   `data/experiment_sessions.jsonl`, and legacy plot output
   `data/plots/...`. Not obvious which entries are deploy-time, runtime,
   or research-only.
3. **`enrollment/` at repo root** — classified as research/dev tooling in
   `docs/DEPLOYMENT.md` but lives outside `research/`; a Pi rsync that
   pulls `./` would include it.
4. **No machine-readable deployment manifest** — only an rsync example in
   `docs/DEPLOYMENT.md`. Selective Pi vs cloud copy is manual.
5. **No experiment index** — each `experiments/exp_<timestamp>/` is
   isolated. A dashboard or notebook needs to `glob` the filesystem to
   enumerate runs.
6. **`shared/` directory** — README-only placeholder. Not a Python
   package; clear in the README but easy to misread as future code.

Non-issues (intentionally preserved):

- `edge/*` layout (no `edge/runtime/` split — would invalidate every
  existing import).
- `config/` at repo root (`edge.main`, `run.py`, research tooling all
  use `from config import …`).
- Cloud server bare imports (`from gallery import …`) — depend on
  `cwd=cloud/`, documented in `cloud/README.md` and `deployment/README.md`.

---

## 5. Stabilization plan (this pass)

Scope: additive boundary clarification + deployment automation. No file
moves, no edge/cloud runtime semantic changes, no schema changes.

| # | Change | Type | Risk |
|---|--------|------|------|
| 1 | Convert `requirments.txt` to `-r edge/requirements-edge.txt` forwarder with deprecation comment | Modify | None — corrects a footgun |
| 2 | Add `deployment/pi/PI_BUNDLE.txt` + `deploy_pi.sh` (dry-run default) | Add | None — opt-in script |
| 3 | Add `deployment/cloud/CLOUD_BUNDLE.txt` + `deploy_cloud.sh` (dry-run default) + small README | Add | None — opt-in script |
| 4 | Add `data/README.md` clarifying runtime vs research roles | Add | None |
| 5 | Add `enrollment/README.md` confirming dev-time-only status | Add | None |
| 6 | Add `docs/TELEMETRY.md` — canonical emitter/schema/destination reference | Add | None |
| 7 | Append additive `experiments/index.jsonl` line on session init (try/except wrapped) | Modify `config/experiment_session.py` | Very low — failure path swallowed |
| 8 | Append "Second-pass stabilization" section to `docs/MIGRATION.md` | Modify | None |
| 9 | Append validation steps for new artifacts to `docs/VALIDATION.md` | Modify | None |
| 10 | Update root `README.md` quick-links with deployment scripts | Modify | None |
| 11 | Write `docs/refactor_change_summary.md` | Add | None |

**Deliberately not done in this pass:**

- Moving `enrollment/` under `research/` — would break `python -m enrollment.enroll`. Deferred (docs make ownership clear).
- Splitting `edge/` into subpackages — would break every existing `from edge.X import Y`. Deferred per first-pass migration policy.
- Removing `shared/` — already a doc-only placeholder; deletion adds churn without benefit.
- Promoting `experiments/index.jsonl` to replace `data/experiment_sessions.jsonl` — would lose backward compatibility with the orientation launcher. Both coexist.

---

## 6. Runtime + telemetry preservation checklist

Run after applying changes. Detailed steps in
[VALIDATION.md](VALIDATION.md).

- [ ] `python -m compileall -q config edge cloud research experiments run.py preprocess_dataset.py`
- [ ] `python -c "import run; from edge.main import FinalHybridEdge, DIAG_COLUMNS; assert len(DIAG_COLUMNS) >= 50"`
- [ ] `cd cloud && python -c "from gallery import FaceGallery; from arcface_verifier import ArcFaceVerifier"`
- [ ] All five `analyze_*.py` / `preprocess_dataset.py` / `test_env.py` / `experiments/run_orientation_experiment.py` shims still import without error.
- [ ] `pip install --dry-run -r requirments.txt` resolves to the same packages as `-r edge/requirements-edge.txt`.
- [ ] `bash deployment/pi/deploy_pi.sh --help` and `bash deployment/cloud/deploy_cloud.sh --help` print usage; default invocation prints rsync `--dry-run` output.
- [ ] After a `run.py` smoke session, `experiments/index.jsonl` gains one line matching the `experiments/exp_*/` directory just created.
