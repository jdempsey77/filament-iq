#!/usr/bin/env bash
# Phase 0 baseline snapshot: AMS trays (HA API) + Spoolman spools with RFID (normalized via lib), duplicate report, New+RFID report.
# Writes under snapshots/phase0_baseline/<UTC timestamp>/.
# Required env: HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN, SPOOLMAN_URL.
# Exit: 1 = missing env, 2 = HA tray fetch failed, 3 = Spoolman fetch failed; 0 = success.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SNAPSHOTS_ROOT="${SNAPSHOTS_ROOT:-$REPO_ROOT/snapshots/phase0_baseline}"
LIB_DIR="$SCRIPT_DIR/lib"

# ---------- Required env
if [[ -z "${HOME_ASSISTANT_URL:-}" || -z "${HOME_ASSISTANT_TOKEN:-}" ]]; then
  echo "PHASE0_BASELINE_FAIL: HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN are required." >&2
  exit 1
fi
if [[ -z "${SPOOLMAN_URL:-}" ]]; then
  echo "PHASE0_BASELINE_FAIL: SPOOLMAN_URL is required." >&2
  exit 1
fi

HA_URL="${HOME_ASSISTANT_URL%/}"
HA_TOKEN="${HOME_ASSISTANT_TOKEN}"
SPOOLMAN="${SPOOLMAN_URL%/}"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="$SNAPSHOTS_ROOT/$TIMESTAMP"
mkdir -p "$OUT_DIR"

# ---------- A) AMS trays: GET /api/states/<entity_id> for slots 1-4. If ANY fetch fails → exit 2.
TRAY_ENTITIES=(
  "sensor.p1s_01p00c5a3101668_ams_1_tray_1:1"
  "sensor.p1s_01p00c5a3101668_ams_1_tray_2:2"
  "sensor.p1s_01p00c5a3101668_ams_1_tray_3:3"
  "sensor.p1s_01p00c5a3101668_ams_1_tray_4:4"
)

