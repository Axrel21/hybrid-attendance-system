#!/usr/bin/env bash
# deployment/common/verify_manifests.sh
# ============================================================
# Sanity-check both deploy manifests by dry-running deploy_pi.sh and
# deploy_cloud.sh into temporary directories. Exits non-zero if either
# bundle file is missing, malformed, or excludes critical paths.
#
# Safe to run in CI: makes no network calls, modifies nothing under the
# repo, and cleans up its temporary directories on exit.
#
# Usage:
#   bash deployment/common/verify_manifests.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

PI_TMP="$(mktemp -d)"
CLOUD_TMP="$(mktemp -d)"
PI_LOG="$(mktemp)"
CLOUD_LOG="$(mktemp)"
trap 'rm -rf "$PI_TMP" "$CLOUD_TMP" "$PI_LOG" "$CLOUD_LOG"' EXIT

echo "=== Pi bundle dry-run ==="
# deploy_pi.sh exits non-zero (rsync code 23) when expected gitignored
# runtime artefacts are missing on this host. That is informational, not
# a manifest failure — check the plan header instead.
bash deployment/pi/deploy_pi.sh "$PI_TMP" >"$PI_LOG" 2>&1 || true
if ! grep -q "Pi deploy plan" "$PI_LOG"; then
  echo "ERROR: deploy_pi.sh did not emit a deploy plan."
  cat "$PI_LOG"
  exit 1
fi
# Spot-check that the critical paths are listed.
for required in "run.py" "edge/" "config/" "deployment/pi/" "shared/"; do
  if ! grep -q "$required" "$PI_LOG"; then
    echo "ERROR: Pi manifest does not include '$required'"
    exit 1
  fi
done
# And that cloud/research/datasets are NOT pulled in.
for forbidden in "cloud/" "research/" "dataset_raw/" "dataset_processed/"; do
  if grep -q "^$forbidden" "$PI_LOG"; then
    echo "ERROR: Pi manifest leaks '$forbidden' onto the device"
    exit 1
  fi
done
echo "    OK"

echo "=== Cloud bundle dry-run ==="
bash deployment/cloud/deploy_cloud.sh "$CLOUD_TMP" >"$CLOUD_LOG" 2>&1 || true
if ! grep -q "Cloud deploy plan" "$CLOUD_LOG"; then
  echo "ERROR: deploy_cloud.sh did not emit a deploy plan."
  cat "$CLOUD_LOG"
  exit 1
fi
for required in "cloud/" "shared/" "requirements_cloud.txt"; do
  if ! grep -q "$required" "$CLOUD_LOG"; then
    echo "ERROR: Cloud manifest does not include '$required'"
    exit 1
  fi
done
for forbidden in "^edge/" "^config/" "^run.py" "^research/"; do
  if grep -q "$forbidden" "$CLOUD_LOG"; then
    echo "ERROR: Cloud manifest leaks '${forbidden#^}' onto the server"
    exit 1
  fi
done
echo "    OK"

echo
echo "Both manifests verified."
