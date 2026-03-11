#!/usr/bin/env bash
# scripts/sync-oss.sh
# Check for drift between home_assistant (private) and filament-iq (OSS).
# Exits 1 if drift found (unless --copy is passed).
#
# Usage:
#   ./scripts/sync-oss.sh          # check only, exits 1 if drift
#   ./scripts/sync-oss.sh --copy   # copy changed files, no commit

set -euo pipefail

PRIVATE_REPO="${HOME}/code/home_assistant"
OSS_REPO="${HOME}/code/filament-iq"

PRIVATE_APPS="${PRIVATE_REPO}/appdaemon/apps/filament_iq"
OSS_APPS="${OSS_REPO}/apps/filament_iq"

PRIVATE_TESTS="${PRIVATE_REPO}/tests"
OSS_TESTS="${OSS_REPO}/tests"

COPY_MODE=false
if [[ "${1:-}" == "--copy" ]]; then
  COPY_MODE=true
fi

DRIFT=0
CHANGED_FILES=()
NEW_FILES=()

echo "=== Filament IQ OSS Sync Check ==="
echo "Private : ${PRIVATE_REPO}"
echo "OSS     : ${OSS_REPO}"
echo ""

check_file() {
  local src="$1"
  local dst="$2"
  local fname
  fname=$(basename "$src")

  if [[ ! -f "$dst" ]]; then
    echo "  NEW      ${fname}"
    NEW_FILES+=("$fname")
    DRIFT=1
    if $COPY_MODE; then
      cp "$src" "$dst"
      echo "           → copied"
    fi
  elif ! diff -q "$src" "$dst" > /dev/null 2>&1; then
    echo "  CHANGED  ${fname}"
    CHANGED_FILES+=("$fname")
    DRIFT=1
    if $COPY_MODE; then
      cp "$src" "$dst"
      echo "           → copied"
    fi
  else
    echo "  ok       ${fname}"
  fi
}

echo "── App source files ──"
for f in "${PRIVATE_APPS}"/*.py "${PRIVATE_APPS}/apps.yaml.example"; do
  [[ -f "$f" ]] || continue
  check_file "$f" "${OSS_APPS}/$(basename "$f")"
done

echo ""
echo "── Test files ──"
for f in "${PRIVATE_TESTS}"/test_*.py; do
  [[ -f "$f" ]] || continue
  check_file "$f" "${OSS_TESTS}/$(basename "$f")"
done

echo ""

# Summary
if [[ $DRIFT -eq 0 ]]; then
  echo "✅ OSS repo is in sync — no drift detected"
  exit 0
fi

if $COPY_MODE; then
  echo "✅ Sync complete:"
  [[ ${#NEW_FILES[@]} -gt 0 ]]     && echo "   New files    : ${NEW_FILES[*]}"
  [[ ${#CHANGED_FILES[@]} -gt 0 ]] && echo "   Changed files: ${CHANGED_FILES[*]}"
  echo ""
  echo "Next: review then commit"
  echo "  cd ${OSS_REPO} && git diff --stat"
  exit 0
fi

# Drift found, not in copy mode
echo "⚠️  OSS drift detected:"
[[ ${#NEW_FILES[@]} -gt 0 ]]     && echo "   New files    : ${NEW_FILES[*]}"
[[ ${#CHANGED_FILES[@]} -gt 0 ]] && echo "   Changed files: ${CHANGED_FILES[*]}"
echo ""
echo "Run with --copy to sync:"
echo "  ./scripts/sync-oss.sh --copy"
exit 1
