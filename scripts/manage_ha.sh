#!/usr/bin/env bash
# Manage Home Assistant: deploy, validate, restart, check
# Usage: ./scripts/manage_ha.sh [--stage|--promote|--check|--config|--automations|--scripts|--go2rtc|--all|--restart|--validate|--appdaemon|--appdaemon-restart|--restart-all]
#
#   --check       Compare local stage vs HA
#   --config      Deploy configuration.yaml and included files (scripts.yaml, scenes.yaml)
#   --automations Deploy automations.yaml
#   --scripts     Deploy scripts.yaml only
#   --go2rtc      Deploy go2rtc.yaml (streams for WebRTC/cameras)
#   --all         Deploy all config (config + automations + go2rtc) and restart HA
#   --spoolman-export  Export Spoolman inventory to spools.csv (keep repo in sync)
#   --spoolman-import  Import spools.csv into Spoolman (new spools only)
#   --spoolman-update Push remaining_g/empty_spool_g from CSV to Spoolman (run export first to get spool_id)
#   --stage       Deploy stage dashboard (ui-lovelace-stage.yaml), no restart. Workflow: stage → test → copy from repo to prod.
#   --promote     Optional: copy stage YAML to ui-lovelace.yaml in HA (alternative to copying from repo)
#   --restart     Restart HA (use with --config, or alone)
#   --validate    Validate config via HA API (use with --config, or alone to check current)
#   --appdaemon   Deploy AppDaemon apps (appdaemon/apps -> /addon_configs/<slug>/apps) and restart addon
#   --appdaemon-restart  Restart AppDaemon addon only
#   --restart-all Restart HA core and AppDaemon addon
#
# Requires: deploy.env (copy from deploy.env.example and fill in values)
# Optional: HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN for --validate, --restart

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"
STAGE_FILE="$REPO_ROOT/dashboards/dashboard.stage.yaml"
CONFIG_FILE="$REPO_ROOT/configuration.yaml"
AUTOMATIONS_FILE="$REPO_ROOT/automations.yaml"
SCRIPTS_FILE="$REPO_ROOT/scripts.yaml"
SCENES_FILE="$REPO_ROOT/scenes.yaml"
GO2RTC_FILE="$REPO_ROOT/go2rtc.yaml"

# Safe SSH/SCP wrappers: convert string SSH_OPTS to properly-quoted array arguments
_do_ssh() {
  local -a opts
  read -ra opts <<< "${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"
  ssh "${opts[@]}" "$@"
}
_do_scp() {
  local -a opts
  read -ra opts <<< "${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"
  scp "${opts[@]}" "$@"
}

# Parse args
TARGET="${1:-}"
if [[ -z "$TARGET" || "$TARGET" == "--help" || "$TARGET" == "-h" ]]; then
  echo "Usage: $0 [--stage|--stage-restart|--prod-prep|--promote|--check|--config|--automations|--scripts|--go2rtc|--all|--spoolman-export|--spoolman-import|--spoolman-update|--restart|--validate|--appdaemon|--appdaemon-restart|--restart-all]"
  echo ""
  echo "  --check       Compare local stage vs HA"
  echo "  --config      Deploy configuration.yaml and included files (scripts.yaml, scenes.yaml)"
  echo "  --automations Deploy automations.yaml"
  echo "  --scripts     Deploy scripts.yaml only"
  echo "  --python_scripts  Deploy python_scripts/ to HA config (no restart)"
  echo "  --go2rtc      Deploy go2rtc.yaml (streams for WebRTC/cameras)"
  echo "  --all         Deploy all config (config + automations + go2rtc) and restart HA"
  echo "  --spoolman-export  Export Spoolman inventory to spools.csv (keep repo in sync)"
  echo "  --spoolman-import  Import spools.csv into Spoolman (new spools only)"
  echo "  --spoolman-update Push remaining_g/empty_spool_g from CSV to Spoolman (run export first to get spool_id)"
  echo "  --stage       Deploy stage dashboard (no restart; refresh browser to see changes)"
  echo "  --stage-restart  Deploy stage dashboard and restart HA"
  echo "  --prod-prep   Generate dashboard.prod.yaml from stage (for manual copy to HA main dashboard)"
  echo "  --promote     Optional: copy stage to ui-lovelace.yaml in HA"
  echo "  --restart     Restart HA"
  echo "  --validate    Validate config via HA API"
  echo "  --appdaemon   Deploy AppDaemon apps (appdaemon/apps -> /addon_configs/<slug>/apps) and restart addon"
  echo "  --appdaemon-restart  Restart AppDaemon addon only"
  echo "  --restart-all Restart HA core and AppDaemon addon"
  exit 0
fi
RESTART_AFTER=0
VALIDATE_AFTER=0
for arg in "$@"; do
  [[ "$arg" == "--restart" ]] && RESTART_AFTER=1
  [[ "$arg" == "--validate" ]] && VALIDATE_AFTER=1
done

# Deploy guard: refuse deploy/restart if working tree is dirty (staged or unstaged)
DEPLOY_FLAGS="--config --automations --scripts --python_scripts --go2rtc --all --appdaemon --stage --stage-restart --promote --restart --restart-all --appdaemon-restart"
if [[ " $DEPLOY_FLAGS " == *" $TARGET "* ]]; then
  status_out=$(cd "$REPO_ROOT" && git status --porcelain 2>/dev/null)
  if [[ -n "$status_out" ]]; then
    echo "Error: Refusing to run deploy/restart with a dirty working tree." >&2
    echo "" >&2
    echo "git status --porcelain:" >&2
    echo "$status_out" >&2
    echo "" >&2
    echo "Commit or stash your changes, then run again." >&2
    exit 1
  fi
