# Telemetry ownership

Single reference for what is emitted, where it lands, and who consumes it.
Schemas of record live in code (`edge/main.py`, `edge/telemetry.py`); this
doc reflects them but is not authoritative — the code is.

---

## 1. Per-run experiment session (edge-emitted)

`config.experiment_session.init_experiment_session(project_root)` creates:

```
experiments/exp_<YYYYMMDD_HHMMSS>/
├── telemetry/
│   └── telemetry_log.csv         # frame-level perf
├── diagnostics/
│   ├── diagnostic_log.csv        # per-(frame, track) decision
│   └── attendance_log.csv        # matched events only
├── debug_frames/                 # optional JPEGs (DEBUG_FRAMES=1)
│   ├── manual/
│   ├── spoof_cases/
│   ├── liveness_failures/
│   ├── borderline_recognition/
│   ├── low_detection_conf/
│   ├── sampled/
│   └── misc/
├── plots/                        # AUTO_EXPERIMENT_REPORT PNGs
├── summaries/                    # AUTO_EXPERIMENT_REPORT JSON/MD
├── logs/
│   ├── runtime.log
│   └── debug.log
└── config/
    └── settings_snapshot.json
```

The active session root is exported as `os.environ["EXPERIMENT_ROOT"]`
and `EXPERIMENT_ID`. `experiments/index.jsonl` (added second-pass) gains
one append per session for dashboard enumeration.

---

## 2. Schemas

### `diagnostic_log.csv`

Source of truth: `edge.main.DIAG_COLUMNS`. Schema auto-rotation:
`edge.main._rotate_diag_if_schema_changed` archives mismatched headers to
`diagnostic_log.archived_<ts>.csv` so historical runs never silently mix
with newer columns. Column groups:

| Group | Columns |
|-------|---------|
| Legacy block (do not reorder) | `timestamp, frame_w, frame_h, track_id, lbl, live_conf, reason, decision, mode, distance, brightness, avg_mag, avg_ang_var, avg_mag_var, avg_area_var, rigid_ratio, m_score, g_score, identity, sim, th_high, th_mid, latency_ms` |
| Orientation calibration | `face_w, face_h, mode_raw, orient_ratio, eye_dist_px, vertical_dist_px, orientation_active, avg_blur` |
| Recognition pool tracing | `pool_used, pool_size, num_identities` |
| Session tag | `experiment_label` |
| Performance instrumentation | `t_detect_ms, t_liveness_ms, t_embed_ms, t_match_ms, fps_rolling, cpu_pct, mem_mb, cpu_temp_c` |
| Track 2 hybrid cloud | `cloud_outcome, cloud_identity, cloud_arcface_confidence, cloud_verified, cloud_rtt_ms, cloud_server_total_ms, jpeg_encode_ms, image_size_bytes, edge_cloud_agree` |

### `telemetry_log.csv`

Source of truth: `edge.telemetry.TELEMETRY_CSV_COLUMNS`. Rotation:
`edge.telemetry.rotate_if_schema_changed`. Schema:

```
timestamp, frame_idx, experiment_label, fps_rolling,
dt_ms, dt_std_ms,
t_capture_ms, t_detect_ms, t_tracks_ms,
t_liveness_max_ms, t_embed_max_ms, t_match_max_ms,
t_overlay_ms, t_post_ms, t_total_ms,
cpu_pct, mem_mb, cpu_temp_c,
num_tracks, num_faces_valid, yunet_raw, yunet_kept,
max_live_conf, max_sim
```

### `attendance_log.csv`

Written by `edge.main.FinalHybridEdge.run` for `MATCHED` events:

```
name, confidence, timestamp, latency,
liveness_label, reason, distance, brightness,
motion_score, geometry_score, mode, track_id
```

### Cloud `/verify/image` response

Source of truth: `cloud.main.VerificationResponse` (Pydantic model).
Edge-side mapping: `edge.cloud_client.CloudVerificationResult`. Fields:

```
verified, identity, arcface_confidence,
edge_candidate, edge_cloud_agree,
image_decode_ms, arcface_extract_ms, gallery_search_ms, server_total_ms,
route, request_id, timestamp_server_ms, gallery_size
```

---

## 3. Cross-session / repo-level

