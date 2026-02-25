#!/usr/bin/env bash
# Preflight: assert input_text domain and set_value exist in /api/services (read-only; no entity write).
# Use when HA may be in partial startup (entities restored/unavailable, service missing).
# Usage: from repo root; requires deploy.env with HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN.
# Exit: 0 if input_text and set_value present; 1 otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"
if [[ ! -f "$DEPLOY_ENV" ]]; then
  echo "PREFLIGHT_INPUT_TEXT_SERVICES: SKIP (deploy.env not found)"
  exit 0
fi
set -a
source "$DEPLOY_ENV"
set +a

if [[ -z "${HOME_ASSISTANT_URL:-}" || -z "${HOME_ASSISTANT_TOKEN:-}" ]]; then
  echo "PREFLIGHT_INPUT_TEXT_SERVICES: SKIP (HOME_ASSISTANT_URL/TOKEN not set)"
  exit 0
fi

services_json=$(curl -sS -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" "$HOME_ASSISTANT_URL/api/services" 2>/dev/null) || true
if [[ -z "$services_json" ]]; then
  echo "PREFLIGHT_INPUT_TEXT_SERVICES: FAIL — /api/services empty or unreachable"
  exit 1
fi

if command -v jq >/dev/null 2>&1; then
  has=$(echo "$services_json" | jq -r 'if type == "object" then (has("input_text") and (.["input_text"] | type == "object") and (.["input_text"] | has("set_value"))) else false end' 2>/dev/null || echo "false")
  if [[ "$has" != "true" ]]; then
    echo "PREFLIGHT_INPUT_TEXT_SERVICES: FAIL — input_text domain or set_value missing in /api/services (input_text integration may not have loaded)"
    exit 1
  fi
else
  if ! echo "$services_json" | grep -q '"input_text"' || ! echo "$services_json" | grep -q '"set_value"'; then
    echo "PREFLIGHT_INPUT_TEXT_SERVICES: FAIL — input_text or set_value not found in /api/services"
    exit 1
  fi
fi

echo "PREFLIGHT_INPUT_TEXT_SERVICES: PASS"
exit 0