AMS_TRAYS_JSON="$OUT_DIR/ams_trays.json"
rm -f "$OUT_DIR/ams_trays_raw.txt"
for entry in "${TRAY_ENTITIES[@]}"; do
  entity_id="${entry%%:*}"
  slot="${entry##*:}"
  state_json="$(curl -fS -H "Authorization: Bearer $HA_TOKEN" "$HA_URL/api/states/$entity_id" 2>/dev/null)" || {
    echo "PHASE0_BASELINE_FAIL: HA tray fetch failed entity_id=$entity_id" >&2
    exit 2
  }
  if [[ -z "$state_json" || "$state_json" == "null" ]]; then
    echo "PHASE0_BASELINE_FAIL: HA tray fetch failed entity_id=$entity_id (empty or null)" >&2
    exit 2
  fi
  row="$(printf '%s' "$state_json" | jq -c --arg eid "$entity_id" --argjson slot "$slot" '
    .state as $state
    | (.attributes // {}) as $a
    | {
        slot: $slot,
        entity_id: $eid,
        state: ($state // "" | tostring),
        tag_uid: (($a.tag_uid // "") | tostring),
        tray_uuid: (($a.tray_uuid // "") | tostring),
        remain: (($a.remain // $a.remaining // "") | tostring),
        empty: (if $a.empty != null then $a.empty else null end),
        active: (if $a.active != null then $a.active else null end),
        filament_id: (($a.filament_id // "") | tostring),
        type: (($a.type // "") | tostring),
        color: (($a.color // "") | tostring)
      }
  ')"
  echo "$row" >> "$OUT_DIR/ams_trays_raw.txt"
done

# Build ams_trays.json from NDJSON: read raw lines (-R) so each line is a string, then fromjson parses it. Write to temp and mv on success so we do not leave empty file on jq failure.
AMS_TRAYS_TMP="$OUT_DIR/ams_trays.json.tmp.$$"
if [[ -f "$OUT_DIR/ams_trays_raw.txt" ]] && [[ -s "$OUT_DIR/ams_trays_raw.txt" ]]; then
  jq -R -n '[inputs | select(length > 0) | fromjson]' < "$OUT_DIR/ams_trays_raw.txt" > "$AMS_TRAYS_TMP" && mv "$AMS_TRAYS_TMP" "$AMS_TRAYS_JSON" || { rm -f "$AMS_TRAYS_TMP"; exit 5; }
else
  echo "[]" > "$AMS_TRAYS_JSON"
fi
rm -f "$OUT_DIR/ams_trays_raw.txt"

# ---------- B) Spoolman spools with RFID. Use lib only (no inline normalization). Fetch failure → exit 3.
SPOOLMAN_JSON="$OUT_DIR/spoolman_rfid_spools.json"
curl -fS --max-time 30 "$SPOOLMAN/api/v1/spool?limit=1000" -o "$OUT_DIR/spoolman_raw.json" 2>/dev/null || {
  echo "PHASE0_BASELINE_FAIL: Spoolman fetch failed." >&2
  exit 3
}

# Process with lib only (spools() + normalize_rfid_tag_uid); sort by normalized_rfid_tag_uid, id for deterministic output
jq -L "$LIB_DIR" -c '
  include "spoolman_normalize";
  spools
  | map(
      (.extra // {}) as $e
      | ($e.rfid_tag_uid // $e.rfid_uid // "") as $raw
      | ($raw | normalize_rfid_tag_uid) as $norm
      | select($norm != "" or ($raw | tostring) != "")
      | {
          id: (.id // .spool_id),
          location: (.location // ""),
          filament_name: ((.filament // {}).name // ""),
          vendor_name: ((.filament // {} | .vendor // {}).name // ""),
          normalized_rfid_tag_uid: $norm,
          raw_extra_rfid_tag_uid: ($raw | tostring)
        }
    )
  | sort_by(.normalized_rfid_tag_uid, .id)
' "$OUT_DIR/spoolman_raw.json" > "$SPOOLMAN_JSON"
rm -f "$OUT_DIR/spoolman_raw.json"

# ---------- C) Duplicate RFID report; sort by normalized_rfid_tag_uid
DUPLICATE_REPORT="$OUT_DIR/duplicate_rfid_report.json"
jq -s '
  .[0] as $list
  | ($list | group_by(.normalized_rfid_tag_uid) | map(select(length > 1 and (.[0].normalized_rfid_tag_uid != "")) | {normalized_rfid_tag_uid: .[0].normalized_rfid_tag_uid, spool_ids: (map(.id) | sort)}))
  | sort_by(.normalized_rfid_tag_uid)
' "$SPOOLMAN_JSON" > "$DUPLICATE_REPORT"

# ---------- D) Spools in location "New" with RFID; sort by normalized_rfid_tag_uid, id
NEW_WITH_RFID="$OUT_DIR/new_location_with_rfid.json"
jq -c '[.[] | select((.location | ascii_downcase) == "new" and .normalized_rfid_tag_uid != "")] | sort_by(.normalized_rfid_tag_uid, .id)' "$SPOOLMAN_JSON" > "$NEW_WITH_RFID"

# ---------- summary.txt: AMS trays captured, RFID spools count, Duplicate tag count, "New" with RFID count
TRAY_COUNT="$(jq 'length' "$AMS_TRAYS_JSON")"
SPOOL_RFID_COUNT="$(jq 'length' "$SPOOLMAN_JSON")"
DUPLICATE_COUNT="$(jq 'length' "$DUPLICATE_REPORT")"
NEW_RFID_COUNT="$(jq 'length' "$NEW_WITH_RFID")"
{
  echo "phase0_baseline_snapshot=$TIMESTAMP"
  echo "AMS trays captured=$TRAY_COUNT"
  echo "RFID spools count=$SPOOL_RFID_COUNT"
  echo "Duplicate tag count=$DUPLICATE_COUNT"
  echo "\"New\" with RFID count=$NEW_RFID_COUNT"
  echo "Snapshot path=$OUT_DIR"
} > "$OUT_DIR/summary.txt"

# Single line, machine-readable; absolute path
SNAPSHOT_DIR_ABS="$(cd "$OUT_DIR" && pwd)"
echo "SNAPSHOT_DIR=$SNAPSHOT_DIR_ABS"
