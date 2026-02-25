#!/usr/bin/env bash
# Validate required helpers from helpers_manifest.yaml against HA /api/states.
# Usage: from repo root, ./scripts/validate_helpers.sh
# Requires: deploy.env (HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN), curl, jq.
# Exit: 0 if all helpers present and not zombie; 1 otherwise.
# Optional: OUTPUT_MISSING_ONLY=1 prints only missing entity_ids (one per line) for scripting.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
MANIFEST="$REPO_ROOT/helpers_manifest.yaml"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"

if [[ ! -f "$MANIFEST" ]]; then
  echo "Error: helpers_manifest.yaml not found at $MANIFEST" >&2
  exit 1
fi

# Source deploy.env (required for HA URL/token)
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

# Read required_helpers list (simple grep/sed: lines "  - entity_id")
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

# Helper settle: wait for HA helpers to be stable (not restored/unavailable), not just HTTP 200
SETTLE_SECONDS="${SETTLE_SECONDS:-60}"
SETTLE_SLEEP="${SETTLE_SLEEP:-3}"
PROBE_URL="${HOME_ASSISTANT_URL}/api/states/input_text.spoolman_base_url"
start_ts=$(date +%s)
while true; do
  body=$(curl -sS -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" "$PROBE_URL" 2>/dev/null) || true
  stable=0
  if [[ -n "$body" ]] && echo "$body" | jq -e . >/dev/null 2>&1; then
    state=$(echo "$body" | jq -r '.state // ""')
    restored=$(echo "$body" | jq -r '.attributes.restored // false')
    if [[ "$state" != "unavailable" && "$restored" != "true" ]]; then
      stable=1
    fi
  fi
  if [[ "$stable" -eq 1 ]]; then
    break
  fi
  now_ts=$(date +%s)
  elapsed=$(( now_ts - start_ts ))
  if [[ $elapsed -ge SETTLE_SECONDS ]]; then
    echo "WARN: helper settle timeout (${SETTLE_SECONDS}s); proceeding anyway" >&2
    break
  fi
  sleep "$SETTLE_SLEEP"
done

# Fetch all states once
states_json="$(curl -sS -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" "$HOME_ASSISTANT_URL/api/states")"
if ! echo "$states_json" | jq -e . >/dev/null 2>&1; then
  echo "Error: /api/states did not return valid JSON" >&2
  exit 1
fi

# Build entity_id -> state object map via jq
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

# Output for scripting: only missing entity_ids
if [[ "${OUTPUT_MISSING_ONLY:-0}" == "1" ]]; then
  for e in "${missing[@]}"; do echo "$e"; done
  [[ ${#missing[@]} -gt 0 ]] && exit 1
  exit 0
fi

# Human output
echo "===== HELPERS VALIDATION ====="
ok=$(( ${#required[@]} - ${#missing[@]} - ${#zombies[@]} ))
echo "PASS: $ok helpers OK"
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "MISSING: ${#missing[@]}"
  for e in "${missing[@]}"; do echo "  - $e"; done
fi
if [[ ${#zombies[@]} -gt 0 ]]; then
  echo "ZOMBIES (restored/unavailable): ${#zombies[@]}"
  for e in "${zombies[@]}"; do echo "  - $e"; done
fi
# Hard FAIL when PASS:0 but we expect helpers: input_text integration likely not loaded
if [[ $ok -eq 0 && ${#required[@]} -gt 0 ]]; then
  echo "FAIL: PASS:0 but required helpers expected. input_text integration may not be loaded (check /api/services for input_text domain and set_value)." >&2
fi

[[ ${#missing[@]} -eq 0 && ${#zombies[@]} -eq 0 ]] && exit 0
exit 1
