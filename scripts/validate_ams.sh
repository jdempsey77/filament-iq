#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"

if [[ ! -f "$DEPLOY_ENV" ]]; then
  echo "AMS_VALIDATE: SKIP (deploy.env not found)"
  exit 0
fi
set -a; source "$DEPLOY_ENV"; set +a

if [[ -z "${HOME_ASSISTANT_URL:-}" || -z "${HOME_ASSISTANT_TOKEN:-}" || -z "${SPOOLMAN_URL:-}" ]]; then
  echo "AMS_VALIDATE: SKIP (HOME_ASSISTANT_URL/TOKEN or SPOOLMAN_URL not set)"
  exit 0
fi
if [[ -n "${VALIDATE_AMS_SKIP:-}" ]]; then
  echo "AMS_VALIDATE: SKIP (VALIDATE_AMS_SKIP set)"
  exit 0
fi

PRINTER="p1s_01p00c5a3101668"
ALLZERO="0000000000000000"

PASS=0
FAIL=0
WARN=0
FAIL_LINES=()

SLOT_FILTER="1 2 3 4 5 6"

# ----------------------------
# Arg Parsing
# ----------------------------
if [[ "${1:-}" == "--slot" && -n "${2:-}" ]]; then
  SLOT_FILTER="$2"
elif [[ "${1:-}" == "--slots" && -n "${2:-}" ]]; then
  SLOT_FILTER="$(echo "$2" | tr ',' ' ')"
fi

# ----------------------------

ha() {
  curl -sS -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" \
    "$HOME_ASSISTANT_URL/api/states/$1"
}

spool_http() {
  local id="$1"
  local tmp; tmp="$(mktemp)"
  local http
  http="$(curl -sS -w "%{http_code}" -o "$tmp" "$SPOOLMAN_URL/api/v1/spool/$id" || true)"
  echo "$http $tmp"
}

tray_entity() {
  case "$1" in
    1) echo "sensor.${PRINTER}_ams_1_tray_1" ;;
    2) echo "sensor.${PRINTER}_ams_1_tray_2" ;;
    3) echo "sensor.${PRINTER}_ams_1_tray_3" ;;
    4) echo "sensor.${PRINTER}_ams_1_tray_4" ;;
    5) echo "sensor.${PRINTER}_ams_128_tray_1" ;;
    6) echo "sensor.${PRINTER}_ams_129_tray_1" ;;
    *) return 1 ;;
  esac
}

record_ok() {
  PASS=$((PASS+1))
  echo "$1"
}

record_fail() {
  FAIL=$((FAIL+1))
  echo "$1"
  FAIL_LINES+=("$1")
}

record_warn() {
  WARN=$((WARN+1))
  echo "WARN  $1"
}

action_for() {
  local line="$1"

  if echo "$line" | grep -q "NO_HELPER"; then
    echo "ACTION: Set input_text.ams_slot_<N>_spool_id to the correct Spoolman ID."
    return
  fi
  if echo "$line" | grep -q "SPOOL_HTTP_404"; then
    echo "ACTION: Helper spool does not exist. Clear helper and rebind."
    return
  fi
  if echo "$line" | grep -q "HELPER_RFID_MISMATCH"; then
    echo "ACTION: RFID slot mismatch. Clear helper or bind matching RFID spool."
    return
  fi
  if echo "$line" | grep -q "MATERIAL_MISMATCH"; then
    echo "ACTION: Material mismatch. Correct helper binding."
    return
  fi

  echo "ACTION: Inspect slot manually."
}

