#!/usr/bin/env bash
# regression_test.sh — comprehensive live regression test for FilamentIQ
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"
if [[ ! -f "$DEPLOY_ENV" ]]; then echo "Error: $DEPLOY_ENV not found." >&2; exit 1; fi
set -a; source "$DEPLOY_ENV"; set +a

VERBOSE=0; [[ "${1:-}" == "--verbose" ]] && VERBOSE=1
PRINTER_PREFIX="${PRINTER_PREFIX:-p1s_01p00c5a3101668}"
PASS=0; WARN=0; FAIL=0; GROUP_PASS=0; GROUP_WARN=0; GROUP_FAIL=0

_get_entity() { curl -s -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" "$HOME_ASSISTANT_URL/api/states/$1" 2>/dev/null; }
_http_code()  { curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" "$HOME_ASSISTANT_URL/api/states/$1" 2>/dev/null; }
_state() { echo "$1" | python3 -c "import json,sys; print(json.load(sys.stdin).get('state',''))" 2>/dev/null || echo ""; }
_attr()  { echo "$1" | python3 -c "import json,sys; print(json.load(sys.stdin).get('attributes',{}).get('$2',''))" 2>/dev/null || echo ""; }

pass() { (( PASS++  )) || true; (( GROUP_PASS++  )) || true; [[ $VERBOSE -eq 1 ]] && echo "  PASS  $*"; }
warn() { (( WARN++  )) || true; (( GROUP_WARN++  )) || true; echo "  WARN  $*"; }
fail() { (( FAIL++  )) || true; (( GROUP_FAIL++  )) || true; echo "  FAIL  $*"; }
group_summary() { echo "  -> $1: ${GROUP_PASS} passed, ${GROUP_WARN} warned, ${GROUP_FAIL} failed"; GROUP_PASS=0; GROUP_WARN=0; GROUP_FAIL=0; }

BOOLEANS=(input_boolean.filament_iq_nonrfid_enabled input_boolean.filament_iq_print_active input_boolean.filament_iq_needs_reconcile input_boolean.filament_iq_debug_finish_trigger input_boolean.filament_iq_startup_suppress_swap input_boolean.filament_iq_debug_mode input_boolean.filament_iq_decrement_on_failed input_boolean.filament_iq_auto_mode_opinionated)
TEXTS=(input_text.filament_iq_trays_used_this_print input_text.filament_iq_last_mapping_json input_text.filament_iq_start_json input_text.filament_iq_end_json input_text.filament_iq_active_job_key input_text.filament_iq_last_active_tray input_text.filament_iq_last_print_status_transition input_text.filament_iq_finish_automation_checkpoint input_text.filament_iq_slot_to_spool_binding_json input_text.bambu_printer_access_code input_text.spoolman_base_url)
BUTTONS=(input_button.filament_iq_reconcile_now input_button.filament_iq_weight_snapshot_now)
SELECTS=(input_select.spoolman_new_spool_filament)
NUMBERS=(); for i in 1 2 3 4 5 6; do NUMBERS+=("input_number.filament_iq_start_slot_${i}_g" "input_number.filament_iq_end_slot_${i}_g"); done
DATETIMES=(input_datetime.filament_iq_print_start_time input_datetime.filament_iq_print_end_time)
ALL_ENTITIES=("${BOOLEANS[@]}" "${TEXTS[@]}" "${BUTTONS[@]}" "${SELECTS[@]}" "${NUMBERS[@]}" "${DATETIMES[@]}" sensor.filament_iq_operator_status)

echo ""
echo "Checking HA connectivity..."
ha_code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" "$HOME_ASSISTANT_URL/api/" 2>/dev/null || echo "000")
[[ "$ha_code" != "200" ]] && echo "FAIL: Cannot reach HA API (HTTP $ha_code)" && exit 1
echo "PASS: HA API reachable"

echo ""; echo "[GROUP 1] Entity existence and domain"
for entity in "${ALL_ENTITIES[@]}"; do
  code=$(_http_code "$entity")
  if [[ "$code" == "200" ]]; then
    state=$(_state "$(_get_entity "$entity")")
    [[ "$state" == "unavailable" ]] && warn "$entity (unavailable)" || pass "$entity"
  else fail "$entity (HTTP $code)"; fi
done
group_summary "GROUP 1"

echo ""; echo "[GROUP 2] Helper type correctness"
for entity in "${BOOLEANS[@]}"; do
  state=$(_state "$(_get_entity "$entity")")
  if [[ "$state" == "on" || "$state" == "off" ]]; then pass "$entity ($state)"
  elif [[ "$state" == "unavailable" || "$state" == "unknown" ]]; then warn "$entity ($state)"
  else fail "$entity -- expected on/off, got: $state"; fi
done
for entity in "${TEXTS[@]}"; do
  resp=$(_get_entity "$entity"); maxlen=$(_attr "$resp" "max")
  if [[ -z "$maxlen" ]]; then warn "$entity -- no max attribute"
  elif [[ "$maxlen" -ge 255 ]]; then pass "$entity (max: $maxlen)"
  else fail "$entity -- max=$maxlen < 255"; fi
done
for entity in "${NUMBERS[@]}"; do
  resp=$(_get_entity "$entity"); minv=$(_attr "$resp" "min"); maxv=$(_attr "$resp" "max")
  [[ -n "$minv" && -n "$maxv" ]] && pass "$entity (min:$minv max:$maxv)" || fail "$entity -- missing min/max"
done
for entity in "${DATETIMES[@]}"; do
  state=$(_state "$(_get_entity "$entity")")
  if [[ "$state" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2} ]]; then pass "$entity ($state)"
  elif [[ "$state" == "unknown" || "$state" == "unavailable" ]]; then warn "$entity ($state)"
  else fail "$entity -- unexpected: $state"; fi
done
for entity in "${BUTTONS[@]}"; do
  code=$(_http_code "$entity"); [[ "$code" == "200" ]] && pass "$entity" || fail "$entity (not found)"
done
for entity in "${SELECTS[@]}"; do
  resp=$(_get_entity "$entity"); opts=$(_attr "$resp" "options")
  [[ -n "$opts" && "$opts" != "[]" ]] && pass "$entity (has options)" || warn "$entity -- options empty"
done
group_summary "GROUP 2"

echo ""; echo "[GROUP 3] AppDaemon health"
resp=$(_get_entity "input_text.filament_iq_last_mapping_json"); state=$(_state "$resp")
if [[ -z "$state" || "$state" == "unknown" ]]; then warn "last_mapping_json empty (AppDaemon not yet written)"
else echo "$state" | python3 -c "import json,sys; json.loads(sys.stdin.read())" 2>/dev/null && pass "last_mapping_json valid JSON" || fail "last_mapping_json invalid JSON: $state"; fi

resp=$(_get_entity "input_text.filament_iq_slot_to_spool_binding_json"); state=$(_state "$resp")
if [[ -z "$state" || "$state" == "unknown" ]]; then warn "slot_to_spool_binding_json empty"
else echo "$state" | python3 -c "import json,sys; json.loads(sys.stdin.read())" 2>/dev/null && pass "slot_to_spool_binding_json valid JSON" || fail "slot_to_spool_binding_json invalid JSON"; fi

state=$(_state "$(_get_entity "sensor.filament_iq_operator_status")")
echo "printing_normally idle failed_requires_intervention paused unknown unavailable" | grep -qw "$state" && pass "operator_status ($state)" || fail "operator_status unexpected: $state"
group_summary "GROUP 3"

echo ""; echo "[GROUP 4] Spoolman connectivity"
if [[ -z "$SPOOLMAN_URL" ]]; then warn "SPOOLMAN_URL not set -- skipping"
else
  for endpoint in "api/v1/info" "api/v1/spool" "api/v1/filament"; do
    code=$(curl -s -o /tmp/fiq_sm.json -w "%{http_code}" "$SPOOLMAN_URL/$endpoint" 2>/dev/null || echo "000")
    if [[ "$code" == "200" ]]; then
      if [[ "$endpoint" == "api/v1/spool" ]]; then
        count=$(python3 -c "import json; print(len(json.load(open('/tmp/fiq_sm.json'))))" 2>/dev/null || echo "0")
        [[ "$count" -gt 0 ]] && pass "$endpoint (${count} spools)" || warn "$endpoint -- no spools"
      else pass "$endpoint"; fi
    else fail "$endpoint (HTTP $code)"; fi
  done
fi
group_summary "GROUP 4"

echo ""; echo "[GROUP 5] Printer sensors ($PRINTER_PREFIX)"
for suffix in print_status active_tray task_name; do
  entity="sensor.${PRINTER_PREFIX}_${suffix}"; code=$(_http_code "$entity")
  if [[ "$code" == "200" ]]; then state=$(_state "$(_get_entity "$entity")"); pass "$entity ($state)"
  else fail "$entity (not found)"; fi
done
group_summary "GROUP 5"

echo ""; echo "[GROUP 6] AMS slot helpers"
for slot in 1 2 3 4 5 6; do
  for suffix in spool_id status unbound_reason; do
    entity="input_text.ams_slot_${slot}_${suffix}"; code=$(_http_code "$entity")
    if [[ "$code" == "200" ]]; then state=$(_state "$(_get_entity "$entity")"); pass "$entity ($state)"
    else fail "$entity (not found)"; fi
  done
done
group_summary "GROUP 6"

echo ""
echo "========================================"
echo "  PASSED : $PASS"
echo "  WARNED : $WARN"
echo "  FAILED : $FAIL"
echo "========================================"
[[ $FAIL -gt 0 ]] && exit 1 || exit 0
