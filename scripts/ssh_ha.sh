#!/usr/bin/env bash
# SSH into Home Assistant (HA OS / supervised).
# Usage:
#   ./scripts/ssh_ha.sh                  # Interactive session
#   ./scripts/ssh_ha.sh "grep foo /config/automations.yaml"
#
# Uses deploy.env (SSH_HOST, SSH_USER, SSH_OPTS) if present.
# Defaults: root@192.168.4.124, port 2222, key ~/.ssh/id_ed25519_ha

set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"

# Defaults (HA OS SSH add-on)
SSH_HOST="${SSH_HOST:-192.168.4.124}"
SSH_USER="${SSH_USER:-root}"
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"

# Override from deploy.env if it exists
if [[ -f "$DEPLOY_ENV" ]]; then
  # shellcheck source=/dev/null
  source "$DEPLOY_ENV"
fi

# Build SSH_OPTS array for proper quoting of multi-word options
read -ra _ssh_opts <<< "$SSH_OPTS"

# Ensure port and key if not already in SSH_OPTS
if [[ "$SSH_OPTS" != *"-p"* && "$SSH_OPTS" != *"Port="* ]]; then
  _ssh_opts+=(-p 2222)
fi
if [[ "$SSH_OPTS" != *"-i"* && -f "$HOME/.ssh/id_ed25519_ha" ]]; then
  _ssh_opts+=(-i "$HOME/.ssh/id_ed25519_ha")
fi

exec ssh "${_ssh_opts[@]}" "$SSH_USER@$SSH_HOST" "$@"