color_distance_warn() {
  # simple heuristic: if tray is very dark and spool color is very light, or vice versa
  local tray_hex="$1"
  local spool_hex="$2"

  tray_hex="${tray_hex#\#}"
  tray_hex="${tray_hex%FF}"

  if [ ${#tray_hex} -ne 6 ] || [ ${#spool_hex} -ne 6 ]; then
    return
  fi

  tray_lum=$(( 0x${tray_hex:0:2} + 0x${tray_hex:2:2} + 0x${tray_hex:4:2} ))
  spool_lum=$(( 0x${spool_hex:0:2} + 0x${spool_hex:2:2} + 0x${spool_hex:4:2} ))

  diff=$(( tray_lum - spool_lum ))
  diff=${diff#-}

  if [ "$diff" -gt 300 ]; then
    return 0
  fi
  return 1
}

check_slot() {
  local slot="$1"
  local tray="$(tray_entity "$slot")"
  local helper_entity="input_text.ams_slot_${slot}_spool_id"

  local tray_json helper_json empty tag tray_type tray_color tray_state helper

  tray_json="$(ha "$tray")"
  helper_json="$(ha "$helper_entity")"

  empty="$(echo "$tray_json" | jq -r '.attributes.empty')"
  tag="$(echo "$tray_json" | jq -r '.attributes.tag_uid // ""')"
  tray_type="$(echo "$tray_json" | jq -r '.attributes.type // ""')"
  tray_color="$(echo "$tray_json" | jq -r '.attributes.color // ""')"
  tray_state="$(echo "$tray_json" | jq -r '.state // ""')"
  helper="$(echo "$helper_json" | jq -r '.state | tonumber? // 0')"

  if [ "$empty" = "true" ]; then
    record_ok "SLOT $slot OK   reason=TRAY_EMPTY"
    return
  fi

  # RFID MODE
  if [ -n "$tag" ] && [ "$tag" != "$ALLZERO" ]; then
    if [ "$helper" -le 0 ]; then
      record_fail "SLOT $slot FAIL mode=RFID_VISIBLE reason=NO_HELPER tag=$tag"
      return
    fi

    read -r http tmp < <(spool_http "$helper")
    if [ "$http" != "200" ]; then
      rm -f "$tmp"
      record_fail "SLOT $slot FAIL mode=RFID_VISIBLE helper=$helper reason=SPOOL_HTTP_$http"
      return
    fi

    # v4: identity in lot_nr only (no extra.rfid_tag_uid)
    sm_lot_nr="$(jq -r '.lot_nr // ""' "$tmp" 2>/dev/null | tr -d '"')"
    rm -f "$tmp"
    tag_norm="$(echo "$tag" | tr -d '"' | tr '[:lower:]' '[:upper:]')"
    lot_norm="$(echo "$sm_lot_nr" | tr -d '"' | tr '[:lower:]' '[:upper:]')"

    if [ -z "$lot_norm" ] || [ "$lot_norm" != "$tag_norm" ]; then
      record_fail "SLOT $slot FAIL mode=RFID_VISIBLE reason=HELPER_RFID_MISMATCH (lot_nr)"
      return
    fi

    record_ok "SLOT $slot OK   mode=RFID_VISIBLE reason=LOT_NR_MATCH"
    return
  fi

  # IDENTITY_UNAVAILABLE
  if [ "$helper" -le 0 ]; then
    record_fail "SLOT $slot FAIL mode=IDENTITY_UNAVAILABLE reason=NO_HELPER"
    return
  fi

  read -r http tmp < <(spool_http "$helper")
  if [ "$http" != "200" ]; then
    rm -f "$tmp"
    record_fail "SLOT $slot FAIL mode=IDENTITY_UNAVAILABLE helper=$helper reason=SPOOL_HTTP_$http"
    return
  fi

  sm_mat="$(jq -r '.filament.material // ""' "$tmp")"
  sm_color="$(jq -r '.filament.color_hex // ""' "$tmp")"
  rm -f "$tmp"

  tt="$(printf "%s" "$tray_type" | tr '[:lower:]' '[:upper:]')"
  mm="$(printf "%s" "$sm_mat" | tr '[:lower:]' '[:upper:]')"

  if [ "$tt" != "$mm" ]; then
    record_fail "SLOT $slot FAIL mode=IDENTITY_UNAVAILABLE reason=MATERIAL_MISMATCH"
    return
  fi

  # Soft color warn
  if color_distance_warn "$tray_color" "$sm_color"; then
    record_warn "SLOT $slot color mismatch tray=$tray_color spool=$sm_color"
  fi

  record_ok "SLOT $slot OK   mode=IDENTITY_UNAVAILABLE helper=$helper"
}

echo "=== AMS VALIDATE $(date -u +"%Y-%m-%dT%H:%M:%SZ") ==="

for s in $SLOT_FILTER; do
  check_slot "$s"
done

echo
echo "=== SUMMARY ==="
echo "PASS=$PASS FAIL=$FAIL WARN=$WARN"

if [ "$FAIL" -gt 0 ]; then
  echo
  echo "=== FAILURES ==="
  for line in "${FAIL_LINES[@]}"; do
    echo "$line"
    action_for "$line"
    echo
  done
  exit 2
fi

exit 0
