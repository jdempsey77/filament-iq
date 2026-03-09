#!/usr/bin/env bash
# FilamentIQ live regression test — comprehensive checks against HA API.
# Goes deeper than validate_filament_iq.sh: existence, domain, state correctness.
#
# Usage: ./scripts/regression_test.sh [--verbose]
#
# Requires: deploy.env with HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN
# Optional: SPOOLMAN_URL, PRINTER_PREFIX
# Requires: jq
#
# Exit: 0 if all PASS (warnings OK). Exit 1 if any FAIL.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"
VERBOSE=0

for arg in "$@"; do
  case "$arg" in
    --verbose|-v) VERBOSE=1 ;;
    -h|--help)
      echo "Usage: $0 [--verbose]"
      echo "  --verbose  Show all PASS results, not just FAIL/WARN"
      exit 0
      ;;
  esac
done

if [[ ! -f "$DEPLOY_ENV" ]]; then
  echo "Error: $DEPLOY_ENV not found." >&2
  exit 1
fi
set -a
# shellcheck source=/dev/null
source "$DEPLOY_ENV"
set +a

if [[ -z "${HOME_ASSISTANT_URL:-}" || -z "${HOME_ASSISTANT_TOKEN:-}" ]]; then
  echo "Error: HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN must be set in deploy.env." >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "Error: jq is required." >&2
  exit 1
fi

HA_BASE="${HOME_ASSISTANT_URL%/}"
AUTH_HEADER="Authorization: Bearer $HOME_ASSISTANT_TOKEN"
PRINTER_PREFIX="${PRINTER_PREFIX:-p1s_01p00c5a3101668}"

# All filament_iq_* entities (from apps + validate_filament_iq)
declare -a FILAMENT_IQ_ENTITIES=(
  "input_boolean.filament_iq_nonrfid_enabled"
  "input_boolean.filament_iq_startup_suppress_swap"
  "input_boolean.filament_iq_decrement_on_failed"
  "input_boolean.filament_iq_auto_mode_opinionated"
  "input_datetime.filament_iq_print_start_time"
  "input_datetime.filament_iq_print_end_time"
  "input_text.filament_iq_trays_used_this_print"
  # filament_iq_last_mapping_json is now a command_line sensor, checked in GROUP 3
  "input_text.filament_iq_printer_access_code"
  "input_text.filament_iq_last_active_tray"
  "input_text.filament_iq_slot_to_spool_binding_json"
  "input_number.filament_iq_start_slot_1_g"
  "input_number.filament_iq_start_slot_2_g"
  "input_number.filament_iq_start_slot_3_g"
  "input_number.filament_iq_start_slot_4_g"
  "input_number.filament_iq_start_slot_5_g"
  "input_number.filament_iq_start_slot_6_g"
  "input_number.filament_iq_end_slot_1_g"
  "input_number.filament_iq_end_slot_2_g"
  "input_number.filament_iq_end_slot_3_g"
  "input_number.filament_iq_end_slot_4_g"
  "input_number.filament_iq_end_slot_5_g"
  "input_number.filament_iq_end_slot_6_g"
  "input_button.filament_iq_reconcile_now"
  "input_button.filament_iq_weight_snapshot_now"
  "sensor.filament_iq_operator_status"
)

# AMS slot helpers (GROUP 6) — slots 1-6
declare -a AMS_SLOT_ENTITIES=(
  "input_text.ams_slot_1_spool_id"
  "input_text.ams_slot_1_status"
  "input_text.ams_slot_1_unbound_reason"
  "input_text.ams_slot_2_spool_id"
  "input_text.ams_slot_2_status"
  "input_text.ams_slot_2_unbound_reason"
  "input_text.ams_slot_3_spool_id"
  "input_text.ams_slot_3_status"
  "input_text.ams_slot_3_unbound_reason"
  "input_text.ams_slot_4_spool_id"
  "input_text.ams_slot_4_status"
  "input_text.ams_slot_4_unbound_reason"
  "input_text.ams_slot_5_spool_id"
  "input_text.ams_slot_5_status"
  "input_text.ams_slot_5_unbound_reason"
  "input_text.ams_slot_6_spool_id"
  "input_text.ams_slot_6_status"
  "input_text.ams_slot_6_unbound_reason"
)

# Valid operator_status states (from filament_weight_tracker)
VALID_OPERATOR_STATES="printing_normally|idle|failed_requires_intervention|paused|unknown|printing|finished|failed"

# Counters
G1_PASS=0 G1_FAIL=0 G1_WARN=0
G2_PASS=0 G2_FAIL=0
G3_PASS=0 G3_FAIL=0
G4_PASS=0 G4_FAIL=0
G5_PASS=0 G5_FAIL=0
G6_PASS=0 G6_FAIL=0

