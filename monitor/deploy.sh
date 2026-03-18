#!/bin/bash
# Deploy monitor.py to remote machine
# Usage: MONITOR_HOST=ska bash monitor/deploy.sh
set -e
MONITOR_HOST="${MONITOR_HOST:-ska}"
MONITOR_DIR="${MONITOR_DIR:-~/filament_iq}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Deploying monitor.py to $MONITOR_HOST:$MONITOR_DIR/monitor.py"
ssh "$MONITOR_HOST" "mkdir -p $MONITOR_DIR"
scp "$SCRIPT_DIR/monitor.py" "$MONITOR_HOST:$MONITOR_DIR/monitor.py"
ssh "$MONITOR_HOST" "systemctl --user restart filament-iq-monitor && \
  sleep 2 && systemctl --user status filament-iq-monitor --no-pager"
echo "Deploy complete."
