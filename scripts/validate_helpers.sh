#!/usr/bin/env bash
# Validate required helpers from helpers_manifest.yaml against HA /api/states.
# Usage: from repo root, ./scripts/validate_helpers.sh [--json]
# Requires: deploy.env (HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN), curl, jq.
# Exit: 0 if all helpers present and not zombie; 1 otherwise.
#
# Modes:
#   (default)              Human-readable output with settle wait.
#   --json                 Machine-readable JSON, no settle wait, single-line stdout.
#   OUTPUT_MISSING_ONLY=1  Print only missing entity_ids (one per line).

set -e

JSON_MODE=0
for _arg in "$@"; do
  [[ "$_arg" == "--json" ]] && JSON_MODE=1
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
MANIFEST="$REPO_ROOT/helpers_manifest.yaml"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"

if [[ ! -f "$MANIFEST" ]]; then
  echo "Error: helpers_manifest.yaml not found at $MANIFEST" >&2
  exit 1
fi

if [[ ! -f "$DEPLOY_ENV" ]]; then
  echo "Error: deploy.env not found at $DEPLOY_ENV" >&2
  exit 1
fi
set -a
source "$DEPLOY_ENV"
set +a

if [[ -z "${HOME_ASSISTANT_URL:-}" || -z "${HOME_ASSISTANT_TOKEN:-}" ]]; then
  echo "Error: HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN must be set in deploy.env" >&2
  exit 1
fi

# Read required_helpers list
required=()
while IFS= read -r line; do
  line="${line#*  - }"
  line="${line// /}"
  [[ -n "$line" ]] && required+=("$line")
done < <(sed -n '/^required_helpers:/,/^[^ ]/p' "$MANIFEST" | grep "  - " | sed 's/^[[:space:]]*-[[:space:]]*//')

if [[ ${#required[@]} -eq 0 ]]; then
  echo "Error: no required_helpers found in $MANIFEST" >&2
  exit 1
fi

# Helper settle wait (skipped in --json mode; caller handles timing)
if [[ $JSON_MODE -eq 0 ]]; then
  SETTLE_SECONDS="${SETTLE_SECONDS:-60}"
  SETTLE_SLEEP="${SETTLE_SLEEP:-3}"
  PROBE_URL="${HOME_ASSISTANT_URL}/api/states/input_text.spoolman_base_url"
  _settle_start=$(date +%s)
  while true; do
    _probe_body=$(curl -sS -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" "$PROBE_URL" 2>/dev/null) || true
    _settle_ok=0
    if [[ -n "$_probe_body" ]] && echo "$_probe_body" | jq -e . >/dev/null 2>&1; then
      _probe_state=$(echo "$_probe_body" | jq -r '.state // ""')
      _probe_restored=$(echo "$_probe_body" | jq -r '.attributes.restored // false')
      if [[ "$_probe_state" != "unavailable" && "$_probe_restored" != "true" ]]; then
        _settle_ok=1
      fi
    fi
    [[ $_settle_ok -eq 1 ]] && break
    _settle_now=$(date +%s)
    _settle_elapsed=$(( _settle_now - _settle_start ))
    if [[ $_settle_elapsed -ge $SETTLE_SECONDS ]]; then
      echo "WARN: helper settle timeout (${SETTLE_SECONDS}s); proceeding anyway" >&2
      break
    fi
    sleep "$SETTLE_SLEEP"
  done
fi

# Fetch all states once
states_json="$(curl -sS -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" "$HOME_ASSISTANT_URL/api/states" 2>/dev/null)"
if ! echo "$states_json" | jq -e . >/dev/null 2>&1; then
  if [[ $JSON_MODE -eq 1 ]]; then
    echo '{"required":'"${#required[@]}"',"ok":0,"zombies":0,"missing":'"${#required[@]}"',"zombie_ids":[],"missing_ids":[],"error":"api_states_invalid"}'
    exit 1
  fi
  echo "Error: /api/states did not return valid JSON" >&2
  exit 1
fi

declare -a missing=()
declare -a zombies=()

for entity_id in "${required[@]}"; do
  ent="$(echo "$states_json" | jq -c --arg e "$entity_id" '.[] | select(.entity_id == $e) | {entity_id, state, attributes}')"
  if [[ -z "$ent" ]]; then
    missing+=("$entity_id")
    continue
  fi
  state_val="$(echo "$ent" | jq -r '.state // ""')"
  restored="$(echo "$ent" | jq -r '.attributes.restored // false')"
  if [[ "$state_val" == "unavailable" && "$restored" == "true" ]]; then
    zombies+=("$entity_id")
  fi
done

ok=$(( ${#required[@]} - ${#missing[@]} - ${#zombies[@]} ))

# --json mode: single-line JSON output, no human text
if [[ $JSON_MODE -eq 1 ]]; then
  _z_ids="[]"
  if [[ ${#zombies[@]} -gt 0 ]]; then
    _z_ids=$(printf '%s\n' "${zombies[@]}" | head -5 | jq -R . | jq -sc .)
  fi
  _m_ids="[]"
  if [[ ${#missing[@]} -gt 0 ]]; then
    _m_ids=$(printf '%s\n' "${missing[@]}" | head -5 | jq -R . | jq -sc .)
  fi
  echo "{\"required\":${#required[@]},\"ok\":${ok},\"zombies\":${#zombies[@]},\"missing\":${#missing[@]},\"zombie_ids\":${_z_ids},\"missing_ids\":${_m_ids}}"
  if [[ $ok -eq ${#required[@]} && ${#zombies[@]} -eq 0 && ${#missing[@]} -eq 0 ]]; then
    exit 0
  fi
  exit 1
fi

# OUTPUT_MISSING_ONLY mode
if [[ "${OUTPUT_MISSING_ONLY:-0}" == "1" ]]; then
  if [[ ${#missing[@]} -gt 0 ]]; then
    for e in "${missing[@]}"; do echo "$e"; done
    exit 1
  fi
  exit 0
fi

# Human output
echo "===== HELPERS VALIDATION ====="
echo "PASS: $ok helpers OK"
echo "REQUIRED: ${#required[@]}"
echo "ZOMBIES_COUNT: ${#zombies[@]}"
echo "MISSING_COUNT: ${#missing[@]}"
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "MISSING:"
  for e in "${missing[@]}"; do echo "  - $e"; done
fi
if [[ ${#zombies[@]} -gt 0 ]]; then
  echo "ZOMBIES (restored/unavailable):"
  for e in "${zombies[@]}"; do echo "  - $e"; done
fi
if [[ $ok -eq 0 && ${#required[@]} -gt 0 ]]; then
  echo "FAIL: PASS:0 but required helpers expected. input_text integration may not be loaded." >&2
  exit 1
fi
if [[ ${#zombies[@]} -gt 0 ]]; then
  echo "FAIL: ${#zombies[@]} zombie helpers detected (restored/unavailable)." >&2
  exit 1
fi

[[ ${#missing[@]} -eq 0 ]] && exit 0
exit 1
