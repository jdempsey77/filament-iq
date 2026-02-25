#!/usr/bin/env bash
set -euo pipefail

BASE_BRANCH_DEFAULT="main"
MANAGE_HA="${MANAGE_HA:-./scripts/manage_ha.sh}"
BASE_BRANCH="${BASE_BRANCH:-$BASE_BRANCH_DEFAULT}"
base_ref="origin/${BASE_BRANCH}"

fail() {
  echo "LIGHT_DEPLOY: FAIL — $1" >&2
  echo
  echo "STATUS: FAIL"
  echo "COMMANDS RUN:"
  echo "  (aborted)"
  echo "RELOAD ACTIONS:"
  echo "  none"
  echo "VALIDATION RESULTS:"
  echo "  failed"
  echo "NEXT ACTION:"
  echo "  Use DEPLOY (restart required) or fix above error."
  exit 1
}

note() { echo "LIGHT_DEPLOY: $*"; }
require_env() { [[ -n "${!1:-}" ]] || fail "Missing env var: $1"; }

require_env HOME_ASSISTANT_URL
require_env HOME_ASSISTANT_TOKEN

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "Not in git repo"
git fetch -q origin "${BASE_BRANCH}" || true

if [[ "${LIGHT_DEPLOY_LOCAL:-0}" == "1" ]]; then
  note "Local mode: HEAD~1..HEAD"
  mapfile -t changed < <(git diff --name-only HEAD~1..HEAD)
else
  note "Branch mode: ${base_ref}..."
  mapfile -t changed < <(git diff --name-only "${base_ref}...")
fi

if [[ "${#changed[@]}" -eq 0 ]]; then
  echo "STATUS: PASS"
  echo "COMMANDS RUN:"
  echo "  none"
  echo "RELOAD ACTIONS:"
  echo "  none"
  echo "VALIDATION RESULTS:"
  echo "  no changes detected"
  echo "NEXT ACTION:"
  echo "  none"
  exit 0
fi

note "Changed files:"
printf '  - %s\n' "${changed[@]}"

changed_json="$(python3 - <<'PY'
import json, os, subprocess
if os.environ.get("LIGHT_DEPLOY_LOCAL")=="1":
    files = subprocess.check_output(["git","diff","--name-only","HEAD~1..HEAD"], text=True).splitlines()
else:
    base = os.environ.get("BASE_BRANCH","main")
    files = subprocess.check_output(["git","diff","--name-only",f"origin/{base}..."], text=True).splitlines()
print(json.dumps([f for f in files if f.strip()]))
PY
)"

classification="$(./scripts/classify_light_deploy_changes.py --base-ref "${base_ref}" --changed-files-json "${changed_json}")"
note "Classification:"
echo "${classification}" | sed 's/^/  /'

reloadables="$(echo "${classification}" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("\n".join(d["reloadables"]))')"
requires_restart="$(echo "${classification}" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("\n".join(d["requires_restart"]))')"

if [[ -n "${requires_restart}" ]]; then
  echo "LIGHT_DEPLOY: REFUSE — restart-required changes detected:" >&2
  echo "${requires_restart}" | sed 's/^/  - /' >&2
  fail "Restart-required change detected"
fi

[[ -n "${reloadables}" ]] || fail "No reloadable changes detected"

push_flags=()
while IFS= read -r f; do
  case "$f" in
    automations.yaml) push_flags+=("--automations") ;;
    scripts.yaml) push_flags+=("--scripts") ;;
    configuration.yaml) push_flags+=("--config") ;;
    *) fail "Unexpected reloadable file: $f" ;;
  esac
done <<< "${reloadables}"

dedup_flags=()
for f in "${push_flags[@]}"; do
  [[ " ${dedup_flags[*]} " == *" ${f} "* ]] || dedup_flags+=("${f}")
done

[[ -x "${MANAGE_HA}" ]] || fail "manage_ha.sh not executable: ${MANAGE_HA}"
note "Pushing changes: ${MANAGE_HA} ${dedup_flags[*]}"
"${MANAGE_HA}" "${dedup_flags[@]}"

ha_post() {
  local domain="$1" service="$2"
  curl -sS -X POST \
    -H "Authorization: Bearer ${HOME_ASSISTANT_TOKEN}" \
    -H "Content-Type: application/json" \
    "${HOME_ASSISTANT_URL}/api/services/${domain}/${service}" \
    -d '{}' >/dev/null
}

ha_get() {
  curl -sS -H "Authorization: Bearer ${HOME_ASSISTANT_TOKEN}" "${HOME_ASSISTANT_URL}$1"
}

services_json="$(ha_get /api/services)"
echo "${services_json}" | jq . >/dev/null 2>&1 || fail "HA /api/services not valid JSON"

service_exists() {
  local domain="$1" service="$2"
  echo "${services_json}" | jq -e --arg d "$domain" --arg s "$service" '
    any(.[]; .domain==$d and any(.services|keys[]; .==$s))
  ' >/dev/null
}

reload_actions=""

while IFS= read -r f; do
  case "$f" in
    automations.yaml)
      service_exists automation reload || fail "Missing service: automation.reload"
      ha_post automation reload
      reload_actions+="automation.reload "
      ;;
    scripts.yaml)
      service_exists script reload || fail "Missing service: script.reload"
      ha_post script reload
      reload_actions+="script.reload "
      ;;
    configuration.yaml)
      service_exists homeassistant reload_core_config || fail "Missing service: homeassistant.reload_core_config"
      ha_post homeassistant reload_core_config
      reload_actions+="homeassistant.reload_core_config "
      ;;
  esac
done <<< "${reloadables}"

validation_pass="yes"

if [[ -x ./scripts/validate_helpers.sh ]]; then
  if ! ./scripts/validate_helpers.sh; then
    validation_pass="no"
  fi
fi

if ha_get /api/error_log >/dev/null 2>&1; then
  errlog="$(ha_get /api/error_log || true)"
  if echo "${errlog}" | grep -q "TemplateAssertionError"; then
    validation_pass="no"
  fi
fi

[[ "$validation_pass" == "yes" ]] || fail "Validation failed after reload"

echo
echo "STATUS: PASS"
echo "COMMANDS RUN:"
echo "  ${MANAGE_HA} ${dedup_flags[*]}"
echo "RELOAD ACTIONS:"
echo "  ${reload_actions:-none}"
echo "VALIDATION RESULTS:"
echo "  helpers + template scan passed"
echo "NEXT ACTION:"
echo "  none"

exit 0
