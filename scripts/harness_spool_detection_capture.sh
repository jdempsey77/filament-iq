#!/usr/bin/env bash
# Spool detection harness: capture a single timestamped snapshot for one AMS slot.
# Usage: ./scripts/harness_spool_detection_capture.sh --slot <1-6> --out <path.json>
# Never fails on 404 or bad response; always writes valid JSON. Records status_code, content_type, body, json (when parseable).
# Requires: jq, curl. Env: deploy.env / deploy.env.local (HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN, SPOOLMAN_URL).
#
# Self-test (must produce valid JSON and exit 0):
#   HOME_ASSISTANT_URL=http://127.0.0.1:9 ./scripts/harness_spool_detection_capture.sh --slot 1 --out /tmp/test.json
#   jq . /tmp/test.json

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HARNESS_DEBUG="${HARNESS_DEBUG:-0}"

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

# HTTP GET helper: always returns valid JSON. Never feeds raw response into jq for parsing.
# Output: { "status_code": N, "content_type": "...", "body": "...", "json": <parsed or null> }
# Parses body as JSON only when status_code==200, body non-empty, and body (trimmed) starts with { or [.
http_get() {
  local url="$1"
  local auth="${2:-}"
  local tmp_body
  tmp_body="$(mktemp)"
  local tmp_meta
  tmp_meta="$(mktemp)"
  rm -f "$tmp_body" "$tmp_meta"
  tmp_body="$(mktemp)"
  tmp_meta="$(mktemp)"
  local code="000"
  local content_type=""
  if [[ -n "$auth" ]]; then
    curl -sS -o "$tmp_body" -w "%{http_code}\n%{content_type}" \
      -H "Authorization: Bearer $auth" \
      "$url" 2>/dev/null >"$tmp_meta" || true
  else
    curl -sS -o "$tmp_body" -w "%{http_code}\n%{content_type}" \
      "$url" 2>/dev/null >"$tmp_meta" || true
  fi
  code="$(head -n 1 "$tmp_meta" 2>/dev/null || echo "000")"
  content_type="$(tail -n 1 "$tmp_meta" 2>/dev/null || echo "")"
  [[ -z "$content_type" ]] && content_type="application/octet-stream"

  if [[ "$HARNESS_DEBUG" == "1" ]]; then
    echo "harness_debug: url=$url status_code=$code json_attempt=see_below" >&2
  fi

  # Build JSON: body as string, json only when safe to parse. Use jq -n and --rawfile so body is never parsed from shell.
  jq -n \
    --arg code "$code" \
    --arg ct "$content_type" \
    --rawfile body "$tmp_body" \
    '
      ($body | gsub("^[ \t\r\n]+"; "") | .[0:1]) as $first |
      (
        if ($code | tonumber) == 200 and ($body | length) > 0 and ($first == "{" or $first == "[") then
          try ($body | fromjson) catch null
        else
          null
        end
      ) as $parsed |
      {
        status_code: ($code | if . == "" then 0 else tonumber end),
        content_type: $ct,
        body: $body,
        json: $parsed
      }
    '
  rm -f "$tmp_body" "$tmp_meta"
}

# HA state: GET with auth. Returns same shape as http_get.
ha_state() {
  local entity="$1"
  local url="${HA_BASE}/api/states/${entity}"
  if [[ "$HARNESS_DEBUG" == "1" ]]; then
    echo "harness_debug: ha_state url=$url" >&2
  fi
  http_get "$url" "${HA_TOKEN:-}"
}

# Spoolman GET. Returns same shape as http_get.
spoolman_get() {
  local path="$1"
  local url="${SPOOLMAN_BASE}${path}"
  if [[ "$HARNESS_DEBUG" == "1" ]]; then
    echo "harness_debug: spoolman_get url=$url" >&2
  fi
  http_get "$url" ""
}

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

HELPER_SPOOL_ID="input_text.ams_slot_${SLOT}_spool_id"
HELPER_TRAY_SIG="input_text.ams_slot_${SLOT}_tray_signature"

# Fetch HA states (always valid JSON from http_get)
TRAY_STATE_JSON="$(ha_state "$TRAY_ENTITY")"
SPOOL_ID_STATE_JSON="$(ha_state "$HELPER_SPOOL_ID")"
TRAY_SIG_STATE_JSON="$(ha_state "$HELPER_TRAY_SIG")"

if [[ "$HARNESS_DEBUG" == "1" ]]; then
  echo "harness_debug: tray_state status_code=$(echo "$TRAY_STATE_JSON" | jq -r '.status_code') json_ok=$(echo "$TRAY_STATE_JSON" | jq -r 'if .json != null then "yes" else "no" end')" >&2
fi

# Derived from .json when present; otherwise default. Use only jq on our known-valid response objects.
SPOOL_ID_VAL="$(echo "$SPOOL_ID_STATE_JSON" | jq -r 'if .json != null and .json.state != null then (.json.state | tostring) else "" end')"
HELPER_SPOOL_ID_INT=0
if [[ -n "$SPOOL_ID_VAL" ]] && [[ "$SPOOL_ID_VAL" =~ ^[0-9]+$ ]]; then
  HELPER_SPOOL_ID_INT="$SPOOL_ID_VAL"
fi

TAG_UID="$(echo "$TRAY_STATE_JSON" | jq -r 'if .json != null and .json.attributes != null then (.json.attributes.tag_uid // .json.attributes.tag_uid_hex // "") else "" end')"
TRAY_UUID="$(echo "$TRAY_STATE_JSON" | jq -r 'if .json != null and .json.attributes != null then (.json.attributes.tray_uuid // .json.attributes.tray_id // "") else "" end')"
# Normalize empty to literal empty string for jq --arg
TAG_UID="${TAG_UID:-}"
TRAY_UUID="${TRAY_UUID:-}"

