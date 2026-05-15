#!/usr/bin/env bash
# deployment/common/package_cloud.sh
# ============================================================
# Build a transferable tarball of the cloud (ArcFace) bundle.
#
# Usage:
#   bash deployment/common/package_cloud.sh [OUTPUT_DIR]
#
# OUTPUT_DIR defaults to ./dist/ relative to the repository root. The
# tarball is named arcface_server_<UTC timestamp>.tar.gz and contains a
# single top-level directory "arcface_server/" with cloud/, shared/, and
# the requirements_cloud.txt shim.
#
# Excludes cloud/.venv/, cloud/gallery/, cloud/enrollment_images/,
# __pycache__/, and *.pyc — the server must build its own venv and
# enrolled gallery from its own enrollment images.
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${1:-$REPO_ROOT/dist}"
BUNDLE="$REPO_ROOT/deployment/cloud/CLOUD_BUNDLE.txt"

mkdir -p "$OUT_DIR"

if [[ ! -f "$BUNDLE" ]]; then
  echo "Error: bundle file not found: $BUNDLE" >&2
  exit 2
fi

TS="$(date -u +%Y%m%d_%H%M%SZ)"
TARBALL="$OUT_DIR/arcface_server_$TS.tar.gz"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "Staging cloud bundle..."
cd "$REPO_ROOT"
rsync -a \
  --files-from="$BUNDLE" \
  --ignore-missing-args \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='cloud/.venv/' \
  --exclude='cloud/gallery/' \
  --exclude='cloud/enrollment_images/' \
  ./ "$STAGE/arcface_server/"

echo "Creating tarball $TARBALL..."
tar -czf "$TARBALL" -C "$STAGE" arcface_server

echo "Done."
ls -lh "$TARBALL"
