#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_ENV="$SCRIPT_DIR/deploy.env"
if [[ -f "$DEPLOY_ENV" ]]; then
  set -a
  source "$DEPLOY_ENV"
  set +a
fi

if [[ -z "${HOME_ASSISTANT_URL:-}" || -z "${HOME_ASSISTANT_TOKEN:-}" ]]; then
  echo "INPUT_TEXT_DOMAIN_PRESENT=NO INPUT_TEXT_WRITE_TEST=FAIL SERVICES_HTTP=000 STATE_HTTP=000 DETAILS=\"missing HOME_ASSISTANT_URL/HOME_ASSISTANT_TOKEN\""
  exit 1
fi

helper_entity="${INPUT_TEXT_PREFLIGHT_ENTITY:-input_text.filament_iq_last_active_tray}"
test_value="preflight-$(date +%s)"

services_resp_file="$(mktemp)"
state_before_file="$(mktemp)"
state_after_file="$(mktemp)"
trap 'rm -f "$services_resp_file" "$state_before_file" "$state_after_file"' EXIT

services_http="$(
  curl -s -o "$services_resp_file" -w "%{http_code}" \
    -H "Authorization: Bearer ${HOME_ASSISTANT_TOKEN}" \
    "${HOME_ASSISTANT_URL}/api/services" || true
)"

state_http="$(
  curl -s -o "$state_before_file" -w "%{http_code}" \
    -H "Authorization: Bearer ${HOME_ASSISTANT_TOKEN}" \
    "${HOME_ASSISTANT_URL}/api/states/${helper_entity}" || true
)"

domain_present="NO"
set_service_present="NO"
if [[ "$services_http" == "200" ]]; then
  domain_present="$(
    python3 - <<'PY' "$services_resp_file"
import json, sys
path = sys.argv[1]
try:
    data = json.load(open(path))
except Exception:
    print("NO")
    raise SystemExit(0)
for domain in data:
    if domain.get("domain") == "input_text":
        print("YES")
        raise SystemExit(0)
print("NO")
PY
  )"
  set_service_present="$(
    python3 - <<'PY' "$services_resp_file"
import json, sys
path = sys.argv[1]
try:
    data = json.load(open(path))
except Exception:
    print("NO")
    raise SystemExit(0)
for domain in data:
    if domain.get("domain") != "input_text":
        continue
    for svc in domain.get("services", {}):
        if svc == "set_value":
            print("YES")
            raise SystemExit(0)
print("NO")
PY
  )"
fi

if [[ "$domain_present" != "YES" || "$set_service_present" != "YES" || "$state_http" != "200" ]]; then
  echo "INPUT_TEXT_DOMAIN_PRESENT=${domain_present} INPUT_TEXT_WRITE_TEST=FAIL SERVICES_HTTP=${services_http} STATE_HTTP=${state_http}"
  exit 1
fi

old_value="$(
  python3 - <<'PY' "$state_before_file"
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    print((data.get("state") or ""))
except Exception:
    print("")
PY
)"

set_http="$(
  curl -s -o /dev/null -w "%{http_code}" -X POST \
    -H "Authorization: Bearer ${HOME_ASSISTANT_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"entity_id\":\"${helper_entity}\",\"value\":\"${test_value}\"}" \
    "${HOME_ASSISTANT_URL}/api/services/input_text/set_value" || true
)"

if [[ "$set_http" != "200" ]]; then
  echo "INPUT_TEXT_DOMAIN_PRESENT=YES INPUT_TEXT_WRITE_TEST=FAIL SERVICES_HTTP=${services_http} STATE_HTTP=${state_http}"
  exit 1
fi

write_verified="NO"
for attempt in 1 2 3; do
  sleep 1

  state_after_http="$(
    curl -s -o "$state_after_file" -w "%{http_code}" \
      -H "Authorization: Bearer ${HOME_ASSISTANT_TOKEN}" \
      "${HOME_ASSISTANT_URL}/api/states/${helper_entity}" || true
  )"

  new_value="$(
    python3 - <<'PY' "$state_after_file"
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    print((data.get("state") or ""))
except Exception:
    print("")
PY
  )"

  if [[ "$state_after_http" == "200" && "$new_value" == "$test_value" ]]; then
    write_verified="YES"
    break
  fi
done

if [[ "$write_verified" != "YES" ]]; then
  echo "INPUT_TEXT_DOMAIN_PRESENT=YES INPUT_TEXT_WRITE_TEST=FAIL SERVICES_HTTP=${services_http} STATE_HTTP=${state_after_http}"
  exit 1
fi

# Best-effort restore to avoid polluting debug helper.
curl -s -o /dev/null -X POST \
  -H "Authorization: Bearer ${HOME_ASSISTANT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"entity_id\":\"${helper_entity}\",\"value\":\"${old_value}\"}" \
  "${HOME_ASSISTANT_URL}/api/services/input_text/set_value" || true

echo "INPUT_TEXT_DOMAIN_PRESENT=YES INPUT_TEXT_WRITE_TEST=PASS SERVICES_HTTP=${services_http} STATE_HTTP=${state_after_http}"
exit 0