# Spoolman: by_helper_id (always valid JSON object)
SPOOLMAN_BY_HELPER='{"status_code":0,"content_type":"","body":"","json":null}'
if [[ -n "$SPOOLMAN_BASE" ]] && [[ "$HELPER_SPOOL_ID_INT" -gt 0 ]]; then
  SPOOLMAN_BY_HELPER="$(spoolman_get "/api/v1/spool/${HELPER_SPOOL_ID_INT}")"
fi

# Spoolman: by_tag_uid. If list response is not valid JSON, set response + error.
SPOOLMAN_BY_TAG_JSON='{"matching_spool":null,"match_count":0}'
if [[ -n "$SPOOLMAN_BASE" ]] && [[ -n "$TAG_UID" ]]; then
  LIST_RESPONSE="$(spoolman_get "/api/v1/spool?limit=1000")"
  LIST_JSON="$(echo "$LIST_RESPONSE" | jq -r '.json')"
  if [[ "$LIST_JSON" == "null" ]] || [[ -z "$LIST_JSON" ]]; then
    # Non-JSON or empty: store raw response, zero matches, error (build from valid LIST_RESPONSE so body never passes through shell)
    SPOOLMAN_BY_TAG_JSON="$(echo "$LIST_RESPONSE" | jq '{ response: .body, matching_spool: null, match_count: 0, error: "non_json_response" }')"
  else
    # Valid JSON: extract items array (or use root if array)
    ITEMS_JSON="$(echo "$LIST_RESPONSE" | jq -c 'if .json == null then [] elif (.json | type) == "array" then .json elif .json.items != null then .json.items else [] end')"
    if [[ "$ITEMS_JSON" == "null" ]]; then
      ITEMS_JSON="[]"
    fi
    NORM_TAG="$(echo "$TAG_UID" | tr -d ' \t"' | tr '[:lower:]' '[:upper:]')"
    MATCH_COUNT=0
    MATCHING_SPOOL="null"
    # Iterate only over valid JSON array elements
    idx=0
    len="$(echo "$ITEMS_JSON" | jq 'length')"
    while [[ "$idx" -lt "$len" ]]; do
      row="$(echo "$ITEMS_JSON" | jq -c ".[$idx]")"
      extra="$(echo "$row" | jq -r '.extra // "{}"')"
      uid=""
      if [[ "$extra" =~ ^[{\[] ]]; then
        uid="$(echo "$extra" | jq -r '.rfid_tag_uid // .tag_uid // ""')"
      fi
      if [[ -n "$uid" ]]; then
        norm_uid="$(echo "$uid" | tr -d ' \t"' | tr '[:lower:]' '[:upper:]')"
        if [[ "$norm_uid" == "$NORM_TAG" ]]; then
          MATCH_COUNT=$((MATCH_COUNT + 1))
          MATCHING_SPOOL="$row"
        fi
      fi
      idx=$((idx + 1))
    done
    SPOOLMAN_BY_TAG_JSON="$(jq -n \
      --argjson match "$MATCHING_SPOOL" \
      --argjson count "$MATCH_COUNT" \
      '{ matching_spool: $match, match_count: $count }')"
  fi
fi

# Build final output with jq -n only; all inputs are controlled (args or known-valid JSON from our helpers)
# Pass response objects as filenames to avoid shell/JSON embedding issues: write each to temp file then jq slurp.
TRAY_TMP="$(mktemp)"
SPOOL_ID_TMP="$(mktemp)"
TRAY_SIG_TMP="$(mktemp)"
BY_HELPER_TMP="$(mktemp)"
BY_TAG_TMP="$(mktemp)"
printf '%s' "$TRAY_STATE_JSON" > "$TRAY_TMP"
printf '%s' "$SPOOL_ID_STATE_JSON" > "$SPOOL_ID_TMP"
printf '%s' "$TRAY_SIG_STATE_JSON" > "$TRAY_SIG_TMP"
printf '%s' "$SPOOLMAN_BY_HELPER" > "$BY_HELPER_TMP"
printf '%s' "$SPOOLMAN_BY_TAG_JSON" > "$BY_TAG_TMP"

jq -n \
  --arg ts "$TS_UTC" \
  --argjson slot "$SLOT" \
  --arg tag_uid "$TAG_UID" \
  --arg tray_uuid "$TRAY_UUID" \
  --argjson helper_spool_id_int "$HELPER_SPOOL_ID_INT" \
  --arg expected_location "$EXPECTED_LOCATION" \
  --slurpfile tray_state "$TRAY_TMP" \
  --slurpfile spool_id_state "$SPOOL_ID_TMP" \
  --slurpfile tray_sig_state "$TRAY_SIG_TMP" \
  --slurpfile by_helper "$BY_HELPER_TMP" \
  --slurpfile by_tag "$BY_TAG_TMP" \
  '
    $tray_state[0] as $tray_state |
    $spool_id_state[0] as $spool_id_state |
    $tray_sig_state[0] as $tray_sig_state |
    $by_helper[0] as $by_helper |
    $by_tag[0] as $by_tag |
    {
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
    }
  ' > "$OUT"

rm -f "$TRAY_TMP" "$SPOOL_ID_TMP" "$TRAY_SIG_TMP" "$BY_HELPER_TMP" "$BY_TAG_TMP"

echo "Captured slot $SLOT -> $OUT" >&2
