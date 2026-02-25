#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# skill_deploy.sh (bash 3.2 compatible)
# Deterministic deploy entrypoint:
#   - requires clean working tree (unless SKIP_GIT_CLEAN=1)
#   - runs ./scripts/skill_test.sh (must PASS)
#   - detects change type and deploys using ./scripts/manage_ha.sh only
#   - runs post-deploy verification scripts (if present)
#   - produces a deployment record under ./.artifacts/skill/<ts>/
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TS="$(date +"%Y%m%d_%H%M%S")"
ART_ROOT="${SKILL_ARTIFACTS_DIR:-$REPO_ROOT/.artifacts/skill/$TS}"
LOG_DIR="$ART_ROOT/logs"
mkdir -p "$LOG_DIR"

log() { printf '%s\n' "$*" | tee -a "$LOG_DIR/skill_deploy.log"; }
hr()  { log "--------------------------------------------------------------------------------"; }

cd "$REPO_ROOT"

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")"
SHA="$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")"

hr
log "DEPLOY: starting"
log "INFO: branch=$BRANCH sha=$SHA"
log "INFO: artifacts=$ART_ROOT"
log "INFO: logs=$LOG_DIR"

# ------------------------------------------------------------------------------
# 0) Preconditions: clean tree unless explicitly overridden
# ------------------------------------------------------------------------------
if [[ "${SKIP_GIT_CLEAN:-0}" != "1" ]]; then
  if [[ -n "$(git status --porcelain=v1)" ]]; then
    hr
    log "STATUS: FAIL"
    log "Reason: working tree is not clean."
    log "Fix   : commit/stash changes, then rerun."
    log "Note  : You can override with SKIP_GIT_CLEAN=1 (not recommended for real deploys)."
    exit 2
  fi
else
  log "NOTE: SKIP_GIT_CLEAN=1 set; continuing with dirty tree (risky)."
fi

# ------------------------------------------------------------------------------
# 1) TEST must pass
# ------------------------------------------------------------------------------
hr
log "STEP: TEST (./scripts/skill_test.sh)"
set +e
./scripts/skill_test.sh >"$LOG_DIR/skill_test_stdout.out" 2>&1
TEST_RC=$?
set -e

if [[ $TEST_RC -ne 0 ]]; then
  hr
  log "STATUS: FAIL"
  log "Reason: TEST failed (rc=$TEST_RC). Deployment aborted."
  log "See   : $LOG_DIR/skill_test_stdout.out"
  exit 1
fi
log "RESULT: TEST PASS ✅"

# ------------------------------------------------------------------------------
# 2) Determine base ref for change detection
# ------------------------------------------------------------------------------
BASE_REF=""
if git rev-parse --verify --quiet "origin/$BRANCH" >/dev/null; then
  BASE_REF="origin/$BRANCH"
else
  BASE_REF="HEAD~1"
fi

CHANGED_FILE_LIST="$LOG_DIR/changed_files.txt"
git diff --name-only "$BASE_REF"...HEAD >"$CHANGED_FILE_LIST" 2>/dev/null || true

hr
log "STEP: change detection"
log "BASE_REF: $BASE_REF"
log "Changed files saved to: $CHANGED_FILE_LIST"

# ------------------------------------------------------------------------------
# 3) Decide deploy targets (aligned to manage_ha.sh --help)
# ------------------------------------------------------------------------------
NEEDS_APPDAEMON=0
NEEDS_AUTOMATIONS=0
NEEDS_GO2RTC=0
NEEDS_SCRIPTS_ONLY=0
NEEDS_CONFIG=0
NEEDS_ALL=0

CHANGED_COUNT="$(wc -l <"$CHANGED_FILE_LIST" 2>/dev/null | tr -d ' ' || echo 0)"

