#!/usr/bin/env bash
# Gate: E2E Spoolman UUID pipeline (script.spoolman_set_new_spool_uuid, pure Jinja).
#  a) Clear input_text.spoolman_new_spool_uuid
#  b) POST /api/services/script/turn_on with {"entity_id":"script.spoolman_set_new_spool_uuid"}
#  c) Poll helper up to 5s; if empty dump diagnostics (helper state, script attributes.sequence, Jinja hint)
#  d) Optional: rest_command + Spoolman newest spool extra.ha_spool_uuid (SPOOLMAN_E2E=1)
# Gate PASS only when helper is non-empty and matches UUID format.
# Output: PASS/FAIL checklist + artifacts.

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
SCRIPT_ENTITY="script.spoolman_set_new_spool_uuid"
UUID_REGEX='^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'

log "=== $GATE_NAME ==="
log "Artifact dir: $ARTIFACT_DIR"

# --- (a) Clear helper ---
curl -sS -o /dev/null -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"entity_id\":\"$HELPER\",\"value\":\"\"}" \
  "$HOME_ASSISTANT_URL/api/services/input_text/set_value" 2>/dev/null || true
sleep 1

# --- (b) Trigger UUID via script.turn_on ---
http_code=$(curl -sS -o "$ARTIFACT_DIR/script_turn_on_response.txt" -w "%{http_code}" \
  -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"entity_id\":\"$SCRIPT_ENTITY\"}" \
  "$HOME_ASSISTANT_URL/api/services/script/turn_on" 2>/dev/null || echo "000")

if [[ "$http_code" != "200" ]]; then
  log_fail "script.turn_on ($SCRIPT_ENTITY) returned HTTP $http_code (see $ARTIFACT_DIR/script_turn_on_response.txt)"
else
  log_ok "script.turn_on ($SCRIPT_ENTITY) returned 200"
fi

# --- (c) Poll for UUID; if empty dump diagnostics ---
uuid_val=""
state=""
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
  log "  --- diagnostics ---"
  log "  Helper state (last): $ARTIFACT_DIR/helper_state_5.json"
  if command -v jq >/dev/null 2>&1; then
    [[ -f "$ARTIFACT_DIR/helper_state_5.json" ]] && jq . "$ARTIFACT_DIR/helper_state_5.json" >> "$ARTIFACT_DIR/checklist.txt" 2>/dev/null || true
  fi
  script_json=$(curl -sS -H "$AUTH" "$HOME_ASSISTANT_URL/api/states/$SCRIPT_ENTITY" 2>/dev/null) || true
  echo "$script_json" > "$ARTIFACT_DIR/script_entity.json"
  log "  Script entity ($SCRIPT_ENTITY) attributes.sequence:"
  echo "$script_json" | jq '.attributes.sequence // .' >> "$ARTIFACT_DIR/checklist.txt" 2>/dev/null || true
  log "  Hint: Jinja UUID template may not be supported on this HA version (uuid/uuid4 filters missing). Check script value template."
  if [[ -n "${SSH_HOST:-}" && -n "${SSH_USER:-}" ]] && command -v ssh >/dev/null 2>&1; then
    ssh ${SSH_OPTS:--o StrictHostKeyChecking=accept-new} "$SSH_USER@$SSH_HOST" "ha core logs --no-log-file 2>/dev/null | tail -200" >> "$ARTIFACT_DIR/ha_core_logs_tail.txt" 2>/dev/null || true
    log "  HA logs tail: $ARTIFACT_DIR/ha_core_logs_tail.txt"
  else
    log "  To capture HA logs: set SSH_HOST/SSH_USER and SSH access, or check HA UI Developer Tools -> Logs"
  fi
  log "  --- end diagnostics ---"
fi

# --- (d) Optional: rest_command + Spoolman newest spool extra.ha_spool_uuid ---
if [[ -n "${SPOOLMAN_E2E:-}" && "$SPOOLMAN_E2E" == "1" && -n "${SPOOLMAN_URL:-}" && -n "$uuid_val" ]]; then
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
  log "  SKIP: (d) set SPOOLMAN_E2E=1 and SPOOLMAN_URL for full E2E"
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
