#!/usr/bin/env bash
# Preflight: verify UUID pipeline (python_script.gen_uuid) works.
# 1) Assert python_script domain present in /api/services
# 2) Clear input_text.spoolman_new_spool_uuid
# 3) Call POST /api/services/python_script/gen_uuid with {"target":"input_text.spoolman_new_spool_uuid"}
# 4) Poll helper up to 5s; assert UUID v4 format
# Exit: 0 on success, 1 on failure, 0 (SKIP) if deploy.env or HA unreachable.

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

# Step 1: Assert python_script service exists
services_json=$(curl -sS -H "$AUTH" "$HOME_ASSISTANT_URL/api/services" 2>/dev/null) || true
if [[ -z "$services_json" ]]; then
  echo "PREFLIGHT_SPOOLMAN_UUID: FAIL — could not fetch /api/services"
  exit 1
fi
has_ps=$(echo "$services_json" | jq -r '
  if type == "array" then ([.[] | select(.domain == "python_script")] | length > 0)
  elif type == "object" then has("python_script")
  else false end
' 2>/dev/null || echo "false")
if [[ "$has_ps" != "true" ]]; then
  echo "PREFLIGHT_SPOOLMAN_UUID: FAIL — python_script integration not loaded"
  echo "  Reload python_script or restart HA."
  exit 1
fi

# Step 2: Clear helper
curl -sS -o /dev/null -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"entity_id\":\"$HELPER\",\"value\":\"\"}" \
  "$HOME_ASSISTANT_URL/api/services/input_text/set_value" 2>/dev/null || true
sleep 1

# Step 3: Call python_script.gen_uuid directly
http_code=$(curl -sS -o /dev/null -w "%{http_code}" \
  -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"target\":\"$HELPER\"}" \
  "$HOME_ASSISTANT_URL/api/services/python_script/gen_uuid" 2>/dev/null || echo "000")

if [[ "$http_code" != "200" ]]; then
  echo "PREFLIGHT_SPOOLMAN_UUID: FAIL — python_script.gen_uuid returned HTTP $http_code"
  exit 1
fi

# Step 4: Poll for up to 5s
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

echo "PREFLIGHT_SPOOLMAN_UUID: FAIL — helper value after 5s: '${state:-empty}'"
exit 1
