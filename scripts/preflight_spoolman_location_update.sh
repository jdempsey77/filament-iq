#!/usr/bin/env bash
# Preflight: Spoolman location update — verify PATCH /api/v1/spool/{id}
# endpoint accepts location updates. Uses idempotent read-write-back
# (reads current location, PATCHes same value back).
# Exit: 0 if pass; 1 on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"
if [[ ! -f "$DEPLOY_ENV" ]]; then
  echo "PREFLIGHT_SPOOLMAN_LOCATION_UPDATE: SKIP (deploy.env not found)"
  exit 0
fi
set -a
source "$DEPLOY_ENV"
set +a

if [[ -z "${SPOOLMAN_URL:-}" ]]; then
  echo "PREFLIGHT_SPOOLMAN_LOCATION_UPDATE: SKIP (SPOOLMAN_URL not set)"
  exit 0
fi

spool_resp="$(mktemp)"
patch_resp="$(mktemp)"
trap 'rm -f "$spool_resp" "$patch_resp"' EXIT

# --- Step 1: GET first spool from Spoolman to use as test target ---
list_http="$(
  curl -s -o "$spool_resp" -w "%{http_code}" \
    -H "Accept: application/json" \
    "${SPOOLMAN_URL}/api/v1/spool?limit=1" || echo "000"
)"

if [[ "$list_http" != "200" ]]; then
  echo "PREFLIGHT_SPOOLMAN_LOCATION_UPDATE: FAIL — GET /api/v1/spool HTTP $list_http"
  exit 1
fi

# Extract first spool's id and location
read -r spool_id current_location <<< "$(
  python3 - <<'PY' "$spool_resp"
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    if isinstance(data, list) and len(data) > 0:
        s = data[0]
        sid = s.get("id", "")
        loc = s.get("location", "") or ""
        print(f"{sid} {loc}")
    else:
        print("")
except Exception:
    print("")
PY
)"

if [[ -z "$spool_id" ]]; then
  echo "PREFLIGHT_SPOOLMAN_LOCATION_UPDATE: FAIL — no spools found in Spoolman"
  exit 1
fi

# --- Step 2: PATCH location back to same value (idempotent, no data change) ---
patch_http="$(
  curl -s -o "$patch_resp" -w "%{http_code}" -X PATCH \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -d "{\"location\": \"${current_location}\"}" \
    "${SPOOLMAN_URL}/api/v1/spool/${spool_id}" || echo "000"
)"

if [[ "$patch_http" != "200" ]]; then
  echo "PREFLIGHT_SPOOLMAN_LOCATION_UPDATE: FAIL — PATCH /api/v1/spool/${spool_id} HTTP $patch_http"
  exit 1
fi

# --- Step 3: Verify response contains expected location ---
patched_location="$(
  python3 - <<'PY' "$patch_resp"
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    print(data.get("location", "") or "")
except Exception:
    print("")
PY
)"

if [[ "$patched_location" != "$current_location" ]]; then
  echo "PREFLIGHT_SPOOLMAN_LOCATION_UPDATE: FAIL — PATCH response location='$patched_location' != expected='$current_location'"
  exit 1
fi

echo "PREFLIGHT_SPOOLMAN_LOCATION_UPDATE: PASS (spool_id=$spool_id, location='$current_location' write-back verified)"
exit 0
