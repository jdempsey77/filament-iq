#!/usr/bin/env bash
# Capture evidence for helpers zombie / restored-unavailable debugging.
# Usage: ./scripts/capture_helpers_evidence.sh
# Saves to .artifacts/skill/<timestamp>/logs/. Requires deploy.env (or deploy.env.local) with HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
for f in "$SCRIPT_DIR/deploy.env" "$SCRIPT_DIR/deploy.env.local"; do
  if [[ -f "$f" ]]; then
    set -a; source "$f"; set +a
    break
  fi
done
: "${HOME_ASSISTANT_URL:?Set HOME_ASSISTANT_URL in deploy.env or deploy.env.local}"
: "${HOME_ASSISTANT_TOKEN:?Set HOME_ASSISTANT_TOKEN in deploy.env or deploy.env.local}"

TS="$(date +%Y%m%d_%H%M%S)"
ART_ROOT="${REPO_ROOT}/.artifacts/skill/${TS}"
LOG_DIR="${ART_ROOT}/logs"
mkdir -p "$LOG_DIR"

AUTH="Authorization: Bearer $HOME_ASSISTANT_TOKEN"
BASE="$HOME_ASSISTANT_URL"

echo "Capturing evidence to $LOG_DIR ..."

curl -sS -H "$AUTH" "$BASE/api/config" > "$LOG_DIR/api_config.json" || true
curl -sS -H "$AUTH" "$BASE/api/states/input_text.spoolman_base_url" > "$LOG_DIR/state_spoolman_base_url.json" || true
curl -sS -H "$AUTH" "$BASE/api/states/input_text.p1s_last_mapping_json" > "$LOG_DIR/state_p1s_last_mapping_json.json" || true
curl -sS -H "$AUTH" "$BASE/api/states" > "$LOG_DIR/api_states.json" || true

if command -v jq >/dev/null 2>&1; then
  jq 'length' "$LOG_DIR/api_states.json" 2>/dev/null > "$LOG_DIR/states_count.txt" || true
  jq '[.[] | select(.entity_id | startswith("input_text."))] | length' "$LOG_DIR/api_states.json" 2>/dev/null > "$LOG_DIR/input_text_count.txt" || true
  jq '[.[] | select(.entity_id | startswith("input_text."))] | .[0:5]' "$LOG_DIR/api_states.json" 2>/dev/null > "$LOG_DIR/input_text_sample.json" || true
fi

echo "Curl captures done. See $LOG_DIR"
if [[ -n "${HA_SSH_HOST:-}" ]]; then
  echo "Running SSH evidence on $HA_SSH_HOST ..."
  ssh ${SSH_OPTS:-} "$HA_SSH_HOST" "ha core info" > "$LOG_DIR/ha_core_info.txt" 2>&1 || true
  ssh ${SSH_OPTS:-} "$HA_SSH_HOST" "ha core logs -n 300 2>/dev/null | egrep -i 'safe mode|safe_mode|input_text|restored|unavailable|yaml|config|error|warning' || true" > "$LOG_DIR/ha_core_logs_grep.txt" 2>&1 || true
  ssh ${SSH_OPTS:-} "$HA_SSH_HOST" "grep -n '^input_text:' /config/configuration.yaml 2>/dev/null || true; grep -n 'spoolman_base_url' /config/configuration.yaml 2>/dev/null || true; grep -n 'ams_slot_1_expected_spool_id' /config/configuration.yaml 2>/dev/null || true" > "$LOG_DIR/config_grep.txt" 2>&1 || true
  echo "SSH captures done."
else
  echo "HA_SSH_HOST not set; skipping SSH evidence."
fi

echo "Evidence in $ART_ROOT"
