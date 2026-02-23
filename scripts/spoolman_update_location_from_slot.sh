#!/usr/bin/env bash
set -euo pipefail
AUDIT_LOG="/config/spoolman_writer_audit.log"
touch "$AUDIT_LOG" 2>/dev/null || true
printf '%s\n' "$(date -Iseconds) RUN script=spoolman_update_location_from_slot pid=$$ user=$(whoami) slot=${1:-} spool_id_raw=${6:-} tag=${3:-}" >> "$AUDIT_LOG" || true

slot="${1:-}"
tray_uuid="${2:-}"
tag_uid="${3:-}"
tray_hex="${4:-}"
tray_type="${5:-}"
spool_id_raw="${6:-0}"
expected_spool_id_raw="${7:-0}"
spoolman_url_raw="${8:-}"
log_file="/config/spoolman_location_debug.log"

spoolman_url="${spoolman_url_raw%/}"

to_int() {
  local val="${1:-0}"
  if [[ "$val" =~ ^[0-9]+$ ]]; then
    echo "$val"
  else
    echo "0"
  fi
}

spool_id="$(to_int "$spool_id_raw")"
expected_spool_id="$(to_int "$expected_spool_id_raw")"

if [[ -z "$slot" ]]; then
  echo "SPOOLMAN_LOCATION_SKIP reason=missing_slot"
  exit 0
fi

# Canonicalize tag_uid for comparison and PATCH (strip quotes/backslashes; match Python canonicalizer).
tag_uid_norm="$(printf '%s' "${tag_uid}" | tr -d '"\\' | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')"
tray_uuid_norm="$(echo "$tray_uuid" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]-')"
echo "SPOOLMAN_LOCATION_DEBUG slot=${slot} tray_uuid=${tray_uuid} tray_uuid_norm=${tray_uuid_norm} tag_uid=${tag_uid} tag_uid_norm=${tag_uid_norm} enroll_action=${enroll_action:-unset}"
printf '%s\n' "SPOOLMAN_LOCATION_DEBUG slot=${slot} tray_uuid=${tray_uuid} tray_uuid_norm=${tray_uuid_norm} tag_uid=${tag_uid} tag_uid_norm=${tag_uid_norm} enroll_action=${enroll_action:-unset}" >> "$log_file" || true
if [[ -z "$tag_uid_norm" || "$tag_uid_norm" == "0000000000000000" || "$tray_uuid_norm" == "00000000000000000000000000000000" ]]; then
  echo "SPOOLMAN_LOCATION_SKIP slot=${slot} reason=non_rfid_tray_change message=\"SKIP: non-RFID tray change; manual rebind required.\" tray_uuid=${tray_uuid} tag_uid=${tag_uid}"
  exit 0
fi

# Phase 0 freeze: gate before SPOOLMAN_URL check and any curl. Only when tag normalizes to real RFID (non-empty, not placeholder) do we require AMS_ALLOW_RFID_WRITES=1.
if [[ -n "$tag_uid_norm" && "$tag_uid_norm" != "0" && "$tag_uid_norm" != "0000000000000000" && "$tag_uid_norm" != "UNKNOWN" && "$tag_uid_norm" != "NULL" && "$tag_uid_norm" != "null" ]]; then
  if [[ "${AMS_ALLOW_RFID_WRITES:-}" != "1" ]]; then
    echo "SPOOLMAN_WRITE_REFUSED: AMS_ALLOW_RFID_WRITES must be 1 to write RFID identity."
    exit 9
  fi
fi

if [[ -z "$spoolman_url" ]]; then
  echo "SPOOLMAN_LOCATION_SKIP slot=${slot} reason=missing_spoolman_url"
  exit 0
fi

if [[ "$spool_id" -le 0 ]]; then
  spool_id="$expected_spool_id"
fi