# Datetime format regex (ISO 8601: 2024-01-15 12:30:00 or 2024-01-15T12:30:00)
DATETIME_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}$'

_fetch_entity() {
  local entity_id="$1"
  curl -sS -H "$AUTH_HEADER" "${HA_BASE}/api/states/${entity_id}" 2>/dev/null || echo "null"
}

_fetch_all_states() {
  curl -sS -H "$AUTH_HEADER" "${HA_BASE}/api/states" 2>/dev/null || echo "[]"
}

_print_result() {
  local status="$1"  # PASS, FAIL, WARN
  local entity_id="$2"
  local msg="${3:-}"
  if [[ "$status" == "FAIL" ]] || [[ "$status" == "WARN" ]]; then
    echo "  $status  $entity_id${msg:+ — $msg}"
  elif [[ $VERBOSE -eq 1 ]]; then
    echo "  PASS  $entity_id${msg:+ — $msg}"
  fi
}

# ---------------------------------------------------------------------------
# GROUP 1 — Entity existence and domain correctness
# ---------------------------------------------------------------------------
echo "[GROUP 1] Entity existence"
states_json=$(_fetch_all_states)
for entity_id in "${FILAMENT_IQ_ENTITIES[@]}"; do
  body=$(_fetch_entity "$entity_id")
  code=$(curl -sS -o /dev/null -w "%{http_code}" -H "$AUTH_HEADER" "${HA_BASE}/api/states/${entity_id}" 2>/dev/null || echo "000")
  if [[ "$code" != "200" || -z "$body" || "$body" == "null" ]]; then
    _print_result "FAIL" "$entity_id" "HTTP $code or not found"
    G1_FAIL=$((G1_FAIL + 1))
    continue
  fi
  domain="${entity_id%%.*}"
  actual_domain=$(echo "$body" | jq -r '.entity_id | split(".")[0] // ""')
  if [[ "$actual_domain" != "$domain" ]]; then
    _print_result "FAIL" "$entity_id" "domain mismatch (expected $domain)"
    G1_FAIL=$((G1_FAIL + 1))
    continue
  fi
  state=$(echo "$body" | jq -r '.state // ""')
  if [[ "$state" == "unavailable" ]]; then
    _print_result "WARN" "$entity_id" "state unavailable"
    G1_WARN=$((G1_WARN + 1))
  else
    [[ $VERBOSE -eq 1 ]] && _print_result "PASS" "$entity_id"
    G1_PASS=$((G1_PASS + 1))
  fi
done
echo ""

# ---------------------------------------------------------------------------
# GROUP 2 — Helper type correctness
# ---------------------------------------------------------------------------
echo "[GROUP 2] Helper type correctness"
for entity_id in "${FILAMENT_IQ_ENTITIES[@]}"; do
  body=$(_fetch_entity "$entity_id")
  [[ -z "$body" || "$body" == "null" ]] && continue
  domain="${entity_id%%.*}"
  state=$(echo "$body" | jq -r '.state // ""')
  attrs=$(echo "$body" | jq -r '.attributes // {}')
  ok=1
  case "$domain" in
    input_boolean)
      if [[ "$state" != "on" && "$state" != "off" ]]; then
        _print_result "FAIL" "$entity_id" "state must be on/off, got: $state"
        G2_FAIL=$((G2_FAIL + 1)); ok=0
      fi
      ;;
    input_text)
      max_len=$(echo "$attrs" | jq -r '.max // empty')
      if [[ -n "$max_len" && "$max_len" != "null" ]]; then
        if [[ "${max_len:-0}" -lt 255 ]] 2>/dev/null; then
          _print_result "FAIL" "$entity_id" "max length $max_len < 255"
          G2_FAIL=$((G2_FAIL + 1)); ok=0
        fi
      fi
      ;;
    input_number)
      min_a=$(echo "$attrs" | jq -r 'has("min")')
      max_a=$(echo "$attrs" | jq -r 'has("max")')
      if [[ "$min_a" != "true" || "$max_a" != "true" ]]; then
        _print_result "FAIL" "$entity_id" "missing min/max attributes"
        G2_FAIL=$((G2_FAIL + 1)); ok=0
      fi
      ;;
    input_datetime)
      if [[ -n "$state" && "$state" != "unknown" ]]; then
        if ! echo "$state" | grep -qE "$DATETIME_RE"; then
          _print_result "FAIL" "$entity_id" "state not datetime format: $state"
          G2_FAIL=$((G2_FAIL + 1)); ok=0
        fi
      fi
      ;;
    input_button)
      # state is timestamp or "unknown" — any non-empty is acceptable
      ;;
    input_select)
      opts=$(echo "$attrs" | jq -r '.options // []')
      if [[ "$opts" == "null" || "$opts" == "[]" ]]; then
        _print_result "FAIL" "$entity_id" "missing options list"
        G2_FAIL=$((G2_FAIL + 1)); ok=0
      fi
      ;;
    sensor)
      ;;
    *) ;;
  esac
  if [[ $ok -eq 1 && "$domain" != "sensor" ]]; then
    [[ $VERBOSE -eq 1 ]] && _print_result "PASS" "$entity_id" "type OK"
    G2_PASS=$((G2_PASS + 1))
  fi
