#!/usr/bin/env bash
# Gate: E2E lot_nr — enrolled AMS slots have Spoolman spool.lot_nr populated.
# For slots 1–6: if tray non-empty and helper spool_id set, fetch Spoolman spool and assert lot_nr is non-empty.
# SKIP when deploy.env or HA/Spoolman not configured (exit 0). PASS only when all enrolled spools have lot_nr.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"
GATE_NAME="gate_lot_nr_e2e"
TS=$(date +%Y%m%d_%H%M%S)
ARTIFACT_DIR="${GATE_ARTIFACT_DIR:-$REPO_ROOT/.artifacts/skill/gates/$GATE_NAME-$TS}"
mkdir -p "$ARTIFACT_DIR"

checklist_pass=0
checklist_fail=0
log() { echo "$*" | tee -a "$ARTIFACT_DIR/checklist.txt"; }
log_fail() { echo "  FAIL: $*" | tee -a "$ARTIFACT_DIR/checklist.txt"; checklist_fail=$(( checklist_fail + 1 )); }
log_ok() { echo "  PASS: $*" | tee -a "$ARTIFACT_DIR/checklist.txt"; checklist_pass=$(( checklist_pass + 1 )); }

if [[ ! -f "$DEPLOY_ENV" ]]; then
  log "GATE_LOT_NR_E2E: SKIP (deploy.env not found)"
  exit 0
fi
set -a; source "$DEPLOY_ENV"; set +a
if [[ -z "${HOME_ASSISTANT_URL:-}" || -z "${HOME_ASSISTANT_TOKEN:-}" || -z "${SPOOLMAN_URL:-}" ]]; then
  log "GATE_LOT_NR_E2E: SKIP (HOME_ASSISTANT_URL/TOKEN or SPOOLMAN_URL not set)"
  exit 0
fi

AUTH="Authorization: Bearer $HOME_ASSISTANT_TOKEN"
PRINTER="p1s_01p00c5a3101668"

tray_entity() {
  case "$1" in
    1) echo "sensor.${PRINTER}_ams_1_tray_1" ;;
    2) echo "sensor.${PRINTER}_ams_1_tray_2" ;;
    3) echo "sensor.${PRINTER}_ams_1_tray_3" ;;
    4) echo "sensor.${PRINTER}_ams_1_tray_4" ;;
    5) echo "sensor.${PRINTER}_ams_128_tray_1" ;;
    6) echo "sensor.${PRINTER}_ams_129_tray_1" ;;
    *) return 1 ;;
  esac
}

ha() {
  curl -sS -H "$AUTH" "$HOME_ASSISTANT_URL/api/states/$1"
}

spool_http() {
  local id="$1"
  local tmp; tmp="$(mktemp)"
  local http
  http="$(curl -sS -w "%{http_code}" -o "$tmp" "$SPOOLMAN_URL/api/v1/spool/$id" 2>/dev/null || echo "000")"
  echo "$http $tmp"
}

log "=== $GATE_NAME ==="
log "Artifact dir: $ARTIFACT_DIR"

for slot in 1 2 3 4 5 6; do
  tray="$(tray_entity "$slot")"
  helper_entity="input_text.ams_slot_${slot}_spool_id"
  tray_json="$(ha "$tray" 2>/dev/null)" || true
  helper_json="$(ha "$helper_entity" 2>/dev/null)" || true

  empty="$(echo "$tray_json" | jq -r '.attributes.empty // "true"' 2>/dev/null)"
  helper="$(echo "$helper_json" | jq -r '.state | tonumber? // 0' 2>/dev/null)"

  if [[ "$empty" = "true" ]]; then
    log_ok "slot $slot tray empty (skip)"
    continue
  fi
  if [[ "${helper:-0}" -le 0 ]]; then
    log_ok "slot $slot no helper (skip)"
    continue
  fi

  read -r http tmp < <(spool_http "$helper")
  if [[ "$http" != "200" ]]; then
    rm -f "$tmp"
    log_fail "slot $slot spool_id=$helper HTTP $http"
    continue
  fi
  lot_nr="$(jq -r '.lot_nr // ""' "$tmp" 2>/dev/null | tr -d '"')"
  rm -f "$tmp"

  if [[ -z "$lot_nr" ]]; then
    log_fail "slot $slot spool_id=$helper lot_nr empty"
  else
    log_ok "slot $slot spool_id=$helper lot_nr set"
  fi
done

log ""
log "CHECKLIST: $checklist_pass pass, $checklist_fail fail"
log "ARTIFACTS: $ARTIFACT_DIR"

if [[ $checklist_fail -gt 0 ]]; then
  echo "GATE_LOT_NR_E2E: FAIL"
  exit 1
fi
echo "GATE_LOT_NR_E2E: PASS"
exit 0
