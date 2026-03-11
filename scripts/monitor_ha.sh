#!/usr/bin/env bash
# HA uptime monitor — polls HA API, detects outages, grabs logs on recovery.
# Runs indefinitely on Mac. Ctrl+C to stop.
#
# Requires: HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN in deploy.env / deploy.env.local
# SSH access for log retrieval (uses ssh_ha.sh conventions).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ARTIFACT_DIR="$REPO_ROOT/.artifacts/monitor_ha"

# ── Source deploy env ──────────────────────────────────────────────────
for f in "$SCRIPT_DIR/deploy.env.local" "$SCRIPT_DIR/deploy.env"; do
  if [[ -f "$f" ]]; then
    # shellcheck source=/dev/null
    set -a; source "$f"; set +a
  fi
done

HA_URL="${HOME_ASSISTANT_URL:-}"
HA_TOKEN="${HOME_ASSISTANT_TOKEN:-}"

if [[ -z "$HA_URL" || -z "$HA_TOKEN" ]]; then
  echo "MONITOR_HA: SKIP — HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN not set in deploy.env"
  exit 0
fi

HA_URL="${HA_URL%/}"

# SSH defaults (mirror ssh_ha.sh)
SSH_HOST="${SSH_HOST:-192.168.4.124}"
SSH_USER="${SSH_USER:-root}"
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"
read -ra _ssh_opts <<< "$SSH_OPTS"
if [[ "$SSH_OPTS" != *"-p"* && "$SSH_OPTS" != *"Port="* ]]; then
  _ssh_opts+=(-p 2222)
fi
if [[ "$SSH_OPTS" != *"-i"* && -f "$HOME/.ssh/id_ed25519_ha" ]]; then
  _ssh_opts+=(-i "$HOME/.ssh/id_ed25519_ha")
fi

mkdir -p "$ARTIFACT_DIR"

POLL_INTERVAL=30
OUTAGE_POLL_INTERVAL=10
OUTAGE_START=""
CONSECUTIVE_FAILS=0

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

poll_ha() {
  local result
  # Single curl: -s silent, -o discard body, -w write code+time, --connect-timeout 5
  result=$(curl -s -o /dev/null -w "%{http_code} %{time_total}" \
    --connect-timeout 5 --max-time 10 \
    -H "Authorization: Bearer $HA_TOKEN" \
    "$HA_URL/api/" 2>/dev/null) || result="000 0"
  echo "$result"
}

grab_recovery_logs() {
  local ts outage_log
  ts="$(date '+%Y%m%d_%H%M%S')"
  outage_log="$ARTIFACT_DIR/${ts}_outage.log"

  log "Grabbing HA logs via SSH..."
  {
    echo "# HA Outage Recovery Log"
    echo "# Outage start: $OUTAGE_START"
    echo "# Recovery:     $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "# Duration:     ${1:-unknown}"
    echo ""
    echo "=== Last 200 lines of home-assistant.log ==="
    ssh "${_ssh_opts[@]}" "$SSH_USER@$SSH_HOST" \
      "tail -200 /config/home-assistant.log" 2>/dev/null || echo "(SSH log fetch failed)"
    echo ""
    echo "=== Addon status ==="
    ssh "${_ssh_opts[@]}" "$SSH_USER@$SSH_HOST" \
      "ha addons info a0d7b954_appdaemon 2>/dev/null | head -20" 2>/dev/null || echo "(addon check failed)"
  } > "$outage_log"

  log "Recovery log saved: $outage_log"

  # macOS desktop notification
  if command -v osascript &>/dev/null; then
    osascript -e "display notification \"HA back online after ${1:-unknown}. Log saved.\" with title \"HA Monitor\" sound name \"Glass\"" 2>/dev/null || true
  fi
}

cleanup() {
  log "Monitor stopped (Ctrl+C)"
  exit 0
}
trap cleanup INT TERM

# ── Main loop ──────────────────────────────────────────────────────────
log "HA Monitor started — polling $HA_URL every ${POLL_INTERVAL}s"
log "Artifacts: $ARTIFACT_DIR"

while true; do
  read -r code rtime <<< "$(poll_ha)"

  if [[ "$code" == "200" ]]; then
    if [[ -n "$OUTAGE_START" ]]; then
      # Recovery detected
      outage_end=$(date '+%s')
      # macOS BSD date — use gdate on Linux
      outage_start_epoch=$(date -j -f "%Y-%m-%d %H:%M:%S" "$OUTAGE_START" '+%s' 2>/dev/null || echo "0")
      if [[ "$outage_start_epoch" -gt 0 ]]; then
        duration_s=$(( outage_end - outage_start_epoch ))
        duration_fmt="${duration_s}s"
        if [[ $duration_s -ge 60 ]]; then
          duration_fmt="$(( duration_s / 60 ))m$(( duration_s % 60 ))s"
        fi
      else
        duration_fmt="unknown"
      fi
      log "RECOVERY — HA back online after $duration_fmt (was down since $OUTAGE_START)"
      grab_recovery_logs "$duration_fmt"
      OUTAGE_START=""
      CONSECUTIVE_FAILS=0
    else
      log "OK — ${rtime}s response_time"
    fi
    sleep "$POLL_INTERVAL"
  else
    CONSECUTIVE_FAILS=$(( CONSECUTIVE_FAILS + 1 ))
    if [[ -z "$OUTAGE_START" ]]; then
      OUTAGE_START="$(date '+%Y-%m-%d %H:%M:%S')"
      log "OUTAGE DETECTED — HTTP $code at $OUTAGE_START"
      if command -v osascript &>/dev/null; then
        osascript -e "display notification \"HA unreachable (HTTP $code)\" with title \"HA Monitor\" sound name \"Basso\"" 2>/dev/null || true
      fi
    else
      log "STILL DOWN — HTTP $code (fail #$CONSECUTIVE_FAILS, since $OUTAGE_START)"
    fi
    sleep "$OUTAGE_POLL_INTERVAL"
  fi
done
