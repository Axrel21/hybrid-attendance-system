#!/usr/bin/env bash
# deployment/cloud/deploy_cloud.sh
# ============================================================
# Selective rsync of the cloud (ArcFace) bundle to a server host.
#
# Defaults to --dry-run. Pass --apply to actually copy.
#
# Usage:
#   bash deployment/cloud/deploy_cloud.sh user@server:~/arcface_server/
#   bash deployment/cloud/deploy_cloud.sh --apply user@server:~/arcface_server/
#   CLOUD_DEST=user@host:~/arcface_server/ bash deployment/cloud/deploy_cloud.sh --apply
#
# Reads the file list from deployment/cloud/CLOUD_BUNDLE.txt. Excludes the
# locally enrolled gallery (gallery/) — the server must build its own with
# enroll_gallery.py against authoritative enrollment images.
# ============================================================
set -euo pipefail

show_help() {
  cat <<'EOF'
deploy_cloud.sh — selective rsync of the cloud bundle to a server host.

Usage:
  deploy_cloud.sh [--apply] [--bundle PATH] DEST

  DEST        rsync target (e.g. user@server:~/arcface_server/).
              May also be supplied via CLOUD_DEST env var.
  --apply     actually copy. Without this flag, runs with --dry-run.
  --bundle    override path to the file list (default:
              deployment/cloud/CLOUD_BUNDLE.txt).
  -h, --help  show this message.

Does not deploy edge/, run.py, config/, research/, datasets, or local
gallery/ embeddings. The destination must run its own enroll_gallery.py
before starting uvicorn.

Exit codes:
  0 ok / dry-run completed
  1 invalid arguments
  2 bundle file missing
EOF
}

APPLY=0
BUNDLE=""
DEST="${CLOUD_DEST:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) show_help; exit 0 ;;
    --apply) APPLY=1; shift ;;
    --bundle) BUNDLE="${2:-}"; shift 2 ;;
    --) shift; break ;;
    -*) echo "Unknown option: $1" >&2; show_help >&2; exit 1 ;;
    *) DEST="$1"; shift ;;
  esac
done

if [[ -z "$DEST" ]]; then
  echo "Error: DEST not provided (positional arg or CLOUD_DEST env var)." >&2
  show_help >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

BUNDLE="${BUNDLE:-$REPO_ROOT/deployment/cloud/CLOUD_BUNDLE.txt}"

if [[ ! -f "$BUNDLE" ]]; then
  echo "Error: bundle file not found: $BUNDLE" >&2
  exit 2
fi

RSYNC_FLAGS=(
  -avh --relative --human-readable
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='cloud/.venv/'
  --exclude='cloud/gallery/'
  --exclude='cloud/enrollment_images/'
)
if [[ "$APPLY" -eq 0 ]]; then
  RSYNC_FLAGS+=(--dry-run)
fi
RSYNC_FLAGS+=(--files-from "$BUNDLE")

echo "=================================================="
echo "  Cloud deploy plan"
echo "  Repo root : $REPO_ROOT"
echo "  Bundle    : $BUNDLE"
echo "  Dest      : $DEST"
echo "  Mode      : $([[ $APPLY -eq 1 ]] && echo APPLY || echo DRY-RUN)"
echo "  Excludes  : __pycache__/, *.pyc, cloud/.venv/, cloud/gallery/, cloud/enrollment_images/"
echo "=================================================="

cd "$REPO_ROOT"
exec rsync "${RSYNC_FLAGS[@]}" ./ "$DEST"
