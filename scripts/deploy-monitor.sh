#!/usr/bin/env bash
# Deploy filament-iq-monitor to ska.
# Syncs monitor.py, service unit, and config. Enables and restarts the daemon.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MONITOR_DIR="$REPO_ROOT/monitor"
SKA_HOST="ska"

echo "=== Deploying filament-iq-monitor to $SKA_HOST ==="
echo ""

# Validate source files exist
for f in "$MONITOR_DIR/monitor.py" "$MONITOR_DIR/monitor.service" "$MONITOR_DIR/monitor-config.env"; do
  if [[ ! -f "$f" ]]; then
    echo "ERROR: Missing $f"
    exit 1
  fi
done

# Create remote directories
echo "Creating remote directories..."
ssh -n "$SKA_HOST" 'mkdir -p ~/filament_iq ~/.config/systemd/user ~/.config/filament_iq'

# Sync files
echo "Syncing monitor.py..."
rsync -avz "$MONITOR_DIR/monitor.py" "$SKA_HOST:~/filament_iq/monitor.py"

echo "Syncing monitor.service..."
rsync -avz "$MONITOR_DIR/monitor.service" "$SKA_HOST:~/.config/systemd/user/filament-iq-monitor.service"

echo "Syncing monitor-config.env..."
rsync -avz "$MONITOR_DIR/monitor-config.env" "$SKA_HOST:~/.config/filament_iq/monitor-config.env"

echo ""

# Reload, enable, restart
echo "Reloading systemd and restarting daemon..."
ssh -n "$SKA_HOST" bash -c "'
  systemctl --user daemon-reload
  systemctl --user enable filament-iq-monitor.service
  systemctl --user restart filament-iq-monitor.service
  sleep 2
  systemctl --user status filament-iq-monitor.service --no-pager || true
'"

echo ""
echo "=== Deploy complete ==="
echo "  Monitor: ~/filament_iq/monitor.py"
echo "  Service: ~/.config/systemd/user/filament-iq-monitor.service"
echo "  Config:  ~/.config/filament_iq/monitor-config.env"
echo "  Secrets: ~/.config/filament_iq/secrets.env (not deployed — use rotate-secret.sh)"
echo ""
echo "Logs:  journalctl --user -u filament-iq-monitor -f"
