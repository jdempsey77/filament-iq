#!/usr/bin/env bash
# Serious-mode pre-check: clean working tree and (optionally) run tests.
# Usage: ./scripts/serious_mode_check.sh
# Safe and fast; if pytest is missing, warns and continues.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# 1) Git clean check (staged or unstaged = dirty)
status_out=$(cd "$REPO_ROOT" && git status --porcelain 2>/dev/null)
if [[ -n "$status_out" ]]; then
  echo "serious_mode_check: FAIL — working tree is dirty." >&2
  echo "" >&2
  echo "git status --porcelain:" >&2
  echo "$status_out" >&2
  echo "" >&2
  echo "Commit or stash your changes, then run again." >&2
  exit 1
fi

# 2) Unit tests if tests dir exists and pytest is available
TESTS_DIR="$REPO_ROOT/tests"
TESTS_RAN=0
if [[ -d "$TESTS_DIR" ]]; then
  if command -v pytest &>/dev/null; then
    (cd "$REPO_ROOT" && pytest -q) || exit 1
    TESTS_RAN=1
  else
    echo "serious_mode_check: WARN — pytest not found; skipping tests." >&2
  fi
fi

echo "serious_mode_check: OK — clean tree"
[[ "$TESTS_RAN" -eq 1 ]] && echo "  Tests passed."