# RFID truth wins: when tray has tag_uid, resolve to spool_id from Spoolman and use that for location.
if [[ -n "$tag_uid_norm" && "$tag_uid_norm" != "0000000000000000" && "$tag_uid_norm" =~ ^[0-9A-F]+$ ]]; then
  all_spools_for_resolve="$(curl -fsS "${spoolman_url}/api/v1/spool?limit=1000" 2>/dev/null || echo "[]")"
  resolved_id=""
  if [[ -n "$all_spools_for_resolve" ]]; then
    resolved_id="$(
      printf '%s' "$all_spools_for_resolve" | jq -r --arg uid "$tag_uid_norm" '
        (if type == "array" then . elif (type == "object" and (.items | type == "array")) then .items else [] end) as $spools
        | [$spools[]? | select(
            ((.extra // {}).rfid_tag_uid // "") as $raw
            | ($raw | if . == null then "" elif type == "string" then (try fromjson catch .) else tostring end | ascii_downcase) == ($uid | ascii_downcase)
          ) | (.id // 0 | tonumber)]
        | if length == 1 then .[0] | tostring else "" end
      ' 2>/dev/null || true
    )"
  fi
  if [[ -n "$resolved_id" && "$resolved_id" =~ ^[0-9]+$ && "$resolved_id" -gt 0 ]]; then
    echo "RFID_RESOLVE slot=${slot} tag_uid=${tag_uid_norm} -> spool_id=${resolved_id} reason=spoolman_match"
    printf '%s\n' "RFID_RESOLVE slot=${slot} tag_uid=${tag_uid_norm} -> spool_id=${resolved_id} reason=spoolman_match" >> "$log_file" 2>/dev/null || true
    spool_id="$resolved_id"
  fi
fi

if [[ "$spool_id" -le 0 ]]; then
  echo "SPOOLMAN_LOCATION_SKIP slot=${slot} reason=no_bound_spool_id tray_uuid=${tray_uuid} tag_uid=${tag_uid} tray_hex=${tray_hex} tray_type=${tray_type}"
  exit 0
fi

# Physical AMS1 slots only (1–4). Slots 5/6 do not exist as hardware; do not write location for them.
case "$slot" in
  1|2|3|4) location="AMS1_Slot${slot}" ;;
  5|6)
    echo "SPOOLMAN_LOCATION_SKIP slot=${slot} reason=slot_not_physical"
    exit 0
    ;;
  *)
    echo "SPOOLMAN_LOCATION_SKIP slot=${slot} reason=invalid_slot"
    exit 0
    ;;
esac

# Hard guard: never PATCH legacy location (AMS2_HT_*, HT1, HT2) to Spoolman; use Shelf.
if echo "$location" | grep -qiE 'AMS2_HT_|HT1|HT2'; then
  echo "SPOOLMAN_LOCATION_GUARD forcing legacy location to Shelf: ${location}"
  location="Shelf"
fi

# Find existing spool currently assigned to this slot location.
spools_json="$(curl -fsS "${spoolman_url}/api/v1/spool" || echo "[]")"

