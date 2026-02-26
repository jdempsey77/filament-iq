#!/usr/bin/env bash
# Preflight: verify UUID pipeline (script.spoolman_set_new_spool_uuid -> python_script.gen_uuid).
# 1) Clear input_text.spoolman_new_spool_uuid
# 2) POST /api/services/script/turn_on with {"entity_id":"script.spoolman_set_new_spool_uuid"}
# 3) Poll helper up to 5s; assert UUID format
# 4) If still empty: print helper state JSON, script entity JSON, python_script hint
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
SCRIPT_ENTITY="script.spoolman_set_new_spool_uuid"

# Step 1: Clear helper
curl -sS -o /dev/null -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"entity_id\":\"$HELPER\",\"value\":\"\"}" \
  "$HOME_ASSISTANT_URL/api/services/input_text/set_value" 2>/dev/null || true
sleep 1

# Step 2: Trigger UUID generation via script
http_code=$(curl -sS -o /dev/null -w "%{http_code}" \
  -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"entity_id\":\"$SCRIPT_ENTITY\"}" \
  "$HOME_ASSISTANT_URL/api/services/script/turn_on" 2>/dev/null || echo "000")

if [[ "$http_code" != "200" ]]; then
  echo "PREFLIGHT_SPOOLMAN_UUID: FAIL — script.turn_on ($SCRIPT_ENTITY) returned HTTP $http_code"
  exit 1
fi

# Step 3: Poll for up to 5s
uuid_regex='^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
state=""
for attempt in 1 2 3 4 5; do
  sleep 1
  body=$(curl -sS -H "$AUTH" "$HOME_ASSISTANT_URL/api/states/$HELPER" 2>/dev/null) || true
  state=$(echo "$body" | jq -r '.state // ""' 2>/dev/null)
  if [[ "$state" =~ $uuid_regex ]]; then
    echo "PREFLIGHT_SPOOLMAN_UUID: PASS (uuid=${state}, attempt=${attempt})"
    exit 0
  fi
done

# Step 4: Still empty — fail with diagnostics
echo "PREFLIGHT_SPOOLMAN_UUID: FAIL — helper value after 5s: '${state:-empty}'"
echo "  --- diagnostics ---"
echo "  Helper state:"
echo "$body" | jq . 2>/dev/null || echo "$body"
script_json=$(curl -sS -H "$AUTH" "$HOME_ASSISTANT_URL/api/states/$SCRIPT_ENTITY" 2>/dev/null) || true
echo "  Script entity ($SCRIPT_ENTITY) attributes.sequence:"
echo "$script_json" | jq '.attributes.sequence // .' 2>/dev/null || echo "$script_json"
echo "  Hint: python_script.gen_uuid may be failing (reload Python Scripts; check gen_uuid.py sandbox compatibility)."
echo "  --- end diagnostics ---"
exit 1
