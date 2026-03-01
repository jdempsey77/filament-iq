#!/usr/bin/env bash
# Clear input_text.ams_slot_X_unbound_reason for slots 1-6 so the dashboard
# stops showing "BIND NEEDED" for already-reconciled or intentionally-empty slots.
# Usage: from repo root, ./scripts/clear_ams_unbound_reasons.sh
# Requires deploy.env (or deploy.env.local) with HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for f in "$SCRIPT_DIR/deploy.env" "$SCRIPT_DIR/deploy.env.local"; do
  if [[ -f "$f" ]]; then
    set -a; source "$f"; set +a
    break
  fi
done
: "${HOME_ASSISTANT_URL:?Set HOME_ASSISTANT_URL in deploy.env or deploy.env.local}"
: "${HOME_ASSISTANT_TOKEN:?Set HOME_ASSISTANT_TOKEN in deploy.env or deploy.env.local}"

AUTH="Authorization: Bearer $HOME_ASSISTANT_TOKEN"
BASE="$HOME_ASSISTANT_URL"

for i in 1 2 3 4 5 6; do
  entity="input_text.ams_slot_${i}_unbound_reason"
  if curl -sS -X POST -H "$AUTH" -H "Content-Type: application/json" \
    -d '{"state": ""}' \
    "$BASE/api/states/$entity" >/dev/null; then
    echo "Cleared $entity"
  else
    echo "Failed to clear $entity" >&2
  fi
done

echo "Done. Slots 1-6 unbound_reason set to empty."