fi

do_validate() {
  if [[ -z "$HOME_ASSISTANT_URL" || -z "$HOME_ASSISTANT_TOKEN" ]]; then
    echo "Skipping validate (set HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN in deploy.env)."
    return 1
  fi
  echo "Validating configuration..."
  local url="${HOME_ASSISTANT_URL}/api/config/core/check_config"
  local resp
  resp=$(curl -s -w "\n%{http_code}" -X POST \
    -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{}' \
    "$url")
  local status
  status=$(echo "$resp" | tail -n1)
  resp=$(echo "$resp" | sed '$d')
  if [[ -z "$resp" ]]; then
    echo "Empty response from HA (HTTP $status). Check:"
    echo "  - HOME_ASSISTANT_URL in deploy.env (e.g. http://192.168.4.124:8123)"
    echo "  - HOME_ASSISTANT_TOKEN is valid"
    echo "  - HA is reachable from this machine"
    return 1
  fi
  if echo "$resp" | grep -qE '"result"\s*:\s*"valid"'; then
    echo "Configuration is VALID."
    return 0
  else
    echo "Configuration is INVALID. Raw API response:"
    echo "$resp"
    return 1
  fi
}

do_restart() {
  if [[ -n "$HOME_ASSISTANT_URL" && -n "$HOME_ASSISTANT_TOKEN" ]]; then
    echo "Restarting Home Assistant..."
    curl -s -X POST \
      -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{}' \
      "$HOME_ASSISTANT_URL/api/services/homeassistant/restart"
    echo ""
    echo "Restart initiated. HA may take a minute to come back."
  else
    echo "Skipping restart (set HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN in deploy.env)."
  fi
}

do_reload_automations() {
  if [[ -n "$HOME_ASSISTANT_URL" && -n "$HOME_ASSISTANT_TOKEN" ]]; then
    echo "Reloading automations via HA API..."
    local resp
    resp=$(curl -s -w "\n%{http_code}" -X POST \
      -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{}' \
      "${HOME_ASSISTANT_URL}/api/services/automation/reload")
    local status
    status=$(echo "$resp" | tail -n1)
    if [[ "$status" == "200" ]]; then
      echo "Automations reloaded successfully."
    else
      echo "Reload returned HTTP $status. Check HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN in deploy.env."
    fi
  else
    echo "Skipping auto-reload (set HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN in deploy.env)."
    echo "Reload automations manually: Developer Tools > YAML > Automations > Reload"
  fi
}

# Wait for HA to respond after restart (three phases):
#   Phase A:  /api/config HTTP 200               (HA_WAIT_SECONDS, default 180s)
#   Phase B1: input_text service in /api/services (HA_WAIT_INPUT_TEXT_SECONDS, default 180s)
#   Phase B2: validate_helpers.sh --json ready    (HA_WAIT_HELPERS_SECONDS, default 420s)
# B2 delegates readiness to validate_helpers.sh to guarantee a single source of truth.
wait_for_ha() {
  if [[ -z "$HOME_ASSISTANT_URL" || -z "$HOME_ASSISTANT_TOKEN" ]]; then
    return
  fi
  local sleep_sec="${HA_WAIT_SLEEP:-3}"
  local start_ts end_ts elapsed code

  # Phase A: poll /api/config until 200 or timeout (uses HA_WAIT_SECONDS for backward compat)
  local phase_a_sec="${HA_WAIT_SECONDS:-180}"
  echo "Waiting for Home Assistant (Phase A: /api/config, timeout ${phase_a_sec}s)..."
  start_ts=$(date +%s)
  while true; do
    code=$(curl -sS -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" "$HOME_ASSISTANT_URL/api/config" 2>/dev/null || echo "000")
    if [[ "$code" == "200" ]]; then
      end_ts=$(date +%s); elapsed=$(( end_ts - start_ts ))
      echo "Phase A: /api/config returned 200 after ${elapsed}s."
      break
    fi
    end_ts=$(date +%s); elapsed=$(( end_ts - start_ts ))
    if [[ $elapsed -ge $phase_a_sec ]]; then
      echo "WARN: Phase A timeout (${phase_a_sec}s); continuing anyway." >&2
      return
    fi
    echo "  ... waiting for /api/config (${elapsed}s)"
    sleep "$sleep_sec"
  done

  # -------------------------------------------------------------------
  # Phase B1: wait for input_text domain + set_value in /api/services
  # -------------------------------------------------------------------
  local b1_sec="${HA_WAIT_INPUT_TEXT_SECONDS:-180}"
  echo "Waiting for Home Assistant (Phase B1: input_text service, timeout ${b1_sec}s)..."
  start_ts=$(date +%s)
  while true; do
    local services_json has_domain
    services_json=$(curl -sS -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" "$HOME_ASSISTANT_URL/api/services" 2>/dev/null) || true
    if command -v jq >/dev/null 2>&1 && [[ -n "$services_json" ]]; then
      has_domain=$(echo "$services_json" | jq -r '
        if type == "array" then
          ([.[] | select(.domain == "input_text") | .services | has("set_value")] | any)
        elif type == "object" then
          (has("input_text") and (.["input_text"] | type == "object") and (.["input_text"] | has("set_value")))
        else false end
      ' 2>/dev/null || echo "false")
      if [[ "$has_domain" == "true" ]]; then
        end_ts=$(date +%s); elapsed=$(( end_ts - start_ts ))
        echo "Phase B1: input_text.set_value present after ${elapsed}s."
        break
      fi
    fi
    end_ts=$(date +%s); elapsed=$(( end_ts - start_ts ))
    if [[ $elapsed -ge $b1_sec ]]; then
      if [[ "${HA_ALLOW_PARTIAL_STARTUP:-0}" == "1" ]]; then
        echo "WARN: Phase B1 timeout (${b1_sec}s); input_text service missing. Continuing (HA_ALLOW_PARTIAL_STARTUP=1)." >&2
        return
      fi
      echo "ERROR: Phase B1 timeout (${b1_sec}s); input_text service still missing." >&2
      echo "  input_text.set_value not registered; helpers will be zombies." >&2
      echo "  Fix: deploy config and restart, or set HA_ALLOW_PARTIAL_STARTUP=1." >&2
      return 1
    fi
    echo "  ... waiting for input_text service (${elapsed}s)"
    sleep "$sleep_sec"
  done

  # -------------------------------------------------------------------
  # Phase B2: delegate to validate_helpers.sh --json (single source of truth)
  # -------------------------------------------------------------------
  local b2_sec="${HA_WAIT_HELPERS_SECONDS:-420}"
  local vh_script="$REPO_ROOT/scripts/validate_helpers.sh"
  if [[ ! -x "$vh_script" ]]; then
    echo "WARN: validate_helpers.sh not found or not executable; skipping Phase B2." >&2
    return
  fi

  echo "Waiting for Home Assistant (Phase B2: validate_helpers.sh --json, timeout ${b2_sec}s)..."
  start_ts=$(date +%s)

  while true; do
    local vh_json vh_rc
    vh_json=$("$vh_script" --json 2>/dev/null) && vh_rc=0 || vh_rc=$?

    end_ts=$(date +%s); elapsed=$(( end_ts - start_ts ))

    if [[ $vh_rc -eq 0 ]]; then
      local _ok _req
      _ok=$(echo "$vh_json" | jq -r '.ok // 0' 2>/dev/null || echo "0")
      _req=$(echo "$vh_json" | jq -r '.required // 0' 2>/dev/null || echo "0")
      echo "Phase B2: validate_helpers PASS (ok=${_ok} required=${_req}) after ${elapsed}s. HA is ready."
      return
    fi

    # Not ready yet — extract fields for status line
    local _ok _req _z _m _z_ids _m_ids
    if command -v jq >/dev/null 2>&1 && [[ -n "$vh_json" ]]; then
      _ok=$(echo "$vh_json" | jq -r '.ok // 0' 2>/dev/null || echo "?")
      _req=$(echo "$vh_json" | jq -r '.required // 0' 2>/dev/null || echo "?")
      _z=$(echo "$vh_json" | jq -r '.zombies // 0' 2>/dev/null || echo "?")
      _m=$(echo "$vh_json" | jq -r '.missing // 0' 2>/dev/null || echo "?")
      _z_ids=$(echo "$vh_json" | jq -r '.zombie_ids[:3] | join(", ")' 2>/dev/null || echo "")
      _m_ids=$(echo "$vh_json" | jq -r '.missing_ids[:3] | join(", ")' 2>/dev/null || echo "")
    else
      _ok="?" _req="?" _z="?" _m="?" _z_ids="" _m_ids=""
    fi

    # Timeout check
    if [[ $elapsed -ge $b2_sec ]]; then
      echo "ERROR: Phase B2 timeout (${b2_sec}s). validate_helpers not ready." >&2
      echo "  ok=${_ok} required=${_req} zombies=${_z} missing=${_m}" >&2
      [[ -n "$_z_ids" ]] && echo "  zombie_ids: ${_z_ids}" >&2
      [[ -n "$_m_ids" ]] && echo "  missing_ids: ${_m_ids}" >&2
      if [[ "${HA_ALLOW_PARTIAL_STARTUP:-0}" == "1" ]]; then
        echo "  Continuing (HA_ALLOW_PARTIAL_STARTUP=1)." >&2
        return
      fi
      return 1
    fi

    # Progress line
    local _detail=""
    [[ -n "$_z_ids" ]] && _detail=" z=[${_z_ids}]"
    [[ -n "$_m_ids" ]] && _detail="${_detail} m=[${_m_ids}]"
    echo "  Phase B2: ok=${_ok} required=${_req} zombies=${_z} missing=${_m}${_detail} (${elapsed}s)"
    sleep "$sleep_sec"
  done
}

# Deploy gate: run helpers validation (after reload/restart). Exit non-zero on failure.
do_validate_helpers() {
  if [[ -z "$HOME_ASSISTANT_URL" || -z "$HOME_ASSISTANT_TOKEN" ]]; then
    echo "Skipping helpers validation (set HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN in deploy.env)."
    return 0
  fi
  (cd "$REPO_ROOT" && ./scripts/validate_helpers.sh) || exit 1
}

# Resolve SSH target for AppDaemon: HA_SSH_HOST if set (else derive from HOME_ASSISTANT_URL), default root@host
_resolve_appdaemon_ssh_target() {
  if [[ -n "${HA_SSH_HOST:-}" ]]; then
    if [[ "$HA_SSH_HOST" == *"@"* ]]; then
      AD_SSH_TARGET="$HA_SSH_HOST"
    else
      AD_SSH_TARGET="root@${HA_SSH_HOST}"
    fi
  else
    local url="${HOME_ASSISTANT_URL:-}"
    url="${url#*://}"
    url="${url%%/*}"
    url="${url%:*}"
    [[ -z "$url" ]] && url="localhost"
    AD_SSH_TARGET="root@${url}"
  fi
}

deploy_appdaemon() {
  local apps_dir="$REPO_ROOT/appdaemon/apps"
  if [[ ! -d "$apps_dir" ]]; then
    echo "Error: $apps_dir not found." >&2
    return 1
  fi
  local slug="${APPDAEMON_ADDON_SLUG:-a0d7b954_appdaemon}"
  local remote_dir="/addon_configs/${slug}/apps"
  if ! _do_ssh "$AD_SSH_TARGET" "mkdir -p $remote_dir/filament_iq"; then
    echo "Error: could not create remote dir $remote_dir on host." >&2
    return 1
  fi
  # Deploy only: apps.yaml config + filament_iq/ package (source of truth)
  echo "Deploying apps.yaml to $AD_SSH_TARGET:$remote_dir/"
  if ! _do_scp "$apps_dir/apps.yaml" "$AD_SSH_TARGET:$remote_dir/apps.yaml"; then
    echo "Error: scp apps.yaml failed." >&2
    return 1
  fi
  echo "Deploying filament_iq/ package to $AD_SSH_TARGET:$remote_dir/"
  if ! _do_scp -r "$apps_dir/filament_iq" "$AD_SSH_TARGET:$remote_dir/"; then
    echo "Error: scp filament_iq/ failed." >&2
    return 1
  fi
  # Verify package landed
  if ! _do_ssh "$AD_SSH_TARGET" "test -f $remote_dir/filament_iq/__init__.py"; then
    echo "Error: filament_iq/__init__.py missing on remote after deploy." >&2
    return 1
  fi
  if ! _do_ssh "$AD_SSH_TARGET" "ha apps restart '${slug}'"; then
    echo "Error: addon restart failed." >&2
    return 1
  fi
  echo "Command completed successfully."
  wait_for_appdaemon || return 1
  echo "AppDaemon apps deployed and addon restarted."
  return 0
}

restart_appdaemon() {
  local slug="${APPDAEMON_ADDON_SLUG:-a0d7b954_appdaemon}"
  if ! _do_ssh "$AD_SSH_TARGET" "ha apps restart '${slug}'"; then
    echo "Error: AppDaemon addon restart failed." >&2
    return 1
  fi
  wait_for_appdaemon || return 1
  echo "AppDaemon addon restarted."
  return 0
}

wait_for_appdaemon() {
  local slug="${APPDAEMON_ADDON_SLUG:-a0d7b954_appdaemon}"
  local max_attempts=30
  local attempt=0
  local status

  echo "Waiting for AppDaemon addon to start..."
  while [[ $attempt -lt $max_attempts ]]; do
    status=$(_do_ssh "$AD_SSH_TARGET" \
      "ha addons info '${slug}' --raw-json 2>/dev/null" 2>/dev/null \
      | grep -o '"state":"[^"]*"' | head -1 | cut -d'"' -f4) || true

    if [[ "$status" == "started" ]]; then
      echo "AppDaemon started (attempt $((attempt+1))/${max_attempts})."
      return 0
    fi

    if [[ "$status" == "error" || "$status" == "failed" || "$status" == "unknown" ]]; then
      echo "AppDaemon failed to start (state=${status})." >&2
      echo "Check logs: ssh $AD_SSH_TARGET 'tail -50 /addon_configs/${slug}/logs/appdaemon.log'" >&2
      return 1
    fi

    attempt=$((attempt+1))
    sleep 2
  done

  echo "AppDaemon did not reach 'started' within 60s (last state=${status:-unknown})." >&2
  return 1
}

restart_ha_core_then_restart_appdaemon() {
  do_restart
  wait_for_ha
  restart_appdaemon
}

# Handle --validate alone (validate config currently on HA)
if [[ "$TARGET" == "--validate" ]]; then
  if [[ -f "$DEPLOY_ENV" ]]; then
    set -a
    source "$DEPLOY_ENV"
    set +a
  fi
  do_validate || exit 1
  exit 0
fi

# Handle --restart alone (just restart, no deploy)
if [[ "$TARGET" == "--restart" ]]; then
  if [[ -f "$DEPLOY_ENV" ]]; then
    set -a
    source "$DEPLOY_ENV"
    set +a
  fi
  do_restart
  wait_for_ha
  do_validate_helpers
  exit 0
fi

# Handle --appdaemon (deploy apps + restart addon)
if [[ "$TARGET" == "--appdaemon" ]]; then
  if [[ ! -f "$DEPLOY_ENV" ]]; then
    echo "Error: $DEPLOY_ENV not found." >&2
    exit 1
  fi
  set -a
  source "$DEPLOY_ENV"
  set +a
  APPDAEMON_ADDON_SLUG="${APPDAEMON_ADDON_SLUG:-a0d7b954_appdaemon}"
  SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"
  _resolve_appdaemon_ssh_target
  deploy_appdaemon || exit 1
  exit 0
fi

# Handle --appdaemon-restart (restart addon only)
if [[ "$TARGET" == "--appdaemon-restart" ]]; then
  if [[ ! -f "$DEPLOY_ENV" ]]; then
    echo "Error: $DEPLOY_ENV not found." >&2
    exit 1
  fi
  set -a
  source "$DEPLOY_ENV"
  set +a
  APPDAEMON_ADDON_SLUG="${APPDAEMON_ADDON_SLUG:-a0d7b954_appdaemon}"
  SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"
  _resolve_appdaemon_ssh_target
  restart_appdaemon || exit 1
  exit 0
fi

# Handle --restart-all (HA core + AppDaemon addon)
if [[ "$TARGET" == "--restart-all" ]]; then
  if [[ ! -f "$DEPLOY_ENV" ]]; then
    echo "Error: $DEPLOY_ENV not found." >&2
    exit 1
  fi
  set -a
  source "$DEPLOY_ENV"
  set +a
  APPDAEMON_ADDON_SLUG="${APPDAEMON_ADDON_SLUG:-a0d7b954_appdaemon}"
  SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"
  _resolve_appdaemon_ssh_target
  restart_ha_core_then_restart_appdaemon || exit 1
  exit 0
fi

# Handle --check (local stage vs HA)
if [[ "$TARGET" == "--check" ]]; then
  if [[ ! -f "$STAGE_FILE" ]]; then
    echo "Error: stage file missing."
    exit 1
  fi
  EXIT_CODE=0

  if [[ -f "$DEPLOY_ENV" ]]; then
    set -a
    source "$DEPLOY_ENV"
    set +a
    if [[ -n "$SSH_HOST" && -n "$SSH_USER" ]]; then
      SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"
      REMOTE_CONFIG="${REMOTE_CONFIG_PATH:-/config}"
      REMOTE_CONFIG="${REMOTE_CONFIG%/}"
      TMP_DIR=$(mktemp -d 2>/dev/null || mktemp -d -t manage-ha-check)
      trap "rm -rf $TMP_DIR" EXIT

      echo "=== Local stage vs HA stage (ui-lovelace-stage.yaml) ==="
      echo "Remote path: $SSH_USER@$SSH_HOST:${REMOTE_CONFIG}/ui-lovelace-stage.yaml"
      if _do_scp -q "$SSH_USER@$SSH_HOST:${REMOTE_CONFIG}/ui-lovelace-stage.yaml" "$TMP_DIR/ha-stage.yaml" 2>/dev/null; then
        if diff -q "$STAGE_FILE" "$TMP_DIR/ha-stage.yaml" > /dev/null 2>&1; then
          echo "SAME"
        else
          echo "DIFFERENT"
          EXIT_CODE=1
        fi
      else
        echo "Could not fetch (SSH failed or file missing)"
      fi

      echo ""
      echo "To view Stage: sidebar → dashboard picker → choose 'Stage'. URL must contain 'lovelace-stage'."
      echo "(Main dashboard loads from storage; not compared)"
    fi
  else
    echo "Add deploy.env to compare against HA"
  fi

  exit $EXIT_CODE
fi

# Handle --config (needs deploy.env)
# Note: RESTART_AFTER is set by the arg loop above
if [[ "$TARGET" == "--config" ]]; then
  if [[ ! -f "$DEPLOY_ENV" ]]; then
    echo "Error: $DEPLOY_ENV not found."
    exit 1
  fi
  set -a
  source "$DEPLOY_ENV"
  set +a
  : "${SSH_HOST:?Set SSH_HOST in deploy.env}"
  : "${SSH_USER:?Set SSH_USER in deploy.env}"
  : "${REMOTE_CONFIG_PATH:?Set REMOTE_CONFIG_PATH in deploy.env}"
  SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"
  REMOTE_PATH="${REMOTE_CONFIG_PATH%/}/configuration.yaml"
  echo "Deploying configuration.yaml to $SSH_USER@$SSH_HOST:$REMOTE_PATH"
  _do_scp "$CONFIG_FILE" "$SSH_USER@$SSH_HOST:$REMOTE_PATH"
  if [[ -f "$SCRIPTS_FILE" ]]; then
    echo "Deploying scripts.yaml to $SSH_USER@$SSH_HOST:${REMOTE_CONFIG_PATH%/}/scripts.yaml"
    _do_scp "$SCRIPTS_FILE" "$SSH_USER@$SSH_HOST:${REMOTE_CONFIG_PATH%/}/scripts.yaml"
  fi
  if [[ -f "$SCENES_FILE" ]]; then
    echo "Deploying scenes.yaml to $SSH_USER@$SSH_HOST:${REMOTE_CONFIG_PATH%/}/scenes.yaml"
    _do_scp "$SCENES_FILE" "$SSH_USER@$SSH_HOST:${REMOTE_CONFIG_PATH%/}/scenes.yaml"
  fi
  if [[ -f "$REPO_ROOT/secrets.yaml" ]]; then
    echo "Deploying secrets.yaml to $SSH_USER@$SSH_HOST:${REMOTE_CONFIG_PATH%/}/secrets.yaml"
    _do_scp "$REPO_ROOT/secrets.yaml" "$SSH_USER@$SSH_HOST:${REMOTE_CONFIG_PATH%/}/secrets.yaml"
  fi
  echo "Done."
  if [[ "$VALIDATE_AFTER" -eq 1 ]]; then
    do_validate || exit 1
  fi
  if [[ "$RESTART_AFTER" -eq 1 ]]; then
    do_restart
    wait_for_ha
    do_validate_helpers
  elif [[ "$VALIDATE_AFTER" -eq 0 ]]; then
    echo "Restart Home Assistant for changes to take effect (or use --restart)."
  fi
  exit 0
fi

# Handle --automations (needs deploy.env)
if [[ "$TARGET" == "--automations" ]]; then
  if [[ ! -f "$DEPLOY_ENV" ]]; then
    echo "Error: $DEPLOY_ENV not found."
    exit 1
  fi
  set -a
  source "$DEPLOY_ENV"
  set +a
  : "${SSH_HOST:?Set SSH_HOST in deploy.env}"
  : "${SSH_USER:?Set SSH_USER in deploy.env}"
  : "${REMOTE_CONFIG_PATH:?Set REMOTE_CONFIG_PATH in deploy.env}"
  SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"
  REMOTE_PATH="${REMOTE_CONFIG_PATH%/}/automations.yaml"
  echo "Deploying automations.yaml to $SSH_USER@$SSH_HOST:$REMOTE_PATH"
  _do_scp "$AUTOMATIONS_FILE" "$SSH_USER@$SSH_HOST:$REMOTE_PATH"
  echo "Done."
  if [[ "$RESTART_AFTER" -eq 1 ]]; then
    do_restart
    wait_for_ha
    do_validate_helpers
  else
    do_reload_automations
    do_validate_helpers
  fi
  exit 0
fi

# Handle --scripts (needs deploy.env)
if [[ "$TARGET" == "--scripts" ]]; then
  if [[ ! -f "$DEPLOY_ENV" ]]; then
    echo "Error: $DEPLOY_ENV not found."
    exit 1
  fi
  if [[ ! -f "$SCRIPTS_FILE" ]]; then
    echo "Error: $SCRIPTS_FILE not found."
    exit 1
  fi
  set -a
  source "$DEPLOY_ENV"
  set +a
  : "${SSH_HOST:?Set SSH_HOST in deploy.env}"
  : "${SSH_USER:?Set SSH_USER in deploy.env}"
  : "${REMOTE_CONFIG_PATH:?Set REMOTE_CONFIG_PATH in deploy.env}"
  SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"
  REMOTE_PATH="${REMOTE_CONFIG_PATH%/}/scripts.yaml"
  echo "Deploying scripts.yaml to $SSH_USER@$SSH_HOST:$REMOTE_PATH"
  _do_scp "$SCRIPTS_FILE" "$SSH_USER@$SSH_HOST:$REMOTE_PATH"
  echo "Done."
  echo "Restart Home Assistant for script changes to take effect (or use --restart)."
  if [[ "$RESTART_AFTER" -eq 1 ]]; then
    do_restart
    wait_for_ha
    do_validate_helpers
  fi
  exit 0
fi

# Handle --python_scripts (deploy python_scripts/ to HA config; no restart required for reload)
if [[ "$TARGET" == "--python_scripts" ]]; then
  if [[ ! -f "$DEPLOY_ENV" ]]; then
    echo "Error: $DEPLOY_ENV not found."
    exit 1
  fi
  PYTHON_SCRIPTS_DIR="$REPO_ROOT/python_scripts"
  if [[ ! -d "$PYTHON_SCRIPTS_DIR" ]]; then
    echo "Error: python_scripts directory not found at $PYTHON_SCRIPTS_DIR"
    exit 1
  fi
  set -a
  source "$DEPLOY_ENV"
  set +a
  : "${SSH_HOST:?Set SSH_HOST in deploy.env}"
  : "${SSH_USER:?Set SSH_USER in deploy.env}"
  : "${REMOTE_CONFIG_PATH:?Set REMOTE_CONFIG_PATH in deploy.env}"
  SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"
  REMOTE_PS="${REMOTE_CONFIG_PATH%/}/python_scripts"
  echo "Deploying python_scripts/ to $SSH_USER@$SSH_HOST:$REMOTE_PS"
  _do_ssh "$SSH_USER@$SSH_HOST" "mkdir -p $REMOTE_PS"
  for f in "$PYTHON_SCRIPTS_DIR"/*.py; do
    [[ -f "$f" ]] || continue
    fn=$(basename "$f")
    _do_scp "$f" "$SSH_USER@$SSH_HOST:$REMOTE_PS/$fn"
    echo "  copied: $fn -> $REMOTE_PS/$fn"
  done
  echo "Remote $REMOTE_PS contents:"
  _do_ssh "$SSH_USER@$SSH_HOST" "ls -la $REMOTE_PS/"
  echo "Done. Reload python_script integration in HA (Developer Tools -> YAML -> Python Scripts) if needed."
  exit 0
fi

# Handle --go2rtc (needs deploy.env)
if [[ "$TARGET" == "--go2rtc" ]]; then
  if [[ ! -f "$DEPLOY_ENV" ]]; then
    echo "Error: $DEPLOY_ENV not found."
    exit 1
  fi
  if [[ ! -f "$GO2RTC_FILE" ]]; then
    echo "Error: $GO2RTC_FILE not found."
    exit 1
  fi
  set -a
  source "$DEPLOY_ENV"
  set +a
  : "${SSH_HOST:?Set SSH_HOST in deploy.env}"
  : "${SSH_USER:?Set SSH_USER in deploy.env}"
  : "${REMOTE_CONFIG_PATH:?Set REMOTE_CONFIG_PATH in deploy.env}"
  SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"
  REMOTE_GO2RTC_PATH="${REMOTE_GO2RTC_PATH:-${REMOTE_CONFIG_PATH%/}/go2rtc.yaml}"
  echo "Deploying go2rtc.yaml to $SSH_USER@$SSH_HOST:$REMOTE_GO2RTC_PATH"
  _do_scp "$GO2RTC_FILE" "$SSH_USER@$SSH_HOST:$REMOTE_GO2RTC_PATH"
  echo "Done."
  echo "Restart the go2rtc add-on (Settings → Add-ons → go2rtc → Restart) for changes to take effect."
  exit 0
fi

# Handle --all (deploy config + automations + go2rtc, then restart)
if [[ "$TARGET" == "--all" ]]; then
  if [[ ! -f "$DEPLOY_ENV" ]]; then
    echo "Error: $DEPLOY_ENV not found."
    exit 1
  fi
  set -a
  source "$DEPLOY_ENV"
  set +a
  : "${SSH_HOST:?Set SSH_HOST in deploy.env}"
  : "${SSH_USER:?Set SSH_USER in deploy.env}"
  : "${REMOTE_CONFIG_PATH:?Set REMOTE_CONFIG_PATH in deploy.env}"
  SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"
  REMOTE_BASE="${REMOTE_CONFIG_PATH%/}"

  echo "=== Deploying all config and restarting ==="
  echo "Deploying configuration.yaml to $SSH_USER@$SSH_HOST:$REMOTE_BASE/configuration.yaml"
  _do_scp "$CONFIG_FILE" "$SSH_USER@$SSH_HOST:$REMOTE_BASE/configuration.yaml"
  if [[ -f "$SCRIPTS_FILE" ]]; then
    echo "Deploying scripts.yaml to $SSH_USER@$SSH_HOST:$REMOTE_BASE/scripts.yaml"
    _do_scp "$SCRIPTS_FILE" "$SSH_USER@$SSH_HOST:$REMOTE_BASE/scripts.yaml"
  fi
  if [[ -f "$SCENES_FILE" ]]; then
    echo "Deploying scenes.yaml to $SSH_USER@$SSH_HOST:$REMOTE_BASE/scenes.yaml"
    _do_scp "$SCENES_FILE" "$SSH_USER@$SSH_HOST:$REMOTE_BASE/scenes.yaml"
  fi
  if [[ -f "$REPO_ROOT/secrets.yaml" ]]; then
    echo "Deploying secrets.yaml to $SSH_USER@$SSH_HOST:$REMOTE_BASE/secrets.yaml"
    _do_scp "$REPO_ROOT/secrets.yaml" "$SSH_USER@$SSH_HOST:$REMOTE_BASE/secrets.yaml"
  fi
  echo "Deploying automations.yaml to $SSH_USER@$SSH_HOST:$REMOTE_BASE/automations.yaml"
  _do_scp "$AUTOMATIONS_FILE" "$SSH_USER@$SSH_HOST:$REMOTE_BASE/automations.yaml"
  if [[ -f "$GO2RTC_FILE" ]]; then
    REMOTE_GO2RTC_PATH="${REMOTE_GO2RTC_PATH:-$REMOTE_BASE/go2rtc.yaml}"
    echo "Deploying go2rtc.yaml to $SSH_USER@$SSH_HOST:$REMOTE_GO2RTC_PATH"
    _do_scp "$GO2RTC_FILE" "$SSH_USER@$SSH_HOST:$REMOTE_GO2RTC_PATH"
  fi
  echo "Done deploying."
  do_restart
  wait_for_ha
  do_validate_helpers
  exit 0
fi

# Handle --spoolman-export (Spoolman → spools.csv; needs deploy.env with SPOOLMAN_URL)
if [[ "$TARGET" == "--spoolman-export" ]]; then
  if [[ ! -f "$DEPLOY_ENV" ]]; then
    echo "Error: $DEPLOY_ENV not found."
    exit 1
  fi
  set -a
  source "$DEPLOY_ENV"
  set +a
  if [[ -z "$SPOOLMAN_URL" ]]; then
    echo "Error: SPOOLMAN_URL not set in deploy.env (e.g. http://host:7912)."
    exit 1
  fi
  SPOOLMAN_DIR="$REPO_ROOT/spoolman_import"
  if [[ ! -f "$SPOOLMAN_DIR/export_spools.py" ]]; then
    echo "Error: $SPOOLMAN_DIR/export_spools.py not found."
    exit 1
  fi
  echo "Exporting Spoolman inventory to spools.csv..."
  (cd "$SPOOLMAN_DIR" && SPOOLMAN_URL="$SPOOLMAN_URL" PYTHONWARNINGS=ignore python3 export_spools.py -o spools.csv)
  echo "Done. Commit spools.csv to keep the repo in sync."
  exit 0
fi

# Handle --spoolman-import (spools.csv → Spoolman; new spools only)
if [[ "$TARGET" == "--spoolman-import" ]]; then
  if [[ ! -f "$DEPLOY_ENV" ]]; then
    echo "Error: $DEPLOY_ENV not found."
    exit 1
  fi
  set -a
  source "$DEPLOY_ENV"
  set +a
  if [[ -z "$SPOOLMAN_URL" ]]; then
    echo "Error: SPOOLMAN_URL not set in deploy.env (e.g. http://host:7912)."
    exit 1
  fi
  SPOOLMAN_DIR="$REPO_ROOT/spoolman_import"
  if [[ ! -f "$SPOOLMAN_DIR/import_spools.py" ]]; then
    echo "Error: $SPOOLMAN_DIR/import_spools.py not found."
    exit 1
  fi
  echo "Importing spools.csv into Spoolman (new spools only; existing names skipped)..."
  (cd "$SPOOLMAN_DIR" && SPOOLMAN_URL="$SPOOLMAN_URL" PYTHONWARNINGS=ignore python3 import_spools.py spools.csv)
  echo "Done."
  exit 0
fi

# Handle --spoolman-update (push CSV weights to Spoolman; needs spool_id from export)
if [[ "$TARGET" == "--spoolman-update" ]]; then
  if [[ ! -f "$DEPLOY_ENV" ]]; then
    echo "Error: $DEPLOY_ENV not found."
    exit 1
  fi
  set -a
  source "$DEPLOY_ENV"
  set +a
  if [[ -z "$SPOOLMAN_URL" ]]; then
    echo "Error: SPOOLMAN_URL not set in deploy.env (e.g. http://host:7912)."
    exit 1
  fi
  SPOOLMAN_DIR="$REPO_ROOT/spoolman_import"
  if [[ ! -f "$SPOOLMAN_DIR/update_spools.py" ]]; then
    echo "Error: $SPOOLMAN_DIR/update_spools.py not found."
    exit 1
  fi
  echo "Pushing remaining_g/empty_spool_g from spools.csv to Spoolman..."
  (cd "$SPOOLMAN_DIR" && SPOOLMAN_URL="$SPOOLMAN_URL" PYTHONWARNINGS=ignore python3 update_spools.py spools.csv)
  echo "Done."
  exit 0
fi

# Prod-prep: generate dashboard.prod.yaml from stage (no deploy, no dirty tree)
if [[ "$TARGET" == "--prod-prep" ]]; then
  PROD_FILE="$REPO_ROOT/dashboards/dashboard.prod.yaml"
  sed 's|/lovelace-stage/|/lovelace/|g' "$STAGE_FILE" | \
    sed 's|# NOTE: Test dashboard uses dashboard-test; stage uses lovelace-stage|# NOTE: Prod version – uses /lovelace/ paths|g' | \
    sed 's|# NOTE: Update navigation_path when promoting to prod dashboard|# NOTE: Prod version – uses /lovelace/ paths|g' \
    > "$PROD_FILE"
  echo "Generated: $PROD_FILE"
  echo "Copy to: Settings → Dashboards → [Main] → ⋮ → Edit → ⋮ → Raw configuration → paste → Save"
  exit 0
fi

# Deploy target
if [[ "$TARGET" == "--stage" || "$TARGET" == "--stage-restart" ]]; then
  SOURCE_FILE="$STAGE_FILE"
  REMOTE_NAME="ui-lovelace-stage.yaml"
  # --stage skips restart by default (just refresh browser); --stage-restart forces HA restart
  [[ "$TARGET" == "--stage-restart" ]] || export SKIP_RESTART=1
elif [[ "$TARGET" == "--promote" ]]; then
  PROD_FILE="$REPO_ROOT/dashboards/dashboard.prod.yaml"
  # Ensure prod version exists and is up to date
  sed 's|/lovelace-stage/|/lovelace/|g' "$STAGE_FILE" | \
    sed 's|# NOTE: Test dashboard uses dashboard-test; stage uses lovelace-stage|# NOTE: Prod version – uses /lovelace/ paths|g' | \
    sed 's|# NOTE: Update navigation_path when promoting to prod dashboard|# NOTE: Prod version – uses /lovelace/ paths|g' \
    > "$PROD_FILE"
  SOURCE_FILE="$PROD_FILE"
  REMOTE_NAME="ui-lovelace.yaml"
else
  echo "Error: use --stage, --prod-prep, --promote, --check, --config, --automations, --scripts, --go2rtc, --all, --spoolman-export, --spoolman-import, --spoolman-update, --restart, --validate, --appdaemon, --appdaemon-restart, or --restart-all."
  exit 1
fi

# Load config
if [[ -f "$DEPLOY_ENV" ]]; then
  set -a
  source "$DEPLOY_ENV"
  set +a
else
  echo "Error: $DEPLOY_ENV not found. Copy deploy.env.example to deploy.env and fill in values."
  exit 1
fi

# Required
: "${SSH_HOST:?Set SSH_HOST in deploy.env}"
: "${SSH_USER:?Set SSH_USER in deploy.env}"
: "${REMOTE_CONFIG_PATH:?Set REMOTE_CONFIG_PATH in deploy.env}"

SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new}"
REMOTE_PATH="${REMOTE_CONFIG_PATH%/}/$REMOTE_NAME"

echo "Deploying $(basename "$SOURCE_FILE") to $SSH_USER@$SSH_HOST:$REMOTE_PATH"
_do_scp "$SOURCE_FILE" "$SSH_USER@$SSH_HOST:$REMOTE_PATH"
echo "Copy complete."
if [[ "$TARGET" == "--stage" || "$TARGET" == "--stage-restart" ]]; then
  # Regenerate prod version for manual copy to HA main dashboard
  PROD_FILE="$REPO_ROOT/dashboards/dashboard.prod.yaml"
  sed 's|/lovelace-stage/|/lovelace/|g' "$STAGE_FILE" | \
    sed 's|# NOTE: Test dashboard uses dashboard-test; stage uses lovelace-stage|# NOTE: Prod version – uses /lovelace/ paths|g' | \
    sed 's|# NOTE: Update navigation_path when promoting to prod dashboard|# NOTE: Prod version – uses /lovelace/ paths|g' \
    > "$PROD_FILE"
  echo ""
  echo "Prod version updated: dashboards/dashboard.prod.yaml"
  echo "  → Copy to: Settings → Dashboards → [Main] → ⋮ → Edit → ⋮ → Raw configuration"
  if [[ "${SKIP_RESTART:-0}" == "1" ]]; then
    echo ""
    echo "Refresh browser at /lovelace-stage to see changes. Use --stage-restart to force HA restart."
  elif [[ -n "$HOME_ASSISTANT_URL" && -n "$HOME_ASSISTANT_TOKEN" ]]; then
    echo ""
    echo "Restarting HA so the Stage dashboard re-reads ui-lovelace-stage.yaml..."
    do_restart
  else
    echo ""
    echo "To see changes: restart HA, or open Stage dashboard → three-dot menu → Refresh."
    echo "To verify file sync: ./scripts/manage_ha.sh --check"
  fi
fi
if [[ "$TARGET" == "--promote" ]]; then
  echo "Stage YAML copied to ui-lovelace.yaml. Reload the dashboard or restart HA if needed."
fi

