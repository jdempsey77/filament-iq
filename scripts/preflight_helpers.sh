#!/usr/bin/env bash
set -euo pipefail

# preflight_helpers.sh — Local-only checks (no HA/AppDaemon required):
#   1. .storage/input_text conflict detection
#   2. helpers_manifest.yaml existence
#   3. Bidirectional sync: manifest ↔ configuration.yaml

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CONFIG_YAML="$REPO_ROOT/configuration.yaml"
MANIFEST="$REPO_ROOT/helpers_manifest.yaml"
STORAGE_FILE="/config/.storage/input_text"

ERRORS=0

# --------------------------------------------------------------------------
# 1) .storage conflict
# --------------------------------------------------------------------------
if [[ -f "$STORAGE_FILE" ]]; then
  echo "ERROR: $STORAGE_FILE exists and will conflict with yaml-defined helpers."
  echo "This file causes the input_text integration to silently fail in Home Assistant."
  echo "Resolution: Delete $STORAGE_FILE and restart HA core."
  echo "See docs/troubleshoot_helpers_zombies.md for details."
  ERRORS=$((ERRORS + 1))
fi

# --------------------------------------------------------------------------
# 2) Manifest existence
# --------------------------------------------------------------------------
if [[ ! -f "$MANIFEST" ]]; then
  echo "ERROR: helpers_manifest.yaml not found at $MANIFEST"
  exit 1
fi

if [[ ! -f "$CONFIG_YAML" ]]; then
  echo "ERROR: configuration.yaml not found at $CONFIG_YAML"
  exit 1
fi

# --------------------------------------------------------------------------
# 3) Parse helpers from configuration.yaml and manifest, then cross-check
# --------------------------------------------------------------------------

# Extract input_text helper names from configuration.yaml.
# Strategy: capture lines between "^input_text:" and the next top-level key,
# then pick 2-space-indented keys (^  name:).
yaml_helpers=()
in_input_text=0
while IFS= read -r line; do
  if [[ "$line" =~ ^input_text: ]]; then
    in_input_text=1
    continue
  fi
  if [[ $in_input_text -eq 1 ]]; then
    # A non-indented, non-blank, non-comment line means we left the section
    if [[ "$line" =~ ^[a-zA-Z] ]]; then
      break
    fi
    # Match "  helper_name:" (exactly 2-space indent, alphanumeric key, colon)
    if [[ "$line" =~ ^\ \ ([a-zA-Z_][a-zA-Z0-9_]*): ]]; then
      yaml_helpers+=("input_text.${BASH_REMATCH[1]}")
    fi
  fi
done < "$CONFIG_YAML"

# Extract helper names from manifest (lines matching "  - input_text.xxx")
manifest_helpers=()
while IFS= read -r line; do
  if [[ "$line" =~ ^\ *-\ *(input_text\.[a-zA-Z0-9_]+) ]]; then
    manifest_helpers+=("${BASH_REMATCH[1]}")
  fi
done < "$MANIFEST"

# Build lookup sets (bash 3.2 compatible — use sorted temp files)
yaml_sorted="$(printf '%s\n' "${yaml_helpers[@]}" | sort)"
manifest_sorted="$(printf '%s\n' "${manifest_helpers[@]}" | sort)"

# In yaml but not in manifest
missing_from_manifest=()
for h in "${yaml_helpers[@]}"; do
  found=0
  for m in "${manifest_helpers[@]}"; do
    if [[ "$h" == "$m" ]]; then found=1; break; fi
  done
  if [[ $found -eq 0 ]]; then
    missing_from_manifest+=("$h")
  fi
done

# In manifest but not in yaml
missing_from_yaml=()
for m in "${manifest_helpers[@]}"; do
  found=0
  for h in "${yaml_helpers[@]}"; do
    if [[ "$m" == "$h" ]]; then found=1; break; fi
  done
  if [[ $found -eq 0 ]]; then
    missing_from_yaml+=("$m")
  fi
done

if [[ ${#missing_from_manifest[@]} -gt 0 ]]; then
  for h in "${missing_from_manifest[@]}"; do
    echo "MISSING_FROM_MANIFEST: $h"
  done
  ERRORS=$((ERRORS + ${#missing_from_manifest[@]}))
fi

if [[ ${#missing_from_yaml[@]} -gt 0 ]]; then
  for h in "${missing_from_yaml[@]}"; do
    echo "MISSING_FROM_YAML: $h"
  done
  ERRORS=$((ERRORS + ${#missing_from_yaml[@]}))
fi

if [[ $ERRORS -gt 0 ]]; then
  echo "FAIL: $ERRORS error(s) found in helpers preflight."
  exit 1
fi

echo "OK: helpers manifest and configuration.yaml are in sync (${#yaml_helpers[@]} helpers)"
exit 0
