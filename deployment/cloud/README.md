# `deployment/cloud/` — selective cloud deployment

| File | Role |
|------|------|
| `CLOUD_BUNDLE.txt` | rsync `--files-from` manifest. Source of truth for what ships to the ArcFace server. |
| `deploy_cloud.sh` | Convenience wrapper. Defaults to `--dry-run`; pass `--apply` to copy. |

The cloud server runs from inside the `cloud/` directory (so `gallery/`
resolves next to `main.py`). Server-side workflow after deployment:

```bash
cd ~/arcface_server/cloud
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python enroll_gallery.py --images_dir enrollment_images/ --gallery_dir gallery/
uvicorn main:app --host 0.0.0.0 --port 8000
```

`deploy_cloud.sh` excludes:

- `__pycache__/`, `*.pyc`
- `cloud/.venv/` — server builds its own venv with `onnxruntime-gpu`
- `cloud/gallery/` — server enrolls its own gallery
- `cloud/enrollment_images/` — never ship enrollment images via the
  generic deploy script; transfer them out of band

See `cloud/README.md` for the full server-side runbook and the
embedding-space invariant.
