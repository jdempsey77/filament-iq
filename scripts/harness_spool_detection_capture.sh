#!/usr/bin/env bash
# Spool detection harness: capture a single timestamped snapshot for one AMS slot.
# Usage: ./scripts/harness_spool_detection_capture.sh --slot <1-6> --out <path.json>
# Never fails on 404; records status codes and bodies in JSON.
# Requires: jq, curl. Env: deploy.env / deploy.env.local (HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN, SPOOLMAN_URL).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Source env: .local overrides .env
for f in "$SCRIPT_DIR/deploy.env.local" "$SCRIPT_DIR/deploy.env"; do
  if [[ -f "$f" ]]; then
    set -a; source "$f"; set +a
  fi
done

SLOT=""
OUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --slot) SLOT="$2"; shift 2 ;;
    --out)  OUT="$2";  shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$SLOT" || -z "$OUT" ]]; then
  echo "Usage: $0 --slot <1-6> --out <path.json>" >&2
  exit 1
fi

if [[ ! "$SLOT" =~ ^[1-6]$ ]]; then
  echo "Slot must be 1-6" >&2
  exit 1
fi

# Tray entity and expected location (match appdaemon/apps/ams_rfid_reconcile.py)
get_tray_entity() {
  case "$1" in
    1) echo "sensor.p1s_01p00c5a3101668_ams_1_tray_1" ;;
    2) echo "sensor.p1s_01p00c5a3101668_ams_1_tray_2" ;;
    3) echo "sensor.p1s_01p00c5a3101668_ams_1_tray_3" ;;
    4) echo "sensor.p1s_01p00c5a3101668_ams_1_tray_4" ;;
    5) echo "sensor.p1s_01p00c5a3101668_ams_128_tray_1" ;;
    6) echo "sensor.p1s_01p00c5a3101668_ams_129_tray_1" ;;
    *) echo "" ;;
  esac
}
get_expected_location() {
  case "$1" in
    1) echo "AMS1_Slot1" ;;
    2) echo "AMS1_Slot2" ;;
    3) echo "AMS1_Slot3" ;;
    4) echo "AMS1_Slot4" ;;
    5) echo "AMS128_Slot1" ;;
    6) echo "AMS129_Slot1" ;;
    *) echo "AMS1_Slot$1" ;;
  esac
}

TRAY_ENTITY="$(get_tray_entity "$SLOT")"
EXPECTED_LOCATION="$(get_expected_location "$SLOT")"
HA_BASE="${HOME_ASSISTANT_URL:-}"
HA_TOKEN="${HOME_ASSISTANT_TOKEN:-}"
SPOOLMAN_BASE="${SPOOLMAN_URL:-}"
if [[ -z "$SPOOLMAN_BASE" ]]; then
  SPOOLMAN_BASE="${SPOOLMAN_BASE_URL:-}"
fi
SPOOLMAN_BASE="${SPOOLMAN_BASE%/}"

TS_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Fetch HA state; never exit on 404
ha_state() {
  local entity="$1"
  local url="${HA_BASE}/api/states/${entity}"
  local tmp
  tmp="$(mktemp)"
  local code
  code=$(curl -sS -w "%{http_code}" -o "$tmp" \
    -H "Authorization: Bearer ${HA_TOKEN}" \
    "$url" 2>/dev/null || echo "000")
  if [[ "$code" == "200" ]]; then
    jq -c . "$tmp"
  else
    jq -n --arg code "$code" --arg body "$(cat "$tmp")" '{ status_code: ($code | tonumber), body: $body }'
  fi
  rm -f "$tmp"
}

# Fetch Spoolman; never exit on 404
spoolman_get() {
  local path="$1"
  local tmp
  tmp="$(mktemp)"
  local code
  code=$(curl -sS -w "%{http_code}" -o "$tmp" \
    "${SPOOLMAN_BASE}${path}" 2>/dev/null || echo "000")
  if [[ "$code" == "200" ]]; then
    jq -c . "$tmp"
  else
    jq -n --arg code "$code" --arg body "$(cat "$tmp")" '{ status_code: ($code | tonumber), body: $body }'
  fi
  rm -f "$tmp"
}

HELPER_SPOOL_ID="input_text.ams_slot_${SLOT}_spool_id"
HELPER_TRAY_SIG="input_text.ams_slot_${SLOT}_tray_signature"

TRAY_STATE="$(ha_state "$TRAY_ENTITY")"
SPOOL_ID_STATE="$(ha_state "$HELPER_SPOOL_ID")"
TRAY_SIG_STATE="$(ha_state "$HELPER_TRAY_SIG")"

# Derived: helper_spool_id_int
SPOOL_ID_VAL=""
if echo "$SPOOL_ID_STATE" | jq -e '.state' >/dev/null 2>&1; then
  SPOOL_ID_VAL="$(echo "$SPOOL_ID_STATE" | jq -r '.state')"
