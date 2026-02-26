#!/usr/bin/env bash
# Spool detection harness: run baseline -> insert spool -> after -> eval.
# Usage: ./scripts/harness_spool_detection_run.sh --slot <n> --mode <rfid|nonrfid> --label <string> [--wait-seconds N] [--poll-seconds N]
# Creates artifacts/harness_spool_detection/<YYYYMMDD_HHMMSS>_<label>/ with baseline.json, after.json, runs eval, prints PASS/FAIL.
# RFID mode: poll-based wait for detection (empty==false or tag_uid or tray_uuid). Non-RFID: fixed sleep.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ARTIFACT_ROOT="${ARTIFACT_ROOT:-$REPO_ROOT/artifacts/harness_spool_detection}"

for f in "$SCRIPT_DIR/deploy.env.local" "$SCRIPT_DIR/deploy.env"; do
  if [[ -f "$f" ]]; then
    set -a; source "$f"; set +a
  fi
done

SLOT=""
MODE=""
LABEL=""
WAIT_SECONDS="${HARNESS_WAIT_SECONDS:-20}"
POLL_SECONDS="${HARNESS_POLL_SECONDS:-2}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --slot) SLOT="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --wait-seconds) WAIT_SECONDS="$2"; shift 2 ;;
    --poll-seconds) POLL_SECONDS="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$SLOT" || -z "$MODE" || -z "$LABEL" ]]; then
  echo "Usage: $0 --slot <n> --mode <rfid|nonrfid> --label <string> [--wait-seconds N] [--poll-seconds N]" >&2
  exit 1
fi

if [[ "$MODE" != "rfid" && "$MODE" != "nonrfid" ]]; then
  echo "Mode must be rfid or nonrfid" >&2
  exit 1
fi

if [[ ! "$SLOT" =~ ^[1-6]$ ]]; then
  echo "Slot must be 1-6" >&2
  exit 1
fi

# Tray entity mapping (same as capture.sh / ams_rfid_reconcile.py)
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

# Poll HA for tray state; return 0 if RFID detected (empty==false or tag_uid or tray_uuid non-empty).
# Output to stdout: empty, name, tag_uid, tray_uuid (one per line) when detected; nothing otherwise.
check_rfid_detected() {
  local entity="$1"
  local url="${HOME_ASSISTANT_URL:-}/api/states/${entity}"
  local resp
  resp="$(curl -sS -H "Authorization: Bearer ${HOME_ASSISTANT_TOKEN:-}" "$url" 2>/dev/null)" || true
  if [[ -z "$resp" ]] || [[ "${resp:0:1}" != "{" ]]; then
    return 1
  fi
  local empty name tag_uid tray_uuid
  empty="$(echo "$resp" | jq -r '.attributes.empty // true' 2>/dev/null)" || true
  name="$(echo "$resp" | jq -r '.attributes.name // .state // ""' 2>/dev/null)" || true
  tag_uid="$(echo "$resp" | jq -r '.attributes.tag_uid // .attributes.tag_uid_hex // ""' 2>/dev/null)" || true
  tray_uuid="$(echo "$resp" | jq -r '.attributes.tray_uuid // .attributes.tray_id // ""' 2>/dev/null)" || true
  if [[ "$empty" == "false" ]] || [[ -n "${tag_uid// }" ]] || [[ -n "${tray_uuid// }" ]]; then
    echo "empty=$empty"
    echo "name=$name"
    echo "tag_uid=$tag_uid"
    echo "tray_uuid=$tray_uuid"
    return 0
  fi
  return 1
}

# Wait for RFID detection up to max_sec, polling every poll_sec. Print progress. Return 0 when detected, 1 on timeout.
wait_for_rfid() {
  local slot="$1"
  local max_sec="$2"
  local poll_sec="$3"
  local entity
  entity="$(get_tray_entity "$slot")"
  local elapsed=0
  local detected
  while [[ "$elapsed" -lt "$max_sec" ]]; do
    if detected="$(check_rfid_detected "$entity")"; then
      echo "RFID detected (elapsed ${elapsed}s):"
      echo "$detected" | while IFS= read -r line; do echo "  $line"; done
      return 0
    fi
    echo "Waiting for RFID detection... (elapsed ${elapsed}s / max ${max_sec}s)"
    sleep "$poll_sec"
    elapsed=$((elapsed + poll_sec))
  done
  echo "Waiting for RFID detection... (elapsed ${elapsed}s / max ${max_sec}s)"
  return 1
}

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${ARTIFACT_ROOT}/${STAMP}_${LABEL}"
mkdir -p "$OUT_DIR"
BASELINE="${OUT_DIR}/baseline.json"
AFTER="${OUT_DIR}/after.json"

echo "Output directory: $OUT_DIR"
echo "Capturing baseline..."
"$SCRIPT_DIR/harness_spool_detection_capture.sh" --slot "$SLOT" --out "$BASELINE"

echo ""
echo "Now insert spool into slot $SLOT and wait for stabilization..."

if [[ "$MODE" == "rfid" ]]; then
  if ! wait_for_rfid "$SLOT" "$WAIT_SECONDS" "$POLL_SECONDS"; then
    echo "RFID not detected within ${WAIT_SECONDS}s; capturing after snapshot anyway."
  fi
else
  echo "Waiting ${WAIT_SECONDS}s..."
  sleep "$WAIT_SECONDS"
fi

echo "Capturing after..."
"$SCRIPT_DIR/harness_spool_detection_capture.sh" --slot "$SLOT" --out "$AFTER"

echo ""
echo "Evaluating (mode=$MODE)..."
if python3 "$SCRIPT_DIR/harness_spool_detection_eval.py" "$BASELINE" "$AFTER" "$MODE"; then
  echo ""
  echo "PASS"
  exit 0
else
  echo ""
  echo "FAIL"
  exit 1
fi
