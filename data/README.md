# `data/` — mixed runtime + research artifacts

This directory holds three semantically distinct things. The mix is
historical; the second-pass stabilization documents the split rather than
reorganising it (moving these paths would invalidate working analysis
scripts and the runtime enrollment lookup).

| Path | Role | Deploy to Pi? | Deploy to cloud? |
|------|------|---------------|------------------|
| `data/known_faces.json` | **Runtime artifact** — MobileFaceNet enrollment DB consumed by `edge.main.FinalHybridEdge.__init__`. Produced offline by `enrollment/enroll.py` on a dev machine. Gitignored. | **Yes** (required) | No |
| `data/experiment_sessions.jsonl` | **Research log** — one line per tagged session, appended by `research/experiments/orientation_launcher.py`. Legacy cross-session marker; preserved for backward compatibility. | No | No |
| `data/plots/<topic>/` | **Research outputs** — legacy default output directory for the older `analyze_*` scripts. Modern post-run plots live under `experiments/<exp_id>/plots/`. | No | No |

The dashboard-readable per-run index lives at `experiments/index.jsonl`
(see `docs/TELEMETRY.md` §3). New tooling should prefer that file over
`data/experiment_sessions.jsonl`.

`data/known_faces.json` is the MobileFaceNet enrollment store and is
**never** mixed with the cloud ArcFace gallery under `cloud/gallery/`
(see `cloud/README.md` for the embedding-space invariant).