| Path | Writer | Reader | Purpose |
|------|--------|--------|---------|
| `experiments/index.jsonl` | `config.experiment_session.init_experiment_session` (second-pass) | Dashboards, future tooling | One JSON line per session: `{experiment_id, started_at, root, label, telemetry_csv, diagnostic_csv, attendance_csv}`. Best-effort; pipeline never blocks on it. |
| `experiments/exp_<id>/config/experiment_protocol.json` | `research.experiment_protocol` CLI (fifth-pass) | `edge.telemetry_uploader.build_session_start`; cloud `/api/sessions/{id}/protocol` | Optional sidecar; structured reproducibility metadata (attack type, distance, lighting, orientation, mounting, movement, dataset label, operator, target identities, ...). See [`EXPERIMENT_PROTOCOL.md`](EXPERIMENT_PROTOCOL.md). |
| `experiments/exp_<id>/summaries/stabilization.json` | `research.analysis.stabilization` CLI (fifth-pass) | Manual / notebooks / future dashboards | Eight-dimension stabilization summary. See [`STABILIZATION_DIAGNOSTICS.md`](STABILIZATION_DIAGNOSTICS.md). |
| `experiments/exp_<id>/summaries/threshold_sweep.json` | `research.analysis.threshold_sweep` CLI (fifth-pass) | Manual / notebooks | Threshold what-if + hysteresis flip-flops. |
| `experiments/exp_<id>/summaries/runtime_diagnostics.json` | `research.analysis.runtime_diagnostics` CLI (sixth-pass) | Manual / dashboards | Gap-filling YuNet / recognition / PAD / orientation metrics. See [`RUNTIME_DIAGNOSTICS.md`](RUNTIME_DIAGNOSTICS.md). |
| `experiments/exp_<id>/summaries/quality_tags.json` | `research.analysis.quality_gates` CLI (sixth-pass) | Manual / dashboards | Soft quality tags + thresholds. See [`QUALITY_GATES.md`](QUALITY_GATES.md). |
| `experiments/exp_<id>/summaries/stabilization_report.json` | `research.analysis.stabilization_report` CLI (sixth-pass) | Manual / dashboards | One-shot bundle: protocol + stabilization + runtime + sweep + tags. |
| `experiments/exp_<id>/summaries/stabilization_report.md` | Same CLI (`--no-md` to skip) | SSH-friendly triage view | Headline metrics + tag table + sweep snapshot. |
| `data/experiment_sessions.jsonl` | `research/experiments/orientation_launcher.py` only | Manual / analysis scripts | Tagged orientation calibration sessions. Legacy; preserved for backward compatibility. |
| `data/known_faces.json` | `enrollment/enroll.py` | `edge.main.FinalHybridEdge.__init__` | MobileFaceNet enrollment DB. Runtime artifact, gitignored. |
| `data/plots/<topic>/` | Legacy `analyze_*` defaults | Manual | Older static plot outputs. Modern flow puts plots under `experiments/<id>/plots/`. |

---

## 4. Consumers

- `edge.experiment_report.generate_experiment_report` — post-run PNG/JSON/MD from session CSVs (triggered by `AUTO_EXPERIMENT_REPORT=1`).
- `research.analysis.orientation` — orientation threshold analysis. Reads diagnostic CSV; defaults to `data/diagnostic_log.csv` but accepts `--diag` to point at any session.
- `research.analysis.pi_perf` — FPS / latency / thermal plots.
- `research.analysis.liveness_diag_print` — PAD/liveness CLI summary.
- `research.analysis.attendance_latency_offload` — latency + offload rate summary.

---

## 5. Cloud-side telemetry

`cloud/main.py` logs per-request lines via `logging` (stdlib, INFO level)
including per-stage `image_decode_ms`, `arcface_extract_ms`,
`gallery_search_ms`, `server_total_ms`, `edge_cloud_agree`. There is no
cloud-side CSV — round-trip telemetry is correlated edge-side via
`cloud_*` columns in `diagnostic_log.csv`.

---

## 6. Stability guarantees

- DIAG / telemetry column order is append-only. Reordering breaks the
  `_rotate_*` rotation and is treated as a schema break.
- `cloud.main.VerificationResponse` is the wire contract; adding optional
  fields is safe, removing or renaming is not.
- `experiments/index.jsonl` is treated as best-effort. Downstream tooling
  must not assume every session appears (legacy sessions predate it).
