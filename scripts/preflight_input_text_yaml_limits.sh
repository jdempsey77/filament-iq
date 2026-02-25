#!/usr/bin/env bash
# Preflight: assert no input_text entity in configuration.yaml has max > 255.
# HA silently refuses to load the input_text integration if any max exceeds 255.
# Usage: from repo root, ./scripts/preflight_input_text_yaml_limits.sh
# Exit: 0 if all max values <= 255; 1 with offending lines if any exceed 255.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
CONFIG="$REPO_ROOT/configuration.yaml"

if [[ ! -f "$CONFIG" ]]; then
  echo "PREFLIGHT_INPUT_TEXT_YAML_LIMITS: SKIP — configuration.yaml not found"
  exit 0
fi

# Extract the input_text: block and find max: lines with values > 255
# Strategy: print lines between ^input_text: and next top-level key; grep for max:
found=0
current_key=""
while IFS= read -r line; do
  # Track current helper key (lines like "  helper_name:")
  if [[ "$line" =~ ^[[:space:]]{2}[a-z_]+: ]]; then
    current_key=$(echo "$line" | sed 's/^[[:space:]]*//' | sed 's/:.*//')
  fi
  # Check max: lines
  if [[ "$line" =~ ^[[:space:]]+max:[[:space:]]*([0-9]+) ]]; then
    max_val="${BASH_REMATCH[1]}"
    if [[ "$max_val" -gt 255 ]]; then
      echo "PREFLIGHT_INPUT_TEXT_YAML_LIMITS: FAIL — input_text.$current_key has max: $max_val (must be <= 255)"
      found=1
    fi
  fi
done < <(sed -n '/^input_text:/,/^[a-z_]*:/p' "$CONFIG" | sed '$ d')

if [[ $found -eq 1 ]]; then
  echo "HA will refuse to load the input_text integration if any max > 255."
  exit 1
fi

echo "PREFLIGHT_INPUT_TEXT_YAML_LIMITS: PASS"
exit 0
