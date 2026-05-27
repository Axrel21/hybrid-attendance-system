#!/usr/bin/env bash
# deployment/cloud/run_backend.sh
# ============================================================
# Launch the composite cloud backend (verification + telemetry +
# dashboard + WebSocket) under uvicorn.
#
# Layout:
#   - cwd is set to <repo>/cloud/ so cloud/main.py's lifespan can resolve
#     "gallery/" relative to the verification module's expected location.
#   - --app-dir is set to <repo> so uvicorn can import the cloud_backend
#     package without polluting PYTHONPATH at the shell level.
#
# Usage:
#   bash deployment/cloud/run_backend.sh
#   bash deployment/cloud/run_backend.sh --host 0.0.0.0 --port 8000 --reload
#
# Override the telemetry storage directory:
#   CLOUD_STORAGE_DIR=/var/lib/hybrid-cloud bash deployment/cloud/run_backend.sh
#
# Prerequisites: pip install -r cloud/requirements.txt
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CLOUD_DIR="$REPO_ROOT/cloud"

# Load deployment/env/{profile}.env (HYBRID_PROFILE defaults to development)
# shellcheck source=/dev/null
source "$REPO_ROOT/deployment/common/load_profile.sh"

if [[ ! -d "$CLOUD_DIR" ]]; then
  echo "Error: cloud directory not found at $CLOUD_DIR" >&2
  exit 2
fi

if ! command -v uvicorn >/dev/null 2>&1; then
  echo "Error: uvicorn not on PATH. Activate the cloud venv first:" >&2
  echo "    cd cloud && python -m venv .venv && source .venv/bin/activate" >&2
  echo "    pip install -r requirements.txt" >&2
  exit 3
fi

DEFAULT_ARGS=(--host 0.0.0.0 --port 8000)
ARGS=("$@")
if [[ ${#ARGS[@]} -eq 0 ]]; then
  ARGS=("${DEFAULT_ARGS[@]}")
fi

echo "=================================================="
echo "  Hybrid cloud backend"
echo "  Repo root  : $REPO_ROOT"
echo "  cwd        : $CLOUD_DIR  (gallery/ resolves here)"
echo "  app-dir    : $REPO_ROOT  (cloud_backend importable)"
echo "  args       : ${ARGS[*]}"
echo "  profile    : ${HYBRID_PROFILE:-development}"
echo "  storage    : ${CLOUD_STORAGE_DIR:-<repo>/cloud_storage}"
echo "=================================================="

cd "$CLOUD_DIR"
exec uvicorn --app-dir "$REPO_ROOT" cloud_backend.server:app "${ARGS[@]}"
