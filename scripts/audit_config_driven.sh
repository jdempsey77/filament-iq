#!/usr/bin/env bash
# FilamentIQ config-driven audit — static analysis for hardcoded instance-specific values.
# Scans appdaemon/apps/filament_iq/*.py and reports findings.
#
# Usage: ./scripts/audit_config_driven.sh
#
# Exit: 0 if CLEAN. Exit 1 if any issues found.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
FILAMENT_IQ_DIR="$REPO_ROOT/appdaemon/apps/filament_iq"

if [[ ! -d "$FILAMENT_IQ_DIR" ]]; then
  echo "Error: $FILAMENT_IQ_DIR not found." >&2
  exit 1
fi

python3 "$SCRIPT_DIR/audit_config_driven.py" "$FILAMENT_IQ_DIR"
