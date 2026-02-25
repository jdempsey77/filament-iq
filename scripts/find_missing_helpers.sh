#!/usr/bin/env bash
# Locate missing helpers from validate_helpers.sh: search repo for their definitions.
# Usage: from repo root, ./scripts/find_missing_helpers.sh
# Requires: bash, grep, jq; deploy.env for validate_helpers.sh.
# Exit: 0 if no missing helpers; 1 if any missing.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Get missing entity_ids (run validator with OUTPUT_MISSING_ONLY)
cd "$REPO_ROOT"
missing_list="$(OUTPUT_MISSING_ONLY=1 ./scripts/validate_helpers.sh 2>/dev/null)" ; val_exit=$?
if [[ $val_exit -eq 0 ]]; then
  echo "===== MISSING HELPERS LOCATOR ====="
  echo "Missing in HA: 0"
  exit 0
fi
if [[ -z "$missing_list" ]]; then
  echo "Validator failed (e.g. deploy.env or HA unreachable):" >&2
  ./scripts/validate_helpers.sh 2>&1
  exit 1
fi

# Search paths: config dirs and repo root *.yaml
search_dirs=()
for d in config home_assistant packages scripts; do
  [[ -d "$REPO_ROOT/$d" ]] && search_dirs+=("$REPO_ROOT/$d")
done
search_args=("${search_dirs[@]}")
for f in "$REPO_ROOT"/*.yaml "$REPO_ROOT"/*.yml; do
  [[ -f "$f" ]] && search_args+=("$f")
done

missing_count=0
while IFS= read -r entity_id; do
  [[ -z "$entity_id" ]] && continue
  (( missing_count++ )) || true
done <<< "$missing_list"

echo "===== MISSING HELPERS LOCATOR ====="
echo "Missing in HA: $missing_count"
exit_code=1

while IFS= read -r entity_id; do
  [[ -z "$entity_id" ]] && continue
  # Key without domain: input_text.ams_slot_2_status -> ams_slot_2_status
  key="${entity_id#*.}"
  echo ""
  echo "$entity_id"

  # Search for key (word) and YAML key form "key:"
  found=0
  if [[ ${#search_args[@]} -gt 0 ]]; then
    hits="$(grep -rn -- "$key" "${search_args[@]}" 2>/dev/null || true)"
    if [[ -n "$hits" ]]; then
      echo "  FOUND IN REPO:"
      echo "$hits" | while IFS= read -r line; do
        echo "    - $line"
      done
      found=1
    fi
  fi

  if [[ $found -eq 0 ]]; then
    echo "  NOT FOUND IN REPO (needs to be added to YAML)"
  fi
done <<< "$missing_list"

exit $exit_code
