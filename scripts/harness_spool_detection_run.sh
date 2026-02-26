#!/usr/bin/env bash
# Spool detection harness: run baseline -> insert spool -> after -> eval.
# Usage: ./scripts/harness_spool_detection_run.sh --slot <n> --mode <rfid|nonrfid> --label <string> [--wait-seconds N]
# Creates artifacts/harness_spool_detection/<YYYYMMDD_HHMMSS>_<label>/ with baseline.json, after.json, runs eval, prints PASS/FAIL.

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

while [[ $# -gt 0 ]]; do
  case "$1" in
    --slot) SLOT="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --wait-seconds) WAIT_SECONDS="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$SLOT" || -z "$MODE" || -z "$LABEL" ]]; then
  echo "Usage: $0 --slot <n> --mode <rfid|nonrfid> --label <string> [--wait-seconds N]" >&2
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
echo "Waiting ${WAIT_SECONDS}s..."
sleep "$WAIT_SECONDS"

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
