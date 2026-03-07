#!/usr/bin/env bash
# validate_filament_iq.sh — validates FilamentIQ entities against live HA
# Usage:
#   ./scripts/validate_filament_iq.sh                  # check filament_iq_* names (default)
#   ./scripts/validate_filament_iq.sh --pre-migration  # check old p1s_* names
#   ./scripts/validate_filament_iq.sh --fix            # show fixes for missing entities

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"
if [[ ! -f "$DEPLOY_ENV" ]]; then
  echo "Error: $DEPLOY_ENV not found." >&2; exit 1
fi
set -a; source "$DEPLOY_ENV"; set +a

MODE="post"; FIX=0
for arg in "$@"; do
  [[ "$arg" == "--pre-migration" ]] && MODE="pre"
  [[ "$arg" == "--post-migration" ]] && MODE="post"
  [[ "$arg" == "--fix" ]] && FIX=1
done

PASS=0; WARN=0; FAIL=0; FAIL_IDS=()

check_entity() {
  local entity_id="$1"
  local http_code state
  http_code=$(curl -s -o /tmp/fiq_resp.json -w "%{http_code}" \
    -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" \
    "$HOME_ASSISTANT_URL/api/states/$entity_id" 2>/dev/null || echo "000")
  if [[ "$http_code" == "200" ]]; then
    state=$(python3 -c "import json; d=json.load(open('/tmp/fiq_resp.json')); print(d.get('state',''))" 2>/dev/null || echo "")
    if [[ "$state" == "unavailable" || "$state" == "unknown" ]]; then
      echo "  WARN  $entity_id (state: $state)"; (( WARN++ )) || true
    else
      echo "  PASS  $entity_id (state: $state)"; (( PASS++ )) || true
    fi
  elif [[ "$http_code" == "404" ]]; then
    echo "  FAIL  $entity_id (not found)"; (( FAIL++ )) || true; FAIL_IDS+=("$entity_id")
  else
    echo "  WARN  $entity_id (HTTP $http_code)"; (( WARN++ )) || true
  fi
}

section() { echo ""; echo "--- $1 ---"; }

if [[ "$MODE" == "pre" ]]; then
  echo "Mode: --pre-migration (checking old p1s_* entity names)"
  BOOLEANS=(input_boolean.p1s_nonrfid_enabled input_boolean.p1s_print_active input_boolean.p1s_needs_reconcile input_boolean.p1s_debug_finish_trigger input_boolean.appdaemon_startup_suppress_swap input_boolean.filament_debug_mode input_boolean.p1s_decrement_on_failed input_boolean.p1s_auto_mode_opinionated)
  DATETIMES=(input_datetime.p1s_print_start_time input_datetime.p1s_print_end_time)
  TEXTS=(input_text.p1s_trays_used_this_print input_text.p1s_last_mapping_json input_text.p1s_start_json input_text.p1s_end_json input_text.p1s_active_job_key input_text.bambu_printer_access_code input_text.spoolman_base_url input_text.p1s_last_active_tray input_text.p1s_last_print_status_transition input_text.p1s_finish_automation_checkpoint input_text.p1s_slot_to_spool_binding_json)
  BUTTONS=(input_button.p1s_rfid_reconcile_now input_button.p1s_weight_snapshot_now)
  SELECTS=(input_select.spoolman_new_spool_filament)
  NUMBERS=()
  for i in 1 2 3 4 5 6; do
    NUMBERS+=("input_number.p1s_start_slot_${i}_g" "input_number.p1s_end_slot_${i}_g")
  done
  TEMPLATE_SENSORS=(sensor.p1s_operator_status)
else
  echo "Mode: --post-migration (checking new filament_iq_* entity names)"
  BOOLEANS=(input_boolean.filament_iq_nonrfid_enabled input_boolean.filament_iq_print_active input_boolean.filament_iq_needs_reconcile input_boolean.filament_iq_debug_finish_trigger input_boolean.filament_iq_startup_suppress_swap input_boolean.filament_iq_debug_mode input_boolean.filament_iq_decrement_on_failed input_boolean.filament_iq_auto_mode_opinionated)
  DATETIMES=(input_datetime.filament_iq_print_start_time input_datetime.filament_iq_print_end_time)
  TEXTS=(input_text.filament_iq_trays_used_this_print input_text.filament_iq_start_json input_text.filament_iq_end_json input_text.filament_iq_active_job_key input_text.bambu_printer_access_code input_text.spoolman_base_url input_text.filament_iq_last_active_tray input_text.filament_iq_last_print_status_transition input_text.filament_iq_finish_automation_checkpoint input_text.filament_iq_slot_to_spool_binding_json)
  TEMPLATE_SENSORS+=(sensor.filament_iq_last_mapping_json)
  BUTTONS=(input_button.filament_iq_reconcile_now input_button.filament_iq_weight_snapshot_now)
  SELECTS=(input_select.spoolman_new_spool_filament)
  NUMBERS=()
  for i in 1 2 3 4 5 6; do
    NUMBERS+=("input_number.filament_iq_start_slot_${i}_g" "input_number.filament_iq_end_slot_${i}_g")
  done
  TEMPLATE_SENSORS=(sensor.filament_iq_operator_status)
fi

PRINTER_PREFIX="${PRINTER_PREFIX:-p1s_01p00a1b2c3d4e5f}"
PRINTER_SENSORS=("sensor.${PRINTER_PREFIX}_print_status" "sensor.${PRINTER_PREFIX}_active_tray" "sensor.${PRINTER_PREFIX}_task_name")

echo ""
echo "Checking HA connectivity: $HOME_ASSISTANT_URL"
ha_code=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" \
  "$HOME_ASSISTANT_URL/api/" 2>/dev/null || echo "000")
if [[ "$ha_code" != "200" ]]; then
  echo "FAIL: Cannot reach HA API (HTTP $ha_code)."; exit 1
fi
echo "PASS: HA API reachable"

if [[ -n "$SPOOLMAN_URL" ]]; then
  echo ""
  echo "Checking Spoolman: $SPOOLMAN_URL"
  sm_code=$(curl -s -o /dev/null -w "%{http_code}" "$SPOOLMAN_URL/api/v1/info" 2>/dev/null || echo "000")
  if [[ "$sm_code" == "200" ]]; then echo "PASS: Spoolman reachable"
  else echo "WARN: Spoolman HTTP $sm_code"; (( WARN++ )) || true; fi
fi

section "input_boolean";  for e in "${BOOLEANS[@]}";  do check_entity "$e"; done
section "input_datetime"; for e in "${DATETIMES[@]}"; do check_entity "$e"; done
section "input_text";     for e in "${TEXTS[@]}";     do check_entity "$e"; done
section "input_button";   for e in "${BUTTONS[@]}";   do check_entity "$e"; done
section "input_select";   for e in "${SELECTS[@]}";   do check_entity "$e"; done
section "input_number";   for e in "${NUMBERS[@]}";   do check_entity "$e"; done
section "sensor (template)"; for e in "${TEMPLATE_SENSORS[@]}"; do check_entity "$e"; done
section "sensor (printer — $PRINTER_PREFIX)"; for e in "${PRINTER_SENSORS[@]}"; do check_entity "$e"; done

echo ""
echo "========================================"
echo "  PASSED : $PASS"
echo "  WARNED : $WARN"
echo "  FAILED : $FAIL"
echo "========================================"

if [[ $FIX -eq 1 && ${#FAIL_IDS[@]} -gt 0 ]]; then
  echo ""
  echo "--- Fix suggestions ---"
  for entity_id in "${FAIL_IDS[@]}"; do
    domain="${entity_id%%.*}"; name="${entity_id#*.}"
    echo "${domain}:"; echo "  ${name}:"; echo "    name: \"${name}\""; echo ""
  done
fi

[[ $FAIL -gt 0 ]] && exit 1 || exit 0
