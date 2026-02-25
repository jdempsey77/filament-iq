#!/usr/bin/env bash
# Preflight: detect unsupported HA template filter usage (e.g. | split() which does not exist in HA).
# Usage: from repo root, ./scripts/preflight_template_filters.sh
# Exit: 0 if no forbidden pattern found; 1 and list of matches if found.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Forbidden pattern: pipe to split as filter (HA has no split filter; use .split() method)
PATTERN='\| split\('
FILES=(
  "$REPO_ROOT/automations.yaml"
  "$REPO_ROOT/configuration.yaml"
  "$REPO_ROOT/scripts.yaml"
)

found=0
for f in "${FILES[@]}"; do
  if [[ -f "$f" ]] && grep -n "$PATTERN" "$f" >/dev/null 2>&1; then
    echo "PREFLIGHT_TEMPLATE_FILTERS: FAIL — unsupported template filter in $f"
    grep -n "$PATTERN" "$f" || true
    found=1
  fi
done

if [[ $found -eq 1 ]]; then
  echo "Use .split() method instead of | split() (e.g. (var | lower).split())."
  exit 1
fi

echo "PREFLIGHT_TEMPLATE_FILTERS: PASS — no | split( usage in template YAML"
exit 0
