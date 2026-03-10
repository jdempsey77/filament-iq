#!/usr/bin/env bash
# Preflight: AMS matching — verify all 6 tray sensor entities are reachable
# in HA and Spoolman spool API is accessible.
# Exit: 0 if all checks pass; 1 on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"
if [[ ! -f "$DEPLOY_ENV" ]]; then
  echo "PREFLIGHT_AMS_MATCHING: SKIP (deploy.env not found)"
  exit 0
fi
set -a
source "$DEPLOY_ENV"
set +a

if [[ -z "${HOME_ASSISTANT_URL:-}" || -z "${HOME_ASSISTANT_TOKEN:-}" ]]; then
  echo "PREFLIGHT_AMS_MATCHING: SKIP (HOME_ASSISTANT_URL/TOKEN not set)"
  exit 0
fi

if [[ -z "${SPOOLMAN_URL:-}" ]]; then
  echo "PREFLIGHT_AMS_MATCHING: SKIP (SPOOLMAN_URL not set)"
  exit 0
fi

if [[ -z "${PRINTER_PREFIX:-}" ]]; then
  echo "PREFLIGHT_AMS_MATCHING: SKIP (PRINTER_PREFIX not set)"
  exit 0
fi

ERRORS=0
TRAY_ENTITIES=(
  "sensor.${PRINTER_PREFIX}_ams_1_tray_1"
  "sensor.${PRINTER_PREFIX}_ams_1_tray_2"
  "sensor.${PRINTER_PREFIX}_ams_1_tray_3"
  "sensor.${PRINTER_PREFIX}_ams_1_tray_4"
  "sensor.${PRINTER_PREFIX}_ams_128_tray_1"
  "sensor.${PRINTER_PREFIX}_ams_129_tray_1"
)

# --- Check 1: Each tray sensor entity reachable and not unavailable/unknown ---
state_tmp="$(mktemp)"
trap 'rm -f "$state_tmp"' EXIT

OK_COUNT=0
for entity in "${TRAY_ENTITIES[@]}"; do
  entity_http="$(
    curl -s -o "$state_tmp" -w "%{http_code}" \
      -H "Authorization: Bearer ${HOME_ASSISTANT_TOKEN}" \
      "${HOME_ASSISTANT_URL}/api/states/${entity}" || echo "000"
  )"

  if [[ "$entity_http" != "200" ]]; then
    echo "  FAIL: $entity — HTTP $entity_http (entity not found)"
    ERRORS=$((ERRORS + 1))
    continue
  fi

  state="$(
    python3 - <<'PY' "$state_tmp"
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    print(data.get("state", ""))
except Exception:
    print("")
PY
  )"

  if [[ "$state" == "unavailable" || "$state" == "unknown" ]]; then
    echo "  FAIL: $entity — state=$state (printer may be off or disconnected)"
    ERRORS=$((ERRORS + 1))
  else
    OK_COUNT=$((OK_COUNT + 1))
  fi
done

# --- Check 2: Spoolman GET /api/v1/spool reachable ---
spool_http="$(
  curl -s -o /dev/null -w "%{http_code}" \
    -H "Accept: application/json" \
    "${SPOOLMAN_URL}/api/v1/spool" || echo "000"
)"

if [[ "$spool_http" != "200" ]]; then
  echo "  FAIL: Spoolman /api/v1/spool — HTTP $spool_http"
  ERRORS=$((ERRORS + 1))
fi

# --- Result ---
if [[ $ERRORS -gt 0 ]]; then
  echo "PREFLIGHT_AMS_MATCHING: FAIL — $ERRORS error(s), $OK_COUNT/6 tray entities OK"
  exit 1
fi

echo "PREFLIGHT_AMS_MATCHING: PASS (6/6 tray entities valid, Spoolman spool API reachable)"
exit 0
