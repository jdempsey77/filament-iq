#!/usr/bin/env bash
# Gate: E2E Spoolman UUID pipeline.
#  a) Clear input_text.spoolman_new_spool_uuid
#  b) Call script.spoolman_set_new_spool_uuid; assert helper has UUID format
#  c) Optionally call rest_command.ams_spoolman_create_spool and assert newest spool has extra.ha_spool_uuid (if SPOOLMAN_E2E=1 and Spoolman reachable)
# Output: PASS/FAIL checklist + artifacts to GATE_ARTIFACT_DIR or .artifacts/skill/gates/<timestamp>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"
GATE_NAME="gate_spoolman_uuid_e2e"
TS=$(date +%Y%m%d_%H%M%S)
ARTIFACT_DIR="${GATE_ARTIFACT_DIR:-$REPO_ROOT/.artifacts/skill/gates/$GATE_NAME-$TS}"
mkdir -p "$ARTIFACT_DIR"

checklist_pass=0
checklist_fail=0
log() { echo "$*" | tee -a "$ARTIFACT_DIR/checklist.txt"; }
log_fail() { echo "  FAIL: $*" | tee -a "$ARTIFACT_DIR/checklist.txt"; checklist_fail=$(( checklist_fail + 1 )); }
log_ok() { echo "  PASS: $*" | tee -a "$ARTIFACT_DIR/checklist.txt"; checklist_pass=$(( checklist_pass + 1 )); }

if [[ ! -f "$DEPLOY_ENV" ]]; then
  log "GATE_SPOOLMAN_UUID_E2E: SKIP (deploy.env not found)"
  exit 0
fi
set -a; source "$DEPLOY_ENV"; set +a
if [[ -z "${HOME_ASSISTANT_URL:-}" || -z "${HOME_ASSISTANT_TOKEN:-}" ]]; then
  log "GATE_SPOOLMAN_UUID_E2E: SKIP (HOME_ASSISTANT_URL/TOKEN not set)"
  exit 0
fi

AUTH="Authorization: Bearer $HOME_ASSISTANT_TOKEN"
HELPER="input_text.spoolman_new_spool_uuid"
UUID_REGEX='^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'

log "=== $GATE_NAME ==="
log "Artifact dir: $ARTIFACT_DIR"

# --- (a) Clear helper ---
curl -sS -o /dev/null -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"entity_id\":\"$HELPER\",\"value\":\"\"}" \
  "$HOME_ASSISTANT_URL/api/services/input_text/set_value" 2>/dev/null || true
sleep 1

# --- (b) Call script, poll for UUID ---
http_code=$(curl -sS -o "$ARTIFACT_DIR/script_response.txt" -w "%{http_code}" \
  -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"entity_id":"script.spoolman_set_new_spool_uuid"}' \
  "$HOME_ASSISTANT_URL/api/services/script/turn_on" 2>/dev/null || echo "000")

if [[ "$http_code" != "200" ]]; then
  log_fail "script.spoolman_set_new_spool_uuid returned HTTP $http_code (see $ARTIFACT_DIR/script_response.txt)"
else
  log_ok "script.spoolman_set_new_spool_uuid returned 200"
fi

uuid_val=""
for attempt in 1 2 3 4 5; do
  sleep 1
  body=$(curl -sS -H "$AUTH" "$HOME_ASSISTANT_URL/api/states/$HELPER" 2>/dev/null) || true
  echo "$body" > "$ARTIFACT_DIR/helper_state_$attempt.json"
  state=$(echo "$body" | jq -r '.state // ""' 2>/dev/null)
  if [[ "$state" =~ $UUID_REGEX ]]; then
    uuid_val="$state"
    log_ok "helper has UUID after ${attempt}s: $uuid_val"
    break
  fi
done

if [[ -z "$uuid_val" ]]; then
  log_fail "helper still empty/unavailable after 5s (state=${state:-empty})"
fi

# --- (c)(d) Optional: create spool via rest_command, assert newest has extra.ha_spool_uuid ---
if [[ -n "${SPOOLMAN_E2E:-}" && "$SPOOLMAN_E2E" == "1" && -n "${SPOOLMAN_URL:-}" && -n "$uuid_val" ]]; then
  # Call rest_command (uses current helper state)
  rc_code=$(curl -sS -o "$ARTIFACT_DIR/rest_command_response.txt" -w "%{http_code}" \
    -X POST -H "$AUTH" -H "Content-Type: application/json" -d '{}' \
    "$HOME_ASSISTANT_URL/api/services/rest_command/ams_spoolman_create_spool" 2>/dev/null || echo "000")
  if [[ "$rc_code" != "200" ]]; then
    log_fail "rest_command.ams_spoolman_create_spool returned HTTP $rc_code"
  else
    sleep 1
    spools=$(curl -sS "$SPOOLMAN_URL/api/v1/spool" 2>/dev/null) || true
    echo "$spools" > "$ARTIFACT_DIR/spools.json"
    newest_id=$(echo "$spools" | jq -r 'if type == "array" then (sort_by(.id) | last | .id) else empty end' 2>/dev/null)
    extra_ha=$(echo "$spools" | jq -r 'if type == "array" then (sort_by(.id) | last | .extra.ha_spool_uuid // "") else "" end' 2>/dev/null)
    if [[ -n "$extra_ha" && "$extra_ha" == *"$uuid_val"* ]]; then
      log_ok "newest spool (id=$newest_id) has extra.ha_spool_uuid containing UUID"
    else
      log_fail "newest spool (id=$newest_id) extra.ha_spool_uuid='${extra_ha:-empty}' (expected to contain $uuid_val)"
    fi
  fi
else
  log "  SKIP: (c)(d) set SPOOLMAN_E2E=1 and SPOOLMAN_URL for full E2E"
fi

# --- Summary ---
log ""
log "CHECKLIST: $checklist_pass pass, $checklist_fail fail"
log "ARTIFACTS: $ARTIFACT_DIR"

if [[ $checklist_fail -gt 0 ]]; then
  echo "GATE_SPOOLMAN_UUID_E2E: FAIL"
  exit 1
fi
echo "GATE_SPOOLMAN_UUID_E2E: PASS"
exit 0
