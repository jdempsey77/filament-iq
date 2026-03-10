#!/usr/bin/env bash
# Monitor Agent: capture pre/post print snapshots, poll print state,
# tail AppDaemon logs, write structured artifact.
# Usage: ./scripts/monitor_print.sh
# Exit: 0 on clean capture; 1 on setup failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"

if [[ ! -f "$DEPLOY_ENV" ]]; then
  echo "MONITOR: ABORT — deploy.env not found at $DEPLOY_ENV"
  exit 1
fi
set -a
source "$DEPLOY_ENV"
set +a

# --- Validate required env vars ---
for var in HOME_ASSISTANT_URL HOME_ASSISTANT_TOKEN SPOOLMAN_URL PRINTER_PREFIX; do
  if [[ -z "${!var:-}" ]]; then
    echo "MONITOR: ABORT — $var not set in deploy.env"
    exit 1
  fi
done

# --- Config ---
POLL_INTERVAL=10

# --- Artifact directory ---
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
ARTIFACT_DIR="$REPO_ROOT/.artifacts/monitor/${TIMESTAMP}"
mkdir -p "$ARTIFACT_DIR"

LOG_FILE="$ARTIFACT_DIR/appd_log.txt"
ARTIFACT_JSON="$ARTIFACT_DIR/monitor.json"

echo "MONITOR: artifact dir = $ARTIFACT_DIR"

# --- Helper: HA state fetch ---
ha_state() {
  local entity="$1"
  local tmp
  tmp="$(mktemp)"
  local http
  http="$(curl -s -o "$tmp" -w "%{http_code}" \
    -H "Authorization: Bearer ${HOME_ASSISTANT_TOKEN}" \
    "${HOME_ASSISTANT_URL}/api/states/${entity}" || echo "000")"
  if [[ "$http" == "200" ]]; then
    python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('state',''))" "$tmp"
  else
    echo "unavailable"
  fi
  rm -f "$tmp"
}

# --- Helper: Spoolman spool weights ---
spoolman_weights() {
  local tmp
  tmp="$(mktemp)"
  local http
  http="$(curl -s -o "$tmp" -w "%{http_code}" \
    -H "Accept: application/json" \
    "${SPOOLMAN_URL}/api/v1/spool" || echo "000")"
  if [[ "$http" == "200" ]]; then
    python3 - <<'PY' "$tmp"
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    result = {}
    for s in data:
        sid = s.get("id")
        rw = s.get("remaining_weight")
        loc = s.get("location", "") or ""
        if sid is not None:
            result[str(sid)] = {"remaining_weight": rw, "location": loc}
    print(json.dumps(result))
except Exception:
    print("{}")
PY
  else
    echo "{}"
  fi
  rm -f "$tmp"
}

# --- Helper: tray states for all 6 slots ---
tray_states() {
  local entities=(
    "sensor.${PRINTER_PREFIX}_ams_1_tray_1"
    "sensor.${PRINTER_PREFIX}_ams_1_tray_2"
    "sensor.${PRINTER_PREFIX}_ams_1_tray_3"
    "sensor.${PRINTER_PREFIX}_ams_1_tray_4"
    "sensor.${PRINTER_PREFIX}_ams_128_tray_1"
    "sensor.${PRINTER_PREFIX}_ams_129_tray_1"
  )
  local result="{"
  local first=1
  for entity in "${entities[@]}"; do
    local state
    state="$(ha_state "$entity")"
    if [[ $first -eq 1 ]]; then
      first=0
    else
      result+=","
    fi
    result+="\"$entity\":\"$state\""
  done
  result+="}"
  echo "$result"
}

# --- Helper: print status ---
print_status() {
  ha_state "sensor.${PRINTER_PREFIX}_print_status"
}

# --- SSH: tail AppDaemon log ---
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519_ha}"
HA_HOST="${HA_HOST:-root@192.168.4.124}"
HA_SSH_PORT="${HA_SSH_PORT:-2222}"
APPD_LOG="/addon_configs/a0d7b954_appdaemon/logs/appdaemon.log"

start_log_tail() {
  ssh -i "$SSH_KEY" -p "$HA_SSH_PORT" -o StrictHostKeyChecking=no \
    "$HA_HOST" "tail -f '$APPD_LOG'" > "$LOG_FILE" 2>/dev/null &
  LOG_TAIL_PID=$!
  echo "MONITOR: tailing AppDaemon log (PID=$LOG_TAIL_PID)"
}

stop_log_tail() {
  if [[ -n "${LOG_TAIL_PID:-}" ]] && kill -0 "$LOG_TAIL_PID" 2>/dev/null; then
    kill "$LOG_TAIL_PID" 2>/dev/null || true
    wait "$LOG_TAIL_PID" 2>/dev/null || true
  fi
}

trap stop_log_tail EXIT

