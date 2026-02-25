#!/usr/bin/env bash
set -euo pipefail

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
# 2) Determine change set base ref
# ------------------------------------------------------------------------------
BASE_REF=""
if git rev-parse --verify --quiet "origin/$BRANCH" >/dev/null; then
  BASE_REF="origin/$BRANCH"
else
  BASE_REF="HEAD~1"
fi

CHANGED_FILE_LIST="$LOG_DIR/changed_files.txt"
git diff --name-only "$BASE_REF"...HEAD | tee "$CHANGED_FILE_LIST" >/dev/null || true

hr
log "STEP: change detection"
log "BASE_REF: $BASE_REF"
log "Changed files saved to: $CHANGED_FILE_LIST"

# ------------------------------------------------------------------------------
# 3) Decide deploy targets (your manage_ha.sh flags)
# ------------------------------------------------------------------------------
NEEDS_APPDAEMON=0
NEEDS_AUTOMATIONS=0
NEEDS_GO2RTC=0
NEEDS_SCRIPTS_ONLY=0
NEEDS_CONFIG=0
NEEDS_ALL=0

# If lots changed, prefer --all (safer, restarts HA).
CHANGED_COUNT="$(wc -l <"$CHANGED_FILE_LIST" 2>/dev/null | tr -d ' ' || echo 0)"

while IFS= read -r f; do
  [[ -z "$f" ]] && continue

  # AppDaemon
  if [[ "$f" == appdaemon/* || "$f" == */appdaemon/* ]]; then
    NEEDS_APPDAEMON=1
  fi

  # Automations
  if [[ "$f" == "automations.yaml" ]]; then
    NEEDS_AUTOMATIONS=1
  fi

  # go2rtc
  if [[ "$f" == "go2rtc.yaml" ]]; then
    NEEDS_GO2RTC=1
  fi

  # scripts.yaml only changes can be narrow
  if [[ "$f" == "scripts.yaml" ]]; then
    NEEDS_SCRIPTS_ONLY=1
  fi

  # General HA config bundle
  if [[ "$f" == "configuration.yaml" || "$f" == "scenes.yaml" || "$f" == "secrets.yaml" || "$f" == "templates.yaml" || "$f" == "ui-lovelace.yaml" || "$f" == dashboards/* || "$f" == lovelace/* ]]; then
    NEEDS_CONFIG=1
  fi

  # Deploy tooling changes: doesn't necessarily require HA deploy, but does affect workflow
  if [[ "$f" == scripts/* ]]; then
    : # no-op; keep for future heuristics
  fi
done < "$CHANGED_FILE_LIST"

# Heuristic: if more than 6 files changed and includes any HA config areas, use --all
if [[ "$CHANGED_COUNT" -ge 7 && ( "$NEEDS_CONFIG" -eq 1 || "$NEEDS_AUTOMATIONS" -eq 1 || "$NEEDS_GO2RTC" -eq 1 ) ]]; then
  NEEDS_ALL=1
fi

# If configuration.yaml changed, config deploy is required; if automations/go2rtc changed too,
# --all is cleaner and ensures restart.
if [[ "$NEEDS_CONFIG" -eq 1 && ( "$NEEDS_AUTOMATIONS" -eq 1 || "$NEEDS_GO2RTC" -eq 1 ) ]]; then
  NEEDS_ALL=1
fi

HA_TARGETS=()
if [[ "$NEEDS_ALL" -eq 1 ]]; then
  HA_TARGETS+=("--all")
else
  # Prefer minimal targets when safe
  # If config changed, use --config (covers included files like scripts.yaml, scenes.yaml)
  if [[ "$NEEDS_CONFIG" -eq 1 ]]; then
    HA_TARGETS+=("--config")
  else
    [[ "$NEEDS_SCRIPTS_ONLY" -eq 1 ]] && HA_TARGETS+=("--scripts")
  fi
  [[ "$NEEDS_AUTOMATIONS" -eq 1 ]] && HA_TARGETS+=("--automations")
  [[ "$NEEDS_GO2RTC" -eq 1 ]] && HA_TARGETS+=("--go2rtc")
fi

# Deduplicate HA targets (bash-safe)
DEDUPED=()
for t in "${HA_TARGETS[@]}"; do
  skip=0
  for u in "${DEDUPED[@]}"; do
    [[ "$t" == "$u" ]] && skip=1
  done
  [[ $skip -eq 0 ]] && DEDUPED+=("$t")
done
HA_TARGETS=("${DEDUPED[@]}")

hr
log "DECISION:"
log "  HA targets     : ${HA_TARGETS[*]:-(none)}"
log "  AppDaemon deploy: $NEEDS_APPDAEMON"

# ------------------------------------------------------------------------------
# 4) Write deployment record header
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
# 7) Post-deploy verification (minimal but real)
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