done
# input_select.spoolman_new_spool_filament (not filament_iq_ but used by FilamentIQ)
body=$(_fetch_entity "input_select.spoolman_new_spool_filament")
if [[ -n "$body" && "$body" != "null" ]]; then
  opts=$(echo "$body" | jq -r '.attributes.options // []')
  if [[ "$opts" == "null" || "$opts" == "[]" ]]; then
    _print_result "FAIL" "input_select.spoolman_new_spool_filament" "missing options list"
    G2_FAIL=$((G2_FAIL + 1))
  else
    [[ $VERBOSE -eq 1 ]] && _print_result "PASS" "input_select.spoolman_new_spool_filament" "options OK"
    G2_PASS=$((G2_PASS + 1))
  fi
fi
echo ""

# ---------------------------------------------------------------------------
# GROUP 3 — AppDaemon health
# ---------------------------------------------------------------------------
echo "[GROUP 3] AppDaemon health"
# filament_iq_last_mapping_json: command_line sensor — valid JSON or empty
body=$(_fetch_entity "sensor.filament_iq_last_mapping_json")
if [[ -n "$body" && "$body" != "null" ]]; then
  state=$(echo "$body" | jq -r '.state // ""')
  if [[ -n "$state" && "$state" != "unknown" && "$state" != "unavailable" ]]; then
    if ! echo "$state" | jq -e . >/dev/null 2>&1; then
      _print_result "FAIL" "sensor.filament_iq_last_mapping_json" "invalid JSON"
      G3_FAIL=$((G3_FAIL + 1))
    else
      [[ $VERBOSE -eq 1 ]] && _print_result "PASS" "sensor.filament_iq_last_mapping_json" "valid JSON"
      G3_PASS=$((G3_PASS + 1))
    fi
  else
    [[ $VERBOSE -eq 1 ]] && _print_result "PASS" "sensor.filament_iq_last_mapping_json" "empty/unknown OK"
    G3_PASS=$((G3_PASS + 1))
  fi
fi

# filament_iq_slot_to_spool_binding_json: valid JSON
body=$(_fetch_entity "input_text.filament_iq_slot_to_spool_binding_json")
if [[ -n "$body" && "$body" != "null" ]]; then
  state=$(echo "$body" | jq -r '.state // ""')
  if [[ -n "$state" && "$state" != "unknown" && "$state" != "unavailable" ]]; then
    if ! echo "$state" | jq -e . >/dev/null 2>&1; then
      _print_result "FAIL" "input_text.filament_iq_slot_to_spool_binding_json" "invalid JSON"
      G3_FAIL=$((G3_FAIL + 1))
    else
      [[ $VERBOSE -eq 1 ]] && _print_result "PASS" "input_text.filament_iq_slot_to_spool_binding_json" "valid JSON"
      G3_PASS=$((G3_PASS + 1))
    fi
  else
    [[ $VERBOSE -eq 1 ]] && _print_result "PASS" "input_text.filament_iq_slot_to_spool_binding_json" "empty OK"
    G3_PASS=$((G3_PASS + 1))
  fi
fi

# sensor.filament_iq_operator_status: known valid states
body=$(_fetch_entity "sensor.filament_iq_operator_status")
if [[ -n "$body" && "$body" != "null" ]]; then
  state=$(echo "$body" | jq -r '.state // ""')
  if [[ -n "$state" && "$state" != "unavailable" ]]; then
    if ! echo "$state" | grep -qE "^($VALID_OPERATOR_STATES)$"; then
      _print_result "FAIL" "sensor.filament_iq_operator_status" "unknown state: $state"
      G3_FAIL=$((G3_FAIL + 1))
    else
      [[ $VERBOSE -eq 1 ]] && _print_result "PASS" "sensor.filament_iq_operator_status" "state=$state"
      G3_PASS=$((G3_PASS + 1))
    fi
  else
    [[ $VERBOSE -eq 1 ]] && _print_result "PASS" "sensor.filament_iq_operator_status" "unavailable (warn)"
    G3_PASS=$((G3_PASS + 1))
  fi
fi
echo ""

