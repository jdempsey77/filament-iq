#!/usr/bin/env bash
# Phase 1 — Deterministic Match Resolution (READ-ONLY). No writes to Spoolman or HA.
# Requires: HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN, SPOOLMAN_URL.
# Output: snapshots/phase1_match/<UTC timestamp>/ with ams_trays.json, spoolman_rfid_spools.json, match_results.json, summary.txt.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SNAPSHOTS_ROOT="${SNAPSHOTS_ROOT:-$REPO_ROOT/snapshots/phase1_match}"
LIB_DIR="$SCRIPT_DIR/lib"

if [[ -z "${HOME_ASSISTANT_URL:-}" || -z "${HOME_ASSISTANT_TOKEN:-}" ]]; then
  echo "PHASE1_FAIL: HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN are required." >&2
  exit 1
fi
if [[ -z "${SPOOLMAN_URL:-}" ]]; then
  echo "PHASE1_FAIL: SPOOLMAN_URL is required." >&2
  exit 1
fi

HA_URL="${HOME_ASSISTANT_URL%/}"
HA_TOKEN="${HOME_ASSISTANT_TOKEN}"
SPOOLMAN="${SPOOLMAN_URL%/}"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="$SNAPSHOTS_ROOT/$TIMESTAMP"
mkdir -p "$OUT_DIR"

# ---------- AMS trays (same entities as Phase 0)
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
    echo "PHASE1_FAIL: HA tray fetch failed entity_id=$entity_id" >&2
    exit 2
  }
  if [[ -z "$state_json" || "$state_json" == "null" ]]; then
    echo "PHASE1_FAIL: HA tray fetch failed entity_id=$entity_id (empty or null)" >&2
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

AMS_TRAYS_TMP="$OUT_DIR/ams_trays.json.tmp.$$"
if [[ -f "$OUT_DIR/ams_trays_raw.txt" ]] && [[ -s "$OUT_DIR/ams_trays_raw.txt" ]]; then
  jq -R -n '[inputs | select(length > 0) | fromjson]' < "$OUT_DIR/ams_trays_raw.txt" > "$AMS_TRAYS_TMP" && mv "$AMS_TRAYS_TMP" "$AMS_TRAYS_JSON" || { rm -f "$AMS_TRAYS_TMP"; exit 22; }
else
  echo "[]" > "$AMS_TRAYS_JSON"
fi
rm -f "$OUT_DIR/ams_trays_raw.txt"

# ---------- Spoolman spools with RFID (reuse jq lib, same as Phase 0)
SPOOLMAN_JSON="$OUT_DIR/spoolman_rfid_spools.json"
curl -fS --max-time 30 "$SPOOLMAN/api/v1/spool?limit=1000" -o "$OUT_DIR/spoolman_raw.json" 2>/dev/null || {
  echo "PHASE1_FAIL: Spoolman fetch failed." >&2
  exit 3
}

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

# ---------- Match resolution per slot (read-only; tray_tag_norm via same jq lib)
MATCH_RESULTS="$OUT_DIR/match_results.json"
# Build array of 4 results (slots 1..4), each with resolved_spool_id, resolution_status, evidence
match_results="[]"
for slot in 1 2 3 4; do
  tray_tag="$(jq -r --argjson s "$slot" '.[] | select(.slot==$s) | .tag_uid // ""' "$AMS_TRAYS_JSON")"
  tray_tag_norm="$(printf '%s' "$tray_tag" | jq -R -L "$LIB_DIR" 'include "spoolman_normalize"; . | normalize_rfid_tag_uid' -r 2>/dev/null || echo "")"

  if [[ -z "$tray_tag_norm" ]]; then
    result="$(jq -n --argjson s "$slot" '{slot:$s, resolved_spool_id: null, resolution_status: "TAG_EMPTY", evidence: {candidates: [], reasons: ["tray tag normalizes to empty"]}}')"
  else
    matches_json="$(jq -c --arg t "$tray_tag_norm" '[.[] | select(.normalized_rfid_tag_uid == $t)]' "$SPOOLMAN_JSON")"
    count="$(echo "$matches_json" | jq 'length')"

    if [[ "$count" == "0" ]]; then
      result="$(jq -n --argjson s "$slot" --arg tag "$tray_tag_norm" '{slot:$s, resolved_spool_id: null, resolution_status: "NO_MATCH", evidence: {candidates: [], reasons: ["no spool with normalized tag "+$tag]}}')"
    elif [[ "$count" -gt 1 ]]; then
      candidates="$(echo "$matches_json" | jq -c '[.[] | {id: .id, location: .location}] | sort_by(.id)')"
      result="$(jq -n --argjson s "$slot" --argjson c "$candidates" '{slot:$s, resolved_spool_id: null, resolution_status: "AMBIGUOUS_DUPLICATES", evidence: {candidates: $c, reasons: ["multiple spools share same normalized tag"]}}')"
    else
      # exactly one match
      spool_id="$(echo "$matches_json" | jq -r '.[0].id')"
      location="$(echo "$matches_json" | jq -r '.[0].location')"
      loc_lower="$(echo "$location" | tr '[:upper:]' '[:lower:]')"
      if [[ "$loc_lower" == "new" ]]; then
        result="$(jq -n --argjson s "$slot" --argjson sid "$spool_id" '{slot:$s, resolved_spool_id: $sid, resolution_status: "SPOOL_IN_NEW", evidence: {candidates: [{id: $sid, location: "New"}], reasons: ["single match is in location New"]}}')"
      else
        result="$(jq -n --argjson s "$slot" --argjson sid "$spool_id" --arg loc "$location" '{slot:$s, resolved_spool_id: $sid, resolution_status: "RESOLVED_UNIQUE", evidence: {candidates: [{id: $sid, location: $loc}], reasons: ["unique match"]}}')"
      fi
    fi
  fi
  match_results="$(echo "$match_results" | jq -c --argjson r "$result" '. + [$r]')"
done

# Sort by slot and write
echo "$match_results" | jq -s 'add | sort_by(.slot)' > "$MATCH_RESULTS"

# ---------- summary.txt: counts per status + per-slot one-liners
status_counts="$(jq -r '[.[].resolution_status] | group_by(.) | map({status: .[0], count: length}) | .[] | "\(.status)=\(.count)"' "$MATCH_RESULTS" | sort)"
ambiguous_count="$(jq '[.[] | select(.resolution_status == "AMBIGUOUS_DUPLICATES")] | length' "$MATCH_RESULTS")"
in_new_count="$(jq '[.[] | select(.resolution_status == "SPOOL_IN_NEW")] | length' "$MATCH_RESULTS")"
{
  echo "phase1_match_snapshot=$TIMESTAMP"
  echo "Snapshot path=$OUT_DIR"
  for line in $status_counts; do echo "$line"; done
  echo "ambiguous_count=$ambiguous_count"
  echo "in_new_count=$in_new_count"
  echo "--- per-slot ---"
  jq -r '.[] | "slot=\(.slot) status=\(.resolution_status) resolved_spool_id=\(.resolved_spool_id // "null")"' "$MATCH_RESULTS"
} > "$OUT_DIR/summary.txt"

SNAPSHOT_DIR_ABS="$(cd "$OUT_DIR" && pwd)"
echo "SNAPSHOT_DIR=$SNAPSHOT_DIR_ABS"