# Read changed files line-by-line (bash 3.2 safe)
while IFS= read -r f; do
  [[ -z "$f" ]] && continue

  # AppDaemon
  if [[ "$f" == appdaemon/* || "$f" == */appdaemon/* ]]; then
    NEEDS_APPDAEMON=1
  fi

  # Automations / go2rtc
  [[ "$f" == "automations.yaml" ]] && NEEDS_AUTOMATIONS=1
  [[ "$f" == "go2rtc.yaml" ]] && NEEDS_GO2RTC=1

  # scripts.yaml only (narrow)
  [[ "$f" == "scripts.yaml" ]] && NEEDS_SCRIPTS_ONLY=1

  # General HA config bundle
  case "$f" in
    configuration.yaml|scenes.yaml|secrets.yaml|templates.yaml|ui-lovelace.yaml)
      NEEDS_CONFIG=1
      ;;
    dashboards/*|lovelace/*)
      NEEDS_CONFIG=1
      ;;
  esac
done < "$CHANGED_FILE_LIST"

# Heuristics: if multiple HA areas changed, prefer --all (safer, includes restart)
if [[ "$NEEDS_CONFIG" -eq 1 && ( "$NEEDS_AUTOMATIONS" -eq 1 || "$NEEDS_GO2RTC" -eq 1 ) ]]; then
  NEEDS_ALL=1
fi
if [[ "$CHANGED_COUNT" -ge 7 && ( "$NEEDS_CONFIG" -eq 1 || "$NEEDS_AUTOMATIONS" -eq 1 || "$NEEDS_GO2RTC" -eq 1 ) ]]; then
  NEEDS_ALL=1
fi

HA_TARGETS=()

if [[ "$NEEDS_ALL" -eq 1 ]]; then
  HA_TARGETS+=("--all")
else
  if [[ "$NEEDS_CONFIG" -eq 1 ]]; then
    HA_TARGETS+=("--config")
  else
    [[ "$NEEDS_SCRIPTS_ONLY" -eq 1 ]] && HA_TARGETS+=("--scripts")
  fi
  [[ "$NEEDS_AUTOMATIONS" -eq 1 ]] && HA_TARGETS+=("--automations")
  [[ "$NEEDS_GO2RTC" -eq 1 ]] && HA_TARGETS+=("--go2rtc")
fi

# Deduplicate HA targets (bash 3.2)
DEDUPED=()
for t in "${HA_TARGETS[@]}"; do
  found=0
  for u in "${DEDUPED[@]}"; do
    [[ "$t" == "$u" ]] && found=1
  done
  [[ $found -eq 0 ]] && DEDUPED+=("$t")
done
HA_TARGETS=("${DEDUPED[@]}")

hr
log "DECISION:"
log "  HA targets      : ${HA_TARGETS[*]:-(none)}"
log "  AppDaemon deploy: $NEEDS_APPDAEMON"

# ------------------------------------------------------------------------------
# 4) Deployment record
# ------------------------------------------------------------------------------
DEPLOY_RECORD="$ART_ROOT/deploy_record.txt"
{
  echo "DEPLOY RECORD"
  echo "Timestamp : $TS"
  echo "Branch    : $BRANCH"
  echo "SHA       : $SHA"
  echo "BaseRef   : $BASE_REF"
  echo "HA Targets: ${HA_TARGETS[*]:-(none)}"
  echo "AppDaemon : $NEEDS_APPDAEMON"
  echo "Artifacts : $ART_ROOT"
  echo ""
  echo "Changed files:"
  sed 's/^/  - /' "$CHANGED_FILE_LIST" 2>/dev/null || true
} > "$DEPLOY_RECORD"

# ------------------------------------------------------------------------------
# 5) Deploy HA targets first
# ------------------------------------------------------------------------------
for t in "${HA_TARGETS[@]}"; do
  hr
  log "STEP: deploy HA $t"
  log "CMD : ./scripts/manage_ha.sh $t"
  set +e
  ./scripts/manage_ha.sh "$t" >"$LOG_DIR/manage_ha_${t#--}.out" 2>&1
  RC=$?
  set -e
  if [[ $RC -ne 0 ]]; then
    hr
    log "STATUS: FAIL"
    log "Reason: manage_ha.sh $t failed (rc=$RC)"
    log "See   : $LOG_DIR/manage_ha_${t#--}.out"
    log "ROLLBACK HINT: revert commit(s) and rerun DEPLOY, or redeploy last known-good SHA."
    exit 1
  fi
  log "RESULT: deploy HA $t PASS"
done

# ------------------------------------------------------------------------------
# 6) Deploy AppDaemon if needed
# ------------------------------------------------------------------------------
if [[ "$NEEDS_APPDAEMON" -eq 1 ]]; then
  hr
  log "STEP: deploy AppDaemon --appdaemon"
  log "CMD : ./scripts/manage_ha.sh --appdaemon"
  set +e
  ./scripts/manage_ha.sh --appdaemon >"$LOG_DIR/manage_ha_appdaemon.out" 2>&1
  RC=$?
  set -e
  if [[ $RC -ne 0 ]]; then
    hr
    log "STATUS: FAIL"
    log "Reason: manage_ha.sh --appdaemon failed (rc=$RC)"
    log "See   : $LOG_DIR/manage_ha_appdaemon.out"
    log "ROLLBACK HINT: revert commit(s) and rerun DEPLOY, or redeploy last known-good SHA."
    exit 1
  fi
  log "RESULT: deploy AppDaemon PASS"
fi

# ------------------------------------------------------------------------------
# 7) Post-deploy verification (run if present)
# ------------------------------------------------------------------------------
hr
log "STEP: post-deploy verification"

POST_FAIL=0
POST_VERIFY=(
  "./scripts/validate_helpers.sh"
  "./scripts/gate_phase0_rfid_regression.sh"
)

for v in "${POST_VERIFY[@]}"; do
  if [[ -f "$REPO_ROOT/${v#./}" ]]; then
    log "VERIFY: $v"
    set +e
    bash -lc "$v" >"$LOG_DIR/post_verify_$(basename "$v").out" 2>&1
    RC=$?
    set -e
    if [[ $RC -ne 0 ]]; then
      log "VERIFY RESULT: FAIL ($v) rc=$RC"
      log "See          : $LOG_DIR/post_verify_$(basename "$v").out"
      POST_FAIL=1
    else
      log "VERIFY RESULT: PASS ($v)"
    fi
  else
    log "SKIP: verify script not found: $v"
  fi
done

# ------------------------------------------------------------------------------
# 8) Final summary
# ------------------------------------------------------------------------------
hr
if [[ $POST_FAIL -eq 0 ]]; then
  log "STATUS: PASS ✅"
else
  log "STATUS: FAIL ❌"
  log "Reason: post-deploy verification failed."
  log "NEXT  : run ROLLBACK procedure or fix-forward, then rerun DEPLOY."
fi

log "DEPLOYMENT RECORD: $DEPLOY_RECORD"
log "LOGS            : $LOG_DIR"

exit $POST_FAIL
