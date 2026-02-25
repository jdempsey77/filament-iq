#!/usr/bin/env bash
# Preflight: detect unsupported HA Jinja filter/test usage that causes TemplateAssertionError at startup.
# Forbidden patterns:
#   - `| split(`   — HA has no split filter; use `.split()` method on strings
#   - `is regex_search(`  — HA has no regex_search test; use simple string checks or `| regex_search()`
# Usage: from repo root, ./scripts/preflight_template_filters.sh
# Exit: 0 if no forbidden pattern found; 1 and list of matches if found.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

FILES=(
  "$REPO_ROOT/automations.yaml"
  "$REPO_ROOT/configuration.yaml"
  "$REPO_ROOT/scripts.yaml"
)

PATTERNS=(
  '\| split('
  'is regex_search('
)
DESCRIPTIONS=(
  '| split() — HA has no split filter; use .split() method'
  'is regex_search() — HA has no regex_search test; use simple string checks or | regex_search() filter'
)

found=0
i=0
for pattern in "${PATTERNS[@]}"; do
  for f in "${FILES[@]}"; do
    if [[ -f "$f" ]] && grep -Fn "$pattern" "$f" >/dev/null 2>&1; then
      echo "PREFLIGHT_TEMPLATE_FILTERS: FAIL — ${DESCRIPTIONS[$i]}"
      echo "  in $f:"
      grep -Fn "$pattern" "$f" || true
      found=1
    fi
  done
  (( i++ )) || true
done

if [[ $found -eq 1 ]]; then
  exit 1
fi

echo "PREFLIGHT_TEMPLATE_FILTERS: PASS"
exit 0
