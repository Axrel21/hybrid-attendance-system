#!/usr/bin/env bash
# deployment/common/load_profile.sh
# Source before starting cloud backend or surveillance.
#
# Usage:
#   source deployment/common/load_profile.sh
#   HYBRID_PROFILE=demo source deployment/common/load_profile.sh
#
# Profiles: development | demo | production
# Optional overrides: deployment/env/local.env (gitignored)

set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$_SCRIPT_DIR/../.." && pwd)"
ENV_DIR="$REPO_ROOT/deployment/env"

PROFILE="${HYBRID_PROFILE:-development}"
export HYBRID_PROFILE="$PROFILE"

_load_file() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -n "$line" && "$line" == *"="* ]] || continue
    local key="${line%%=*}"
    local value="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    if [[ -n "$key" && -z "${!key+x}" ]]; then
      export "$key=$value"
    fi
  done < "$file"
}

_load_file "$ENV_DIR/${PROFILE}.env"
_load_file "$ENV_DIR/local.env"

echo "[load_profile] HYBRID_PROFILE=$HYBRID_PROFILE (repo=$REPO_ROOT)"
