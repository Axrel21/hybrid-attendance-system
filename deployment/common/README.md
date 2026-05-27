# `deployment/common/` — cross-cutting deployment helpers

Scripts that operate on **both** the Pi and cloud bundles, or that
produce deployable artifacts independently of any target host.

`deployment/common/` is **not** a runtime node. Nothing here imports
runtime code, talks to cameras, runs inference, or starts servers.

| Script | Purpose |
|--------|---------|
| [`verify_manifests.sh`](verify_manifests.sh) | Dry-runs both `deploy_pi.sh` and `deploy_cloud.sh` against a temporary directory and fails fast if either manifest is broken. Safe to run in CI. |
| [`package_pi.sh`](package_pi.sh) | Builds a dated `attendance_pi_<ts>.tar.gz` from `PI_BUNDLE.txt`. Useful for offline transfer to a Pi without rsync access. |
| [`package_cloud.sh`](package_cloud.sh) | Same shape as `package_pi.sh` but for the ArcFace server bundle. |

Output of the packaging scripts lands under `./dist/` by default (a
positional argument overrides the directory). `dist/` is gitignored
indirectly via the global `*.tar.gz` exclusion patterns; treat the
tarballs as ephemeral build artefacts.

## Workflow

```bash
# CI / pre-release sanity check
bash deployment/common/verify_manifests.sh

# Build transferable bundles
bash deployment/common/package_pi.sh
bash deployment/common/package_cloud.sh

# Inspect what would actually ship
tar -tzf dist/attendance_pi_<ts>.tar.gz | head
tar -tzf dist/arcface_server_<ts>.tar.gz | head
```

All scripts are idempotent and side-effect-free at the destination
(staging happens in `mktemp -d` directories that are cleaned up on
exit). They do **not** modify the working tree.