# ---------------------------------------------------------------------------
# GROUP 4 — Spoolman connectivity
# ---------------------------------------------------------------------------
echo "[GROUP 4] Spoolman connectivity"
SPOOLMAN_URL="${SPOOLMAN_URL:-}"
if [[ -z "$SPOOLMAN_URL" && -n "$states_json" ]]; then
  spool_url=$(echo "$states_json" | jq -r '.[] | select(.entity_id == "input_text.spoolman_base_url") | .state // ""' 2>/dev/null | head -1)
  [[ -n "$spool_url" && "$spool_url" != "unknown" ]] && SPOOLMAN_URL="$spool_url"
fi
if [[ -n "$SPOOLMAN_URL" ]]; then
  SPOOLMAN_BASE="${SPOOLMAN_URL%/}"
  for path in "/api/v1/info" "/api/v1/spool" "/api/v1/filament"; do
    code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 5 "${SPOOLMAN_BASE}${path}" 2>/dev/null || echo "000")
    if [[ "$code" == "200" ]]; then
      if [[ "$path" == "/api/v1/spool" ]]; then
        body=$(curl -sS --max-time 5 "${SPOOLMAN_BASE}${path}" 2>/dev/null)
        is_array=$(echo "$body" | jq -r 'if type == "array" then "ok" elif .items != null then "ok" else "fail" end' 2>/dev/null || echo "fail")
        if [[ "$is_array" == "ok" ]]; then
          [[ $VERBOSE -eq 1 ]] && echo "  PASS  GET ${SPOOLMAN_BASE}${path} (200, array)"
          G4_PASS=$((G4_PASS + 1))
        else
          echo "  FAIL  GET ${SPOOLMAN_BASE}${path} — response not array"
          G4_FAIL=$((G4_FAIL + 1))
        fi
      else
        [[ $VERBOSE -eq 1 ]] && echo "  PASS  GET ${SPOOLMAN_BASE}${path} (200)"
        G4_PASS=$((G4_PASS + 1))
      fi
    else
      echo "  FAIL  GET ${SPOOLMAN_BASE}${path} — HTTP $code"
      G4_FAIL=$((G4_FAIL + 1))
    fi
  done
else
  echo "  SKIP  SPOOLMAN_URL not set"
fi
echo ""

# ---------------------------------------------------------------------------
# GROUP 5 — Printer sensors (ha-bambulab)
# ---------------------------------------------------------------------------
echo "[GROUP 5] Printer sensors (ha-bambulab)"
for suf in "_print_status" "_active_tray"; do
  entity_id="sensor.${PRINTER_PREFIX}${suf}"
  code=$(curl -sS -o /dev/null -w "%{http_code}" -H "$AUTH_HEADER" "${HA_BASE}/api/states/${entity_id}" 2>/dev/null || echo "000")
  if [[ "$code" == "200" ]]; then
    [[ $VERBOSE -eq 1 ]] && _print_result "PASS" "$entity_id"
    G5_PASS=$((G5_PASS + 1))
  else
    _print_result "FAIL" "$entity_id" "HTTP $code"
    G5_FAIL=$((G5_FAIL + 1))
  fi
done
echo ""

# ---------------------------------------------------------------------------
# GROUP 6 — AMS slot helpers
# ---------------------------------------------------------------------------
echo "[GROUP 6] AMS slot helpers"
for entity_id in "${AMS_SLOT_ENTITIES[@]}"; do
  code=$(curl -sS -o /dev/null -w "%{http_code}" -H "$AUTH_HEADER" "${HA_BASE}/api/states/${entity_id}" 2>/dev/null || echo "000")
  if [[ "$code" == "200" ]]; then
    [[ $VERBOSE -eq 1 ]] && _print_result "PASS" "$entity_id"
    G6_PASS=$((G6_PASS + 1))
  else
    _print_result "FAIL" "$entity_id" "HTTP $code"
    G6_FAIL=$((G6_FAIL + 1))
  fi
done
echo ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "========================================"
echo "GROUP 1: $G1_PASS passed, $G1_WARN warned, $G1_FAIL failed"
echo "GROUP 2: $G2_PASS passed, $G2_FAIL failed"
echo "GROUP 3: $G3_PASS passed, $G3_FAIL failed"
echo "GROUP 4: $G4_PASS passed, $G4_FAIL failed"
echo "GROUP 5: $G5_PASS passed, $G5_FAIL failed"
echo "GROUP 6: $G6_PASS passed, $G6_FAIL failed"
TOTAL_PASS=$((G1_PASS + G2_PASS + G3_PASS + G4_PASS + G5_PASS + G6_PASS))
TOTAL_WARN=$G1_WARN
TOTAL_FAIL=$((G1_FAIL + G2_FAIL + G3_FAIL + G4_FAIL + G5_FAIL + G6_FAIL))
echo "TOTAL: $TOTAL_PASS passed, $TOTAL_WARN warned, $TOTAL_FAIL failed"
echo "========================================"

if [[ $TOTAL_FAIL -gt 0 ]]; then
  exit 1
fi
exit 0
