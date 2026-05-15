#!/usr/bin/env bash
# deployment/pi/deploy_pi.sh
# ============================================================
# Selective rsync of the edge bundle to a Raspberry Pi.
#
# Defaults to --dry-run. Pass --apply to actually copy.
#
# Usage:
#   bash deployment/pi/deploy_pi.sh pi@raspberrypi:~/attendance/
#   bash deployment/pi/deploy_pi.sh --apply pi@raspberrypi:~/attendance/
#   PI_DEST=pi@host:~/attendance/ bash deployment/pi/deploy_pi.sh --apply
#
# Reads the file list from deployment/pi/PI_BUNDLE.txt.
# Does NOT modify the Pi-side environment or restart any service.
# ============================================================
set -euo pipefail

show_help() {
  cat <<'EOF'
deploy_pi.sh — selective rsync of the edge bundle to a Raspberry Pi.

Usage:
  deploy_pi.sh [--apply] [--bundle PATH] DEST

  DEST        rsync target (e.g. pi@raspberrypi:~/attendance/).
              May also be supplied via PI_DEST env var.
  --apply     actually copy. Without this flag, runs with --dry-run
              so you can review the changes first.
  --bundle    override path to the file list (default:
              deployment/pi/PI_BUNDLE.txt).
  -h, --help  show this message.

Reads paths from deployment/pi/PI_BUNDLE.txt by default. Does not deploy
cloud/, research/, datasets, or archived experiment outputs.

Exit codes:
  0 ok / dry-run completed
  1 invalid arguments
  2 bundle file missing
  3 required artifact (e.g. data/known_faces.json) absent in workspace
EOF
}

APPLY=0
BUNDLE=""
DEST="${PI_DEST:-}"

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
  echo "Error: DEST not provided (positional arg or PI_DEST env var)." >&2
  show_help >&2
  exit 1
fi

# Resolve repo root from this script's location.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

BUNDLE="${BUNDLE:-$REPO_ROOT/deployment/pi/PI_BUNDLE.txt}"

if [[ ! -f "$BUNDLE" ]]; then
  echo "Error: bundle file not found: $BUNDLE" >&2
  exit 2
fi

# Preflight: warn (not fail) on missing runtime artifacts so a fresh clone
# without enrollment can still produce a useful dry-run preview.
MISSING_REQUIRED=0
for required in data/known_faces.json; do
  if [[ ! -f "$REPO_ROOT/$required" ]]; then
    echo "WARN: $required missing (build via python -m enrollment.enroll)" >&2
    MISSING_REQUIRED=1
  fi
done
if [[ "$MISSING_REQUIRED" -eq 1 && "$APPLY" -eq 1 ]]; then
  echo "Refusing to --apply with missing required artifacts. Build them first." >&2
  exit 3
fi

RSYNC_FLAGS=(
  -avh --relative --human-readable
  --exclude='__pycache__/'
  --exclude='*.pyc'
)
if [[ "$APPLY" -eq 0 ]]; then
  RSYNC_FLAGS+=(--dry-run)
fi
RSYNC_FLAGS+=(--files-from "$BUNDLE")

echo "=================================================="
echo "  Pi deploy plan"
echo "  Repo root : $REPO_ROOT"
echo "  Bundle    : $BUNDLE"
echo "  Dest      : $DEST"
echo "  Mode      : $([[ $APPLY -eq 1 ]] && echo APPLY || echo DRY-RUN)"
echo "=================================================="

cd "$REPO_ROOT"
exec rsync "${RSYNC_FLAGS[@]}" ./ "$DEST"
