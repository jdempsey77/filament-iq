#!/usr/bin/env bash
# Filament IQ — HA Token Rotation
# Rotates HA_TOKEN in deploy.env.local (Mac) and secrets.env (ska).
# Restarts filament-iq-monitor on ska if running.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/deploy.env.local"
SKA_SECRETS_DIR=".config/filament_iq"
SKA_SECRETS_FILE="$SKA_SECRETS_DIR/secrets.env"
SKA_HOST="ska"
SKA_UNIT="filament-iq-monitor"

mask_token() {
  local t="$1"
  if [[ ${#t} -ge 4 ]]; then
    echo "...${t: -4}"
  else
    echo "...(short)"
  fi
}

# ── Header ───────────────────────────────────────────────────────────
echo "========================================"
echo "Filament IQ — HA Token Rotation"
echo "========================================"
echo ""

# ── Read current token ───────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found"
  exit 1
fi

CURRENT_TOKEN=""
if grep -q '^HOME_ASSISTANT_TOKEN=' "$ENV_FILE" 2>/dev/null; then
  CURRENT_TOKEN=$(grep '^HOME_ASSISTANT_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2-)
fi

if [[ -n "$CURRENT_TOKEN" ]]; then
  echo "Current token: $(mask_token "$CURRENT_TOKEN")"
else
  echo "Current token: (not set)"
fi
echo ""

# ── Prompt for new token ─────────────────────────────────────────────
read -s -r -p "New HA token: " NEW_TOKEN
echo ""
read -s -r -p "Confirm token: " CONFIRM_TOKEN
echo ""
echo ""

if [[ -z "$NEW_TOKEN" ]]; then
  echo "ERROR: Token cannot be empty."
  exit 1
fi

if [[ "$NEW_TOKEN" != "$CONFIRM_TOKEN" ]]; then
  echo "ERROR: Tokens do not match. Aborting."
  exit 1
fi

if [[ "$NEW_TOKEN" == "$CURRENT_TOKEN" ]]; then
  echo "WARNING: New token is identical to current token."
  read -r -p "Continue anyway? [y/N] " yn
  if [[ "$yn" != "y" && "$yn" != "Y" ]]; then
    echo "Aborted."
    exit 0
  fi
  echo ""
fi

# ── Update deploy.env.local (Mac) ───────────────────────────────────
echo "Updating $ENV_FILE ..."

TMP_FILE="$ENV_FILE.tmp.$$"
REPLACED=false
while IFS= read -r line || [[ -n "$line" ]]; do
  if [[ "$line" == HOME_ASSISTANT_TOKEN=* ]]; then
    echo "HOME_ASSISTANT_TOKEN=${NEW_TOKEN}" >> "$TMP_FILE"
    REPLACED=true
  else
    echo "$line" >> "$TMP_FILE"
  fi
done < "$ENV_FILE"
if [[ "$REPLACED" == false ]]; then
  echo "HOME_ASSISTANT_TOKEN=${NEW_TOKEN}" >> "$TMP_FILE"
fi

mv "$TMP_FILE" "$ENV_FILE"
echo "  deploy.env.local: UPDATED ($(mask_token "$NEW_TOKEN"))"
echo ""

# ── Update ska ───────────────────────────────────────────────────────
echo "Pushing to $SKA_HOST ..."

SKA_DAEMON="NOT YET INSTALLED"

REMOTE_SCRIPT='
  set -euo pipefail
  mkdir -p "$HOME/'"$SKA_SECRETS_DIR"'"
  TMP="$HOME/'"$SKA_SECRETS_FILE"'.tmp.$$"
  echo "HOME_ASSISTANT_TOKEN='"$NEW_TOKEN"'" > "$TMP"
  chmod 600 "$TMP"
  mv "$TMP" "$HOME/'"$SKA_SECRETS_FILE"'"
  chmod 600 "$HOME/'"$SKA_SECRETS_FILE"'"

  if systemctl --user list-unit-files '"$SKA_UNIT"'.service >/dev/null 2>&1 && \
     systemctl --user is-active --quiet '"$SKA_UNIT"'.service 2>/dev/null; then
    systemctl --user restart '"$SKA_UNIT"'.service
    echo "DAEMON_RESTARTED"
  elif systemctl --user list-unit-files '"$SKA_UNIT"'.service >/dev/null 2>&1; then
    echo "DAEMON_EXISTS_NOT_ACTIVE"
  else
    echo "DAEMON_NOT_INSTALLED"
  fi
'
SSH_OUTPUT=$(ssh -n "$SKA_HOST" bash -c "$REMOTE_SCRIPT" || true)

if [[ -z "$SSH_OUTPUT" ]]; then
  echo ""
  echo "WARNING: SSH to $SKA_HOST failed."
  echo "  Mac deploy.env.local: UPDATED"
  echo "  ska secrets.env: OUT OF SYNC — update manually"
  echo ""
  echo "To fix manually:"
  echo "  ssh $SKA_HOST"
  echo "  mkdir -p ~/$SKA_SECRETS_DIR"
  echo "  echo 'HA_TOKEN=<token>' > ~/$SKA_SECRETS_FILE"
  echo "  chmod 600 ~/$SKA_SECRETS_FILE"
  exit 1
fi

# Parse daemon status from last line of SSH output
SSH_RESULT=$(echo "$SSH_OUTPUT" | tail -1)
case "$SSH_RESULT" in
  DAEMON_RESTARTED)         SKA_DAEMON="RESTARTED" ;;
  DAEMON_EXISTS_NOT_ACTIVE) SKA_DAEMON="INSTALLED BUT NOT ACTIVE" ;;
  DAEMON_NOT_INSTALLED)     SKA_DAEMON="NOT YET INSTALLED" ;;
  *)                        SKA_DAEMON="UNKNOWN" ;;
esac

echo "  ska secrets.env: UPDATED ($(mask_token "$NEW_TOKEN"))"
echo "  Monitor daemon: $SKA_DAEMON"
echo ""

# ── Summary ──────────────────────────────────────────────────────────
echo "========================================"
echo "Rotation complete"
echo "========================================"
echo "  Mac deploy.env.local:  UPDATED"
echo "  ska secrets.env:       UPDATED"
echo "  Monitor daemon:        $SKA_DAEMON"
echo ""
echo "Verify HA connectivity:"
echo "  curl -s -o /dev/null -w '%{http_code}' \\"
echo "    -H 'Authorization: Bearer \$HA_TOKEN' \\"
echo "    \$HOME_ASSISTANT_URL/api/"