evict_ids="$(
  printf '%s' "$spools_json" | jq -r --arg sid "$spool_id" --arg loc "$location" '
    .[]?
    | select(((.id // 0) | tonumber) != ($sid | tonumber)
      and ((.location // "") == $loc))
    | (.id // 0 | tonumber)
  ' 2>/dev/null || true
)"

if [[ -n "$evict_ids" ]]; then
  while IFS= read -r old_id; do
    if [[ -n "$old_id" && "$old_id" =~ ^[0-9]+$ && "$old_id" -gt 0 ]]; then
      curl -fsS -X PATCH -H "Content-Type: application/json" \
        -d '{"location":"Shelf"}' \
        "${spoolman_url}/api/v1/spool/${old_id}" >/dev/null
      echo "LOCATION_DISPLACE slot=${slot} old_spool_id=${old_id} -> Shelf"
      echo "SPOOLMAN_LOCATION_EVICTED old_spool_id=${old_id} from=${location} to=Shelf"
    fi
  done <<< "$evict_ids"
fi

# RFID enrollment write-path:
# - Merge extra fields safely (do not overwrite unrelated extras)
# - Store rfid_tag_uid as a JSON-encoded string, per Spoolman text-extra behavior
# - Do not silently overwrite a different existing RFID value
enroll_eval="SKIP\tinvalid_tag_uid"
if [[ -n "$tag_uid_norm" && "$tag_uid_norm" != "0000000000000000" && "$tag_uid_norm" =~ ^[0-9a-fA-F]+$ ]]; then
  if spool_detail_json="$(curl -fsS "${spoolman_url}/api/v1/spool/${spool_id}")"; then
    enroll_eval="$(
      printf '%s' "$spool_detail_json" | jq -r --arg uid "$tag_uid_norm" '
        (.extra // {}) as $e
        | ($e.rfid_tag_uid // "") as $raw
        | ($raw | if . == null then "" elif type == "string" then (try fromjson catch .) else tostring end) as $decoded
        | if (($decoded | tostring | ascii_downcase) == "") then
            "ENROLL\t" + ({extra: ($e + {rfid_tag_uid: ($uid | @json)})} | tojson)
          elif (($decoded | tostring | ascii_downcase) == ($uid | ascii_downcase)) then
            "UNCHANGED\t" + ($decoded | tostring)
          else
            "CONFLICT\t" + ($decoded | tostring)
          end
      ' 2>/dev/null || printf 'SKIP\tjq_error'
    )"
  else
    enroll_eval="SKIP\tfetch_error"
  fi
fi

enroll_action="${enroll_eval%%$'\t'*}"
enroll_payload_or_prev="${enroll_eval#*$'\t'}"
if [[ "$enroll_action" == "$enroll_eval" ]]; then
  enroll_payload_or_prev=""
fi

# Conflict override (safe): allow overwrite only when scanned UID is not bound to any other spool.
if [[ "$enroll_action" == "CONFLICT" && -n "$tag_uid_norm" && "$tag_uid_norm" != "0000000000000000" ]]; then
  all_spools_json="$(curl -fsS "${spoolman_url}/api/v1/spool?limit=1000" || curl -fsS "${spoolman_url}/api/v1/spool" || echo "[]")"
  uid_bound_elsewhere="$(
    printf '%s' "$all_spools_json" | jq -r --arg uid "$tag_uid_norm" --arg sid "$spool_id" '
      # Accept either list response or {"items":[...]} response
      (if type == "array" then . elif (type == "object" and (.items | type == "array")) then .items else [] end) as $spools
      | [
          $spools[]?
          | select(((.id // 0) | tostring) != ($sid | tostring))
          | (.extra // {}) as $e
          | ($e.rfid_tag_uid // "") as $raw
          | ($raw | if . == null then "" elif type == "string" then (try fromjson catch .) else tostring end) as $decoded
          | select(($decoded | tostring | ascii_downcase) == ($uid | ascii_downcase))
          | (.id // 0 | tostring)
        ][0] // ""
    ' 2>/dev/null || true
  )"

  if [[ -n "$uid_bound_elsewhere" && "$uid_bound_elsewhere" != "0" ]]; then
    echo "SPOOLMAN_RFID_UID_ALREADY_BOUND slot=${slot} spool_id=${spool_id} uid=${tag_uid_norm} other_spool_id=${uid_bound_elsewhere}"
  else
    if spool_detail_json_force="$(curl -fsS "${spoolman_url}/api/v1/spool/${spool_id}")"; then
      enroll_payload_or_prev="$(
        printf '%s' "$spool_detail_json_force" | jq -r --arg uid "$tag_uid_norm" '
          (.extra // {}) as $e
          | ({extra: ($e + {rfid_tag_uid: ($uid | @json)})} | tojson)
        ' 2>/dev/null || true
      )"
      if [[ -n "$enroll_payload_or_prev" && "$enroll_payload_or_prev" == \{* ]]; then
        prev_conflict_uid="$(
          printf '%s' "$spool_detail_json_force" | jq -r '
            (.extra // {}) as $e
            | ($e.rfid_tag_uid // "") as $raw
            | ($raw | if . == null then "" elif type == "string" then (try fromjson catch .) else tostring end)
          ' 2>/dev/null || true
        )"
        enroll_action="ENROLL"
        echo "SPOOLMAN_RFID_FORCE_ENROLL_SAFE slot=${slot} spool_id=${spool_id} prev=${prev_conflict_uid:-unknown} new=${tag_uid_norm} reason=uid_not_bound_elsewhere"
      fi
    fi
  fi
fi

if [[ "$enroll_action" == "ENROLL" ]]; then
  curl -fsS -X PATCH -H "Content-Type: application/json" \
    -d "${enroll_payload_or_prev}" \
    "${spoolman_url}/api/v1/spool/${spool_id}" >/dev/null
  echo "SPOOLMAN_RFID_ENROLLED slot=${slot} spool_id=${spool_id} tag_uid=${tag_uid_norm} prev_rfid_tag_uid=empty result=ok"
elif [[ "$enroll_action" == "UNCHANGED" ]]; then
  echo "SPOOLMAN_RFID_ENROLL_SKIP slot=${slot} spool_id=${spool_id} tag_uid=${tag_uid_norm} prev_rfid_tag_uid=${enroll_payload_or_prev} reason=already_set"
elif [[ "$enroll_action" == "CONFLICT" ]]; then
  echo "SPOOLMAN_RFID_RECONCILE_REQUIRED slot=${slot} spool_id=${spool_id} tag_uid=${tag_uid_norm} prev_rfid_tag_uid=${enroll_payload_or_prev} reason=conflicting_existing_value"
else
  echo "SPOOLMAN_RFID_ENROLL_SKIP slot=${slot} spool_id=${spool_id} tag_uid=${tag_uid_norm} reason=${enroll_payload_or_prev}"
fi

echo "SPOOLMAN_LOCATION_DEBUG_PATCH branch=$([[ -n "$tag_uid_norm" && "$tag_uid_norm" =~ ^[0-9A-F]+$ && "$enroll_action" != "CONFLICT" ]] && echo UID_AND_LOCATION || echo LOCATION_ONLY) spool_id=${spool_id:-unset} location=${location}"
printf '%s\n' "SPOOLMAN_LOCATION_DEBUG_PATCH branch=$([[ -n "$tag_uid_norm" && "$tag_uid_norm" =~ ^[0-9A-F]+$ && "$enroll_action" != "CONFLICT" ]] && echo UID_AND_LOCATION || echo LOCATION_ONLY) spool_id=${spool_id:-unset} location=${location}" >> "$log_file" || true
curl -fsS -X PATCH -H "Content-Type: application/json" \
  -d "$(
    if [[ -n "$tag_uid_norm" && "$tag_uid_norm" =~ ^[0-9A-F]+$ && "$enroll_action" != "CONFLICT" ]]; then
      jq -cn --arg loc "$location" --arg uid "$tag_uid_norm" '{location:$loc, extra:{rfid_tag_uid: ($uid | @json)}}'
    else
      jq -cn --arg loc "$location" '{location:$loc}'
    fi
  )" \
  "${spoolman_url}/api/v1/spool/${spool_id}" >/dev/null

echo "LOCATION_PATCH slot=${slot} spool_id=${spool_id} location=${location} reason=slot_binding"
echo "SPOOLMAN_LOCATION_SET spool_id=${spool_id} location=${location} slot=${slot} tray_uuid=${tray_uuid} tag_uid=${tag_uid} tray_hex=${tray_hex} tray_type=${tray_type}"
