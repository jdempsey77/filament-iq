#!/usr/bin/env bash
# Preflight: verify the UUID generation pipeline works end-to-end.
#   1) Call script.spoolman_set_new_spool_uuid via HA API
#   2) Wait briefly, then read input_text.spoolman_new_spool_uuid
#   3) Assert value matches UUID v4 regex
# Exit: 0 on success, 1 on failure, 0 (SKIP) if HA unreachable or deploy.env missing.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"
if [[ ! -f "$DEPLOY_ENV" ]]; then
  echo "PREFLIGHT_SPOOLMAN_UUID: SKIP (deploy.env not found)"
  exit 0
fi
set -a; source "$DEPLOY_ENV"; set +a

if [[ -z "${HOME_ASSISTANT_URL:-}" || -z "${HOME_ASSISTANT_TOKEN:-}" ]]; then
  echo "PREFLIGHT_SPOOLMAN_UUID: SKIP (HOME_ASSISTANT_URL/TOKEN not set)"
  exit 0
fi

AUTH="Authorization: Bearer $HOME_ASSISTANT_TOKEN"

# Fire the UUID generation script
http_code=$(curl -sS -o /dev/null -w "%{http_code}" \
  -X POST \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{}' \
  "$HOME_ASSISTANT_URL/api/services/script/spoolman_set_new_spool_uuid" 2>/dev/null || echo "000")

if [[ "$http_code" != "200" ]]; then
  echo "PREFLIGHT_SPOOLMAN_UUID: FAIL — script.spoolman_set_new_spool_uuid returned HTTP $http_code"
  echo "  Possible causes: python_script: not enabled, gen_uuid.py missing, or script not defined."
  exit 1
fi

# Wait for the helper to be populated
sleep 2

# Read the helper value
body=$(curl -sS -H "$AUTH" "$HOME_ASSISTANT_URL/api/states/input_text.spoolman_new_spool_uuid" 2>/dev/null) || true
if [[ -z "$body" ]]; then
  echo "PREFLIGHT_SPOOLMAN_UUID: FAIL — could not read input_text.spoolman_new_spool_uuid"
  exit 1
fi

state=$(echo "$body" | jq -r '.state // ""' 2>/dev/null)
if [[ -z "$state" || "$state" == "unknown" || "$state" == "unavailable" ]]; then
  echo "PREFLIGHT_SPOOLMAN_UUID: FAIL — helper state is '${state}' (empty/unknown/unavailable)"
  exit 1
fi

uuid_regex='^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
if [[ "$state" =~ $uuid_regex ]]; then
  echo "PREFLIGHT_SPOOLMAN_UUID: PASS (uuid=${state})"
  exit 0
else
  echo "PREFLIGHT_SPOOLMAN_UUID: FAIL — helper value '${state}' does not match UUID v4 regex"
  exit 1
fi
