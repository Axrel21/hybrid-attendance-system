# Deployment layout

| Path | Role |
|------|------|
| `deployment/pi/` | Raspberry Pi: systemd unit, setup script, OpenCV GUI notes, optional HighGUI validator |
| `cloud/` (repo root) | FastAPI ArcFace server — **do not** deploy to the Pi for production edge-only bundles |

**Pi**

- Unit file: `deployment/pi/attendance.service` — adjust `WorkingDirectory`, `ExecStart`, and `User` for your install path (e.g. `/home/pi/attendance`).
- Smoke tests: `deployment/pi/pi_setup.sh` (run with Conda/venv already activated).
- GUI / OpenCV wheel notes: `deployment/pi/OPENCV_GUI_RASPBERRY_PI.md`.

**Server**

- Install from repo: `pip install -r cloud/requirements.txt` (or root `requirements_cloud.txt`).
- Run from the `cloud/` directory so `gallery/` resolves relative to the process cwd (see `cloud/README.md`).

See `docs/DEPLOYMENT.md` for selective copy/rsync guidance.
