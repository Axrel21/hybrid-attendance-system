#!/usr/bin/env bash
# deployment/common/package_pi.sh
# ============================================================
# Build a transferable tarball of the Pi bundle.
#
# Usage:
#   bash deployment/common/package_pi.sh [OUTPUT_DIR]
#
# OUTPUT_DIR defaults to ./dist/ relative to the repository root.
# The tarball is named attendance_pi_<UTC timestamp>.tar.gz and contains
# a single top-level directory "attendance/" mirroring the layout the
# Pi expects under its install root (e.g. /home/pi/attendance/).
#
# Reads the file list from deployment/pi/PI_BUNDLE.txt. Excludes
# __pycache__/ and *.pyc. Side-effect-free at the destination; the
# staging directory is cleaned up on exit.
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${1:-$REPO_ROOT/dist}"
BUNDLE="$REPO_ROOT/deployment/pi/PI_BUNDLE.txt"

mkdir -p "$OUT_DIR"

if [[ ! -f "$BUNDLE" ]]; then
  echo "Error: bundle file not found: $BUNDLE" >&2
  exit 2
fi

TS="$(date -u +%Y%m%d_%H%M%SZ)"
TARBALL="$OUT_DIR/attendance_pi_$TS.tar.gz"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "Staging Pi bundle..."
cd "$REPO_ROOT"
# Use rsync to honour the manifest, exclude caches, and resolve symlinks
# the same way deploy_pi.sh does. --ignore-missing-args lets us package
# from a fresh clone where gitignored runtime artefacts (models/,
# data/known_faces.json) may not yet exist — the tarball is still useful
# for source review, and the caller is warned below.
rsync -a \
  --files-from="$BUNDLE" \
  --ignore-missing-args \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  ./ "$STAGE/attendance/"

# Warn (do not fail) on missing required runtime artefacts so the caller
# knows the tarball is incomplete in a dev/CI clone.
MISSING=()
for required in data/known_faces.json models/yunet.onnx models/mobilefacenet.tflite; do
  if [[ ! -e "$STAGE/attendance/$required" ]]; then
    MISSING+=("$required")
  fi
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "WARN: tarball is missing runtime artefacts (expected on a dev clone):"
  for m in "${MISSING[@]}"; do echo "    - $m"; done
fi

echo "Creating tarball $TARBALL..."
tar -czf "$TARBALL" -C "$STAGE" attendance

echo "Done."
ls -lh "$TARBALL"