# --- Pre-print snapshot ---
echo "MONITOR: capturing pre-print snapshot..."
PRE_TRAYS="$(tray_states)"
PRE_WEIGHTS="$(spoolman_weights)"
PRE_STATUS="$(print_status)"
PRE_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "MONITOR: pre-print status=$PRE_STATUS"

# --- Smart timeout: estimated remaining time + 2h buffer ---
_remaining_h="$(ha_state "sensor.${PRINTER_PREFIX}_remaining_time")"
if [[ "$_remaining_h" =~ ^[0-9.]+$ ]]; then
  _remaining_min="$(python3 -c "import math; print(math.ceil(float('${_remaining_h}') * 60))")"
  TIMEOUT_MINUTES=$(( _remaining_min + 120 ))
  if [[ $TIMEOUT_MINUTES -lt 60 ]]; then
    TIMEOUT_MINUTES=60
  fi
  echo "MONITOR: printer estimates ${_remaining_h}h remaining — timeout set to ${TIMEOUT_MINUTES}m"
else
  TIMEOUT_MINUTES=1080
  echo "MONITOR: remaining_time unavailable — using ${TIMEOUT_MINUTES}m fallback timeout"
fi
TIMEOUT_SECONDS=$((TIMEOUT_MINUTES * 60))

# --- Start log tail ---
start_log_tail

# --- Poll loop ---
echo "MONITOR: polling every ${POLL_INTERVAL}s (timeout ${TIMEOUT_MINUTES}m)..."
ELAPSED=0
LAST_STATUS="$PRE_STATUS"
POLL_LOG="[]"

while [[ $ELAPSED -lt $TIMEOUT_SECONDS ]]; do
  sleep "$POLL_INTERVAL"
  ELAPSED=$((ELAPSED + POLL_INTERVAL))

  STATUS="$(print_status)"

  if [[ "$STATUS" != "$LAST_STATUS" ]]; then
    echo "MONITOR: status change $LAST_STATUS -> $STATUS (${ELAPSED}s elapsed)"
    LAST_STATUS="$STATUS"
  fi

  # Record poll entry
  POLL_ENTRY="{\"t\":$ELAPSED,\"status\":\"$STATUS\"}"
  if [[ "$POLL_LOG" == "[]" ]]; then
    POLL_LOG="[$POLL_ENTRY"
  else
    POLL_LOG+=",$POLL_ENTRY"
  fi

  # Check for terminal states
  case "$STATUS" in
    finish|failed|idle|offline|unknown|unavailable)
      echo "MONITOR: terminal state '$STATUS' at ${ELAPSED}s — ending capture"
      break
      ;;
  esac
done

POLL_LOG+="]"

if [[ $ELAPSED -ge $TIMEOUT_SECONDS ]]; then
  echo "MONITOR: timeout after ${TIMEOUT_MINUTES}m"
fi

# --- Post-print snapshot ---
echo "MONITOR: capturing post-print snapshot..."
# Small delay for Spoolman writes to settle
sleep 5
POST_TRAYS="$(tray_states)"
POST_WEIGHTS="$(spoolman_weights)"
POST_STATUS="$(print_status)"
POST_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# --- Stop log tail ---
stop_log_tail

# --- Write artifact ---
python3 - <<PY "$ARTIFACT_JSON" "$PRE_TIME" "$POST_TIME" "$PRE_STATUS" "$POST_STATUS" \
  "$PRE_TRAYS" "$POST_TRAYS" "$PRE_WEIGHTS" "$POST_WEIGHTS" "$POLL_LOG" "$LOG_FILE"
import json, sys

out_path = sys.argv[1]
pre_time = sys.argv[2]
post_time = sys.argv[3]
pre_status = sys.argv[4]
post_status = sys.argv[5]
pre_trays = json.loads(sys.argv[6])
post_trays = json.loads(sys.argv[7])
pre_weights = json.loads(sys.argv[8])
post_weights = json.loads(sys.argv[9])
poll_log = json.loads(sys.argv[10])
log_file = sys.argv[11]

# Read captured log lines
try:
    with open(log_file) as f:
        log_lines = f.read().splitlines()
except Exception:
    log_lines = []

artifact = {
    "monitor_version": "1.0",
    "pre_snapshot": {
        "timestamp": pre_time,
        "print_status": pre_status,
        "tray_states": pre_trays,
        "spoolman_weights": pre_weights,
    },
    "post_snapshot": {
        "timestamp": post_time,
        "print_status": post_status,
        "tray_states": post_trays,
        "spoolman_weights": post_weights,
    },
    "poll_log": poll_log,
    "appd_log_lines": len(log_lines),
    "appd_log_file": log_file,
}

with open(out_path, "w") as f:
    json.dump(artifact, f, indent=2)

print(f"MONITOR: artifact written to {out_path}")
print(f"MONITOR: {len(log_lines)} AppDaemon log lines captured")
PY

echo "MONITOR: COMPLETE — $ARTIFACT_JSON"
