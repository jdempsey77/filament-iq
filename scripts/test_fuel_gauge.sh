#!/usr/bin/env bash
# test_fuel_gauge.sh
# Usage:
#   ./test_fuel_gauge.sh preflight
#   ./test_fuel_gauge.sh reset
#   ./test_fuel_gauge.sh watch [seconds] [interval]
#   ./test_fuel_gauge.sh once
#
# Notes:
# - Sources ./deploy.env (or path via DEPLOY_ENV)
# - Requires: curl, jq
# - Expects HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN in deploy.env

set -euo pipefail

DEPLOY_ENV="${DEPLOY_ENV:-./deploy.env}"

if [[ -f "$DEPLOY_ENV" ]]; then
  # shellcheck disable=SC1090
  source "$DEPLOY_ENV"
else
  echo "ERROR: deploy.env not found at: $DEPLOY_ENV"
  echo "Set DEPLOY_ENV=/path/to/deploy.env or run from repo root."
  exit 1
fi

: "${HOME_ASSISTANT_URL:?HOME_ASSISTANT_URL is not set (in deploy.env)}"
: "${HOME_ASSISTANT_TOKEN:?HOME_ASSISTANT_TOKEN is not set (in deploy.env)}"

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: missing command: $1"; exit 1; }; }
need_cmd curl
need_cmd jq

AUTH=(-H "Authorization: Bearer $HOME_ASSISTANT_TOKEN")
JSON=(-H "Content-Type: application/json")

ha_get_state() {
  local entity_id="$1"
  curl -s "${AUTH[@]}" "$HOME_ASSISTANT_URL/api/states/$entity_id" | jq -r '.state'
}

ha_service() {
  local domain_service="$1" payload="$2"
  curl -s -X POST "${AUTH[@]}" "${JSON[@]}" -d "$payload" \
    "$HOME_ASSISTANT_URL/api/services/$domain_service" >/dev/null
}

# Entities (edit here if you ever rename)
PRINT_STATUS_ENTITY="sensor.p1s_01p00c5a3101668_print_status"
INIT_DEBUG_ENTITY="input_text.p1s_init_seed_debug"
START_JSON_ENTITY="input_text.p1s_tray_remaining_start_json"
END_JSON_ENTITY="input_text.p1s_tray_remaining_end_json"

AUTO_INIT="automation.p1s_init_remaining_filament_snapshots"
AUTO_FIRST_ACTIVE="automation.p1s_snapshot_tray_remaining_on_first_active"
AUTO_FINISH="automation.p1s_snapshot_remaining_on_print_finish"

cmd="${1:-watch}"

preflight() {
  echo "HOME_ASSISTANT_URL=$HOME_ASSISTANT_URL"
  echo "Init automation:        $(ha_get_state "$AUTO_INIT")"
  echo "First-active automation:$(ha_get_state "$AUTO_FIRST_ACTIVE")"
  echo "Finish automation:      $(ha_get_state "$AUTO_FINISH")"
}

reset_helpers() {
  ha_service "input_text/set_value" "{\"entity_id\":\"$START_JSON_ENTITY\",\"value\":\"{}\"}"
  ha_service "input_text/set_value" "{\"entity_id\":\"$END_JSON_ENTITY\",\"value\":\"{}\"}"
  ha_service "input_text/set_value" "{\"entity_id\":\"$INIT_DEBUG_ENTITY\",\"value\":\"RESET\"}"
  echo "Helpers reset:"
  echo "  $INIT_DEBUG_ENTITY=RESET"
  echo "  $START_JSON_ENTITY={}"
  echo "  $END_JSON_ENTITY={}"
}

once() {
  local ps debug start end
  ps="$(ha_get_state "$PRINT_STATUS_ENTITY")"
  debug="$(ha_get_state "$INIT_DEBUG_ENTITY")"
  start="$(ha_get_state "$START_JSON_ENTITY")"
  end="$(ha_get_state "$END_JSON_ENTITY")"

  echo "print_status = $ps"
  echo "debug        = $debug"
  echo "start_json   = $start"
  echo "end_json     = $end"
}

watch() {
  local duration="${1:-60}"
  local interval="${2:-2}"
  local i=0
  local end_ts=$(( $(date +%s) + duration ))

  echo "⏳ Monitoring for ${duration}s (every ${interval}s)..."
  while [[ $(date +%s) -lt $end_ts ]]; do
    i=$((i+1))
    echo "----- [$i] $(date '+%Y-%m-%d %H:%M:%S')"
    once
    sleep "$interval"
  done
}

case "$cmd" in
  preflight)
    preflight
    ;;
  reset)
    reset_helpers
    ;;
  once)
    once
    ;;
  watch)
    watch "${2:-60}" "${3:-2}"
    ;;
  *)
    echo "Usage:"
    echo "  $0 preflight"
    echo "  $0 reset"
    echo "  $0 once"
    echo "  $0 watch [seconds] [interval]"
    exit 1
    ;;
esac
