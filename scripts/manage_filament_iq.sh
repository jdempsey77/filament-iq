#!/usr/bin/env bash
# manage_filament_iq.sh — FilamentIQ deploy script
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"

if [[ ! -f "$DEPLOY_ENV" ]]; then
  echo "Error: $DEPLOY_ENV not found." >&2; exit 1
fi
set -a; source "$DEPLOY_ENV"; set +a

APPDAEMON_ADDON_SLUG="${APPDAEMON_ADDON_SLUG:-a0d7b954_appdaemon}"
REMOTE_CONFIG_PATH="${REMOTE_CONFIG_PATH:-/config}"
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"
SSH_PORT="${HA_SSH_PORT:-22}"
SSH_KEY="${HA_SSH_KEY:-}"
_SSH_ARGS() { echo "$SSH_OPTS ${SSH_KEY:+-i $SSH_KEY} -p $SSH_PORT"; }
_SCP_ARGS() { echo "$SSH_OPTS ${SSH_KEY:+-i $SSH_KEY} -P $SSH_PORT"; }

_resolve_ssh_target() {
  if [[ -n "$HA_SSH_HOST" ]]; then echo "root@$HA_SSH_HOST"
  elif [[ -n "$SSH_HOST" ]]; then echo "root@$SSH_HOST"
  elif [[ -n "$HOME_ASSISTANT_URL" ]]; then
    local host; host=$(echo "$HOME_ASSISTANT_URL" | sed 's|https\?://||' | sed 's|:.*||')
    echo "root@$host"
  else
    echo "Error: No SSH host configured." >&2; exit 1
  fi
}

_dirty_tree_guard() {
  local porcelain; porcelain=$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)
  if [[ -n "$porcelain" ]]; then
    echo "Error: Refusing to deploy with a dirty working tree."
    echo "$porcelain"; exit 1
  fi
}

_ha_post() {
  curl -s -o /dev/null -w "%{http_code}" -X POST \
    -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" \
    -H "Content-Type: application/json" \
    -d "${2:-{}}" "$HOME_ASSISTANT_URL/api/$1"
}

_ha_get_code() {
  curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" \
    "$HOME_ASSISTANT_URL/api/$1"
}

cmd_appdaemon() {
  _dirty_tree_guard
  local target; target=$(_resolve_ssh_target)
  local remote_path="/addon_configs/${APPDAEMON_ADDON_SLUG}/apps/filament_iq"
  echo "=== Deploying FilamentIQ AppDaemon apps ==="
  echo "Target: $target:$remote_path"
  ssh $(_SSH_ARGS) "$target" "mkdir -p $remote_path"
  scp $(_SCP_ARGS) -r "$REPO_ROOT/appdaemon/apps/filament_iq/." "$target:$remote_path/"
  echo "Files deployed."
  echo "Restarting AppDaemon ($APPDAEMON_ADDON_SLUG)..."
  ssh $(_SSH_ARGS) "$target" "ha apps restart '$APPDAEMON_ADDON_SLUG'"
  echo "AppDaemon restarted."
  echo "=== Deploy complete ==="
}

cmd_appdaemon_restart() {
  local target; target=$(_resolve_ssh_target)
  echo "Restarting AppDaemon ($APPDAEMON_ADDON_SLUG)..."
  ssh $(_SSH_ARGS) "$target" "ha apps restart '$APPDAEMON_ADDON_SLUG'"
  echo "Done."
}

cmd_ha_config() {
  _dirty_tree_guard
  local target; target=$(_resolve_ssh_target)
  local remote_pkg="$REMOTE_CONFIG_PATH/packages/filament_iq.yaml"
  echo "=== Deploying FilamentIQ HA package ==="
  ssh $(_SSH_ARGS) "$target" "mkdir -p $REMOTE_CONFIG_PATH/packages"
  scp $(_SCP_ARGS) "$REPO_ROOT/ha-config/packages/filament_iq.yaml" "$target:$remote_pkg"
  echo "Package deployed to $remote_pkg"
  local code; code=$(_ha_post "services/homeassistant/reload_core_config")
  [[ "$code" == "200" || "$code" == "204" ]] && echo "Core config reloaded." || echo "Warning: HTTP $code"
  echo ""
  echo "REMINDER: Ensure configuration.yaml includes:"
  echo "  homeassistant:"
  echo "    packages: !include_dir_named packages"
  echo "=== Deploy complete ==="
}

cmd_validate() {
  echo "Validating HA configuration..."
  local resp; resp=$(curl -s -X POST \
    -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" \
    -H "Content-Type: application/json" \
    "$HOME_ASSISTANT_URL/api/config/core/check_config")
  local result; result=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('result','unknown'))" 2>/dev/null || echo "unknown")
  if [[ "$result" == "valid" ]]; then echo "VALID"
  else echo "INVALID — $resp"; exit 1; fi
}

cmd_check() {
  local target; target=$(_resolve_ssh_target)
  local remote_path="/addon_configs/${APPDAEMON_ADDON_SLUG}/apps/filament_iq"
  echo "=== Checking deployed FilamentIQ files ==="
  for local_file in $(find "$REPO_ROOT/appdaemon/apps/filament_iq" -name "*.py" | sort); do
    local filename; filename=$(basename "$local_file")
    local local_md5; local_md5=$(md5 -q "$local_file" 2>/dev/null || md5sum "$local_file" | awk '{print $1}')
    local remote_md5; remote_md5=$(ssh $(_SSH_ARGS) "$target" "md5sum $remote_path/$filename 2>/dev/null | awk '{print \$1}'" 2>/dev/null || echo "missing")
    if [[ "$local_md5" == "$remote_md5" ]]; then echo "  SAME       $filename"
    elif [[ "$remote_md5" == "missing" ]]; then echo "  MISSING    $filename"
    else echo "  DIFFERENT  $filename"; fi
  done
  echo "=== Check complete ==="
}

cmd_all() { cmd_appdaemon; cmd_ha_config; }

cmd_restart() {
  echo "Restarting Home Assistant..."
  _ha_post "services/homeassistant/restart" > /dev/null
  echo "Waiting for HA (max 180s)..."
  local elapsed=0
  while [[ $elapsed -lt 180 ]]; do
    sleep 5; elapsed=$(( elapsed + 5 ))
    local code; code=$(_ha_get_code "config" 2>/dev/null || echo "000")
    if [[ "$code" == "200" ]]; then echo "HA is back up (${elapsed}s)."; return 0; fi
    echo "  ...waiting (${elapsed}s)"
  done
  echo "Error: HA did not come back up within 180s." >&2; exit 1
}

cmd_help() {
  cat << 'EOF'
manage_filament_iq.sh — FilamentIQ deploy script

  --appdaemon         Deploy AppDaemon apps and restart AppDaemon
  --appdaemon-restart Restart AppDaemon only (no file copy)
  --ha-config         Deploy ha-config/packages/filament_iq.yaml
  --validate          Validate HA config
  --check             Diff local vs deployed AppDaemon files
  --all               Run --appdaemon then --ha-config
  --restart           Restart HA and wait for it to come back up
  --help              Show this help
EOF
}

case "${1:-}" in
  --appdaemon)         cmd_appdaemon ;;
  --appdaemon-restart) cmd_appdaemon_restart ;;
  --ha-config)         cmd_ha_config ;;
  --validate)          cmd_validate ;;
  --check)             cmd_check ;;
  --all)               cmd_all ;;
  --restart)           cmd_restart ;;
  --help)              cmd_help ;;
  *) echo "Unknown flag: ${1:-}. Run --help for usage."; exit 1 ;;
esac
