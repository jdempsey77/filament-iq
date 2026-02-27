#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MODULE="$REPO_ROOT/appdaemon/apps/spoolman_extra_canonicalizer.py"

if [[ ! -f "$MODULE" ]]; then
  echo "FAIL: $MODULE not found" >&2
  exit 1
fi

export PYTHONPATH="$REPO_ROOT/appdaemon/apps${PYTHONPATH:+:$PYTHONPATH}"
if python3 -c "from spoolman_extra_canonicalizer import canonicalize_rfid_tag_uid, canonicalize_ha_spool_uuid, encode_extra_json_string, is_double_encoded, validate_extra_value_no_quotes; print('OK')" 2>&1; then
  exit 0
else
  echo "FAIL: spoolman_extra_canonicalizer import check failed" >&2
  exit 1
fi