fi
HELPER_SPOOL_ID_INT=0
if [[ -n "$SPOOL_ID_VAL" ]] && [[ "$SPOOL_ID_VAL" =~ ^[0-9]+$ ]]; then
  HELPER_SPOOL_ID_INT="$SPOOL_ID_VAL"
fi

# Derived: tag_uid, tray_uuid from tray entity
TAG_UID=""
TRAY_UUID=""
if echo "$TRAY_STATE" | jq -e '.attributes' >/dev/null 2>&1; then
  TAG_UID="$(echo "$TRAY_STATE" | jq -r '.attributes.tag_uid // .attributes.tag_uid_hex // ""')"
  TRAY_UUID="$(echo "$TRAY_STATE" | jq -r '.attributes.tray_uuid // .attributes.tray_id // ""')"
fi

# Spoolman: by_helper_id
SPOOLMAN_BY_HELPER='{"status_code":0,"body":null}'
if [[ -n "$SPOOLMAN_BASE" ]] && [[ "$HELPER_SPOOL_ID_INT" -gt 0 ]]; then
  SPOOLMAN_BY_HELPER="$(spoolman_get "/api/v1/spool/${HELPER_SPOOL_ID_INT}")"
fi

# Spoolman: by_tag_uid (list spools, normalize extra.rfid_tag_uid, match)
SPOOLMAN_BY_TAG='{"matching_spool":null,"match_count":0}'
if [[ -n "$SPOOLMAN_BASE" ]] && [[ -n "$TAG_UID" ]]; then
  LIST_PAYLOAD="$(spoolman_get "/api/v1/spool?limit=1000")"
  ITEMS="[]"
  if echo "$LIST_PAYLOAD" | jq -e '.status_code' >/dev/null 2>&1; then
    # Error response from spoolman_get
    : "skip match"
  elif echo "$LIST_PAYLOAD" | jq -e '.items' >/dev/null 2>&1; then
    ITEMS="$(echo "$LIST_PAYLOAD" | jq -c '.items')"
  elif echo "$LIST_PAYLOAD" | jq -e 'type == "array"' >/dev/null 2>&1; then
    ITEMS="$(echo "$LIST_PAYLOAD" | jq -c '.')"
  fi
  if [[ "$ITEMS" == "null" ]]; then
    ITEMS="[]"
  fi
  # Normalize tag: strip quotes/spaces, uppercase
  NORM_TAG="$(echo "$TAG_UID" | tr -d ' \t"' | tr '[:lower:]' '[:upper:]')"
  MATCH_COUNT=0
  MATCHING_SPOOL="null"
  for row in $(echo "$ITEMS" | jq -c '.[]'); do
    extra="$(echo "$row" | jq -r '.extra // "{}"')"
    if [[ "$extra" =~ ^[{\[] ]]; then
      uid="$(echo "$extra" | jq -r '.rfid_tag_uid // .tag_uid // ""')"
    else
      uid=""
    fi
    [[ -z "$uid" ]] && continue
    norm_uid="$(echo "$uid" | tr -d ' \t"' | tr '[:lower:]' '[:upper:]')"
    if [[ "$norm_uid" == "$NORM_TAG" ]]; then
      MATCH_COUNT=$((MATCH_COUNT + 1))
      MATCHING_SPOOL="$(echo "$row" | jq -c .)"
    fi
  done
  SPOOLMAN_BY_TAG="$(jq -n \
    --argjson match "$MATCHING_SPOOL" \
    --argjson count "$MATCH_COUNT" \
    '{ matching_spool: $match, match_count: $count }')"
fi

# Build output JSON
jq -n \
  --arg ts "$TS_UTC" \
  --argjson slot "$SLOT" \
  --argjson tray_state "$TRAY_STATE" \
  --argjson spool_id_state "$SPOOL_ID_STATE" \
  --argjson tray_sig_state "$TRAY_SIG_STATE" \
  --arg tag_uid "$TAG_UID" \
  --arg tray_uuid "$TRAY_UUID" \
  --argjson helper_spool_id_int "$HELPER_SPOOL_ID_INT" \
  --arg expected_location "$EXPECTED_LOCATION" \
  --argjson by_helper "$SPOOLMAN_BY_HELPER" \
  --argjson by_tag "$SPOOLMAN_BY_TAG" \
  '{
    timestamp_utc: $ts,
    slot: $slot,
    ha: {
      tray_entity_state: $tray_state,
      helper_spool_id: $spool_id_state,
      helper_tray_signature: $tray_sig_state
    },
    derived: {
      tag_uid: (if $tag_uid != "" then $tag_uid else null end),
      tray_uuid: (if $tray_uuid != "" then $tray_uuid else null end),
      helper_spool_id_int: $helper_spool_id_int,
      expected_spoolman_location: $expected_location
    },
    spoolman: {
      by_helper_id: $by_helper,
      by_tag_uid: $by_tag
    }
  }' > "$OUT"

echo "Captured slot $SLOT -> $OUT" >&2
