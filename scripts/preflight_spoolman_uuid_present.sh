#!/usr/bin/env bash
# Preflight: verify the UUID generation pipeline works end-to-end.
#   1) Clear the helper to empty
#   2) Call script.spoolman_set_new_spool_uuid via HA API
#   3) Poll input_text.spoolman_new_spool_uuid for up to 5s
#   4) Assert value matches UUID v4 regex
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
HELPER="input_text.spoolman_new_spool_uuid"

# Step 1: Clear the helper so we know a fresh UUID was generated
curl -sS -o /dev/null -X POST \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"entity_id\":\"$HELPER\",\"value\":\"\"}" \
  "$HOME_ASSISTANT_URL/api/services/input_text/set_value" 2>/dev/null || true
sleep 1

# Step 2: Fire the UUID generation script (script.turn_on with entity_id)
http_code=$(curl -sS -o /dev/null -w "%{http_code}" \
  -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"entity_id":"script.spoolman_set_new_spool_uuid"}' \
  "$HOME_ASSISTANT_URL/api/services/script/turn_on" 2>/dev/null || echo "000")

if [[ "$http_code" != "200" ]]; then
  echo "PREFLIGHT_SPOOLMAN_UUID: FAIL — script returned HTTP $http_code"
  echo "  Check: script.spoolman_set_new_spool_uuid defined in scripts.yaml?"
  exit 1
fi

# Step 3: Poll for up to 5s
uuid_regex='^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
for attempt in 1 2 3 4 5; do
  sleep 1
  body=$(curl -sS -H "$AUTH" "$HOME_ASSISTANT_URL/api/states/$HELPER" 2>/dev/null) || true
  state=$(echo "$body" | jq -r '.state // ""' 2>/dev/null)
  if [[ "$state" =~ $uuid_regex ]]; then
    echo "PREFLIGHT_SPOOLMAN_UUID: PASS (uuid=${state}, attempt=${attempt})"
    exit 0
  fi
done

# Step 4: Failed
echo "PREFLIGHT_SPOOLMAN_UUID: FAIL — helper value after 5s: '${state:-empty}'"
echo "  Root cause: python_script import restrictions or Jinja template error."
echo "  Debug: check HA logs for python_script or template errors."
exit 1
