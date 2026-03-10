#!/usr/bin/env bash
# Preflight: Spoolman filament dropdown — verify Spoolman filament API
# reachable and HA dropdown entity exists.
# Exit: 0 if both checks pass; 1 on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"
if [[ ! -f "$DEPLOY_ENV" ]]; then
  echo "PREFLIGHT_SPOOLMAN_FILAMENT_DROPDOWN: SKIP (deploy.env not found)"
  exit 0
fi
set -a
source "$DEPLOY_ENV"
set +a

if [[ -z "${SPOOLMAN_URL:-}" ]]; then
  echo "PREFLIGHT_SPOOLMAN_FILAMENT_DROPDOWN: SKIP (SPOOLMAN_URL not set)"
  exit 0
fi

if [[ -z "${HOME_ASSISTANT_URL:-}" || -z "${HOME_ASSISTANT_TOKEN:-}" ]]; then
  echo "PREFLIGHT_SPOOLMAN_FILAMENT_DROPDOWN: SKIP (HOME_ASSISTANT_URL/TOKEN not set)"
  exit 0
fi

ERRORS=0

# --- Check 1: Spoolman GET /api/v1/filament ---
filament_resp="$(mktemp)"
trap 'rm -f "$filament_resp"' EXIT

filament_http="$(
  curl -s -o "$filament_resp" -w "%{http_code}" \
    -H "Accept: application/json" \
    "${SPOOLMAN_URL}/api/v1/filament" || echo "000"
)"

if [[ "$filament_http" != "200" ]]; then
  echo "PREFLIGHT_SPOOLMAN_FILAMENT_DROPDOWN: FAIL — Spoolman /api/v1/filament HTTP $filament_http"
  exit 1
fi

filament_count="$(
  python3 - <<'PY' "$filament_resp"
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    if isinstance(data, list):
        print(len(data))
    else:
        print("0")
except Exception:
    print("-1")
PY
)"

if [[ "$filament_count" == "-1" ]]; then
  echo "PREFLIGHT_SPOOLMAN_FILAMENT_DROPDOWN: FAIL — Spoolman /api/v1/filament invalid JSON"
  exit 1
fi

if [[ "$filament_count" == "0" ]]; then
  echo "PREFLIGHT_SPOOLMAN_FILAMENT_DROPDOWN: FAIL — Spoolman /api/v1/filament returned empty array"
  exit 1
fi

# --- Check 2: HA dropdown entity exists and is not unavailable ---
dropdown_entity="input_select.spoolman_new_spool_filament"
dropdown_http="$(
  curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer ${HOME_ASSISTANT_TOKEN}" \
    "${HOME_ASSISTANT_URL}/api/states/${dropdown_entity}" || echo "000"
)"

if [[ "$dropdown_http" != "200" ]]; then
  echo "PREFLIGHT_SPOOLMAN_FILAMENT_DROPDOWN: FAIL — HA entity $dropdown_entity not found (HTTP $dropdown_http)"
  exit 1
fi

echo "PREFLIGHT_SPOOLMAN_FILAMENT_DROPDOWN: PASS (${filament_count} filaments, dropdown entity exists)"
exit 0
