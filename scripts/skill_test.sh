#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# skill_test.sh (bash 3.2 compatible)
# Deterministic repo validation entrypoint:
#   - repo sanity checks (branch, clean tree unless overridden)
#   - preflight scripts (helpers/integrations)
#   - unit tests (pytest if present)
#   - phase gates (ALL gates discovered)
# Produces:
#   - logs under ./.artifacts/skill/<timestamp>/
#   - PASS/FAIL checklist summary
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TS="$(date +"%Y%m%d_%H%M%S")"
ART_ROOT="${SKILL_ARTIFACTS_DIR:-$REPO_ROOT/.artifacts/skill/$TS}"
LOG_DIR="$ART_ROOT/logs"
mkdir -p "$LOG_DIR"

declare -a PASS_STEPS=()
declare -a FAIL_STEPS=()

log() { printf '%s\n' "$*" | tee -a "$LOG_DIR/skill_test.log"; }
hr()  { log "--------------------------------------------------------------------------------"; }

run_step() {
  # run_step "Human name" "command string"
  local name="$1"
  local cmd="$2"
  local out="$LOG_DIR/$(echo "$name" | tr ' /' '__').out"

  hr
  log "STEP: $name"
  log "CMD : $cmd"
  set +e
  bash -lc "$cmd" >"$out" 2>&1
  local rc=$?
  set -e

  if [[ $rc -eq 0 ]]; then
    PASS_STEPS+=("$name")
    log "RESULT: PASS ($name)"
  else
    FAIL_STEPS+=("$name (rc=$rc, log=$out)")
    log "RESULT: FAIL ($name) rc=$rc"
    log "LOG   : $out"
  fi
  return $rc
}

cd "$REPO_ROOT"

# ==============================================================================
# 0) Repo sanity checks
# ==============================================================================
run_step "git: repo present" "git rev-parse --is-inside-work-tree"
run_step "git: status snapshot" "git status --porcelain=v1 > '$LOG_DIR/git_status_porcelain.txt' && git status -sb"

if [[ "${SKIP_GIT_CLEAN:-0}" != "1" ]]; then
  if [[ -n "$(git status --porcelain=v1)" ]]; then
    hr
    log "STATUS: FAIL"
    log "Reason: working tree is not clean."
    log "Fix   : commit/stash changes, or re-run with SKIP_GIT_CLEAN=1"
    log "Git status saved to: $LOG_DIR/git_status_porcelain.txt"
    exit 2
  fi
else
  log "NOTE: SKIP_GIT_CLEAN=1 set; continuing with dirty tree."
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")"
SHA="$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")"
log "INFO: branch=$BRANCH sha=$SHA"
log "INFO: artifacts=$ART_ROOT"

# ==============================================================================
# 1) Preflights (canonical list — adjust as your repo standardizes)
# ==============================================================================
PREFLIGHTS=(
  "./scripts/preflight_input_text_yaml_limits.sh"
  "./scripts/preflight_template_filters.sh"
  "./scripts/preflight_input_text_services.sh"
  "./scripts/preflight_input_text.sh"
  "./scripts/preflight_spoolman_filament_dropdown.sh"
  "./scripts/preflight_ams_matching.sh"
  "./scripts/preflight_spoolman_location_update.sh"
  "./scripts/preflight_spoolman_uuid_present.sh"
  "./scripts/preflight_canonicalizer.sh"
  "./scripts/preflight_helpers.sh"
  "./scripts/validate_helpers.sh"
)

log ""
log "DISCOVERY: Preflights (configured list):"
for p in "${PREFLIGHTS[@]}"; do
  log "  - $p"
done

for p in "${PREFLIGHTS[@]}"; do
  if [[ -x "$REPO_ROOT/${p#./}" ]]; then
    run_step "preflight: $p" "$p" || true
  elif [[ -f "$REPO_ROOT/${p#./}" ]]; then
    run_step "preflight: $p" "bash '$p'" || true
  else
    log "SKIP: preflight not found: $p"
  fi
done

# ==============================================================================
# 2) Unit tests (pytest if present)
# ==============================================================================
if [[ -f "$REPO_ROOT/pytest.ini" || -d "$REPO_ROOT/tests" ]]; then
  if [[ -n "${VENV_ACTIVATE:-}" && -f "${VENV_ACTIVATE:-}" ]]; then
    # Prefer explicit venv if provided
    run_step "unit: pytest (venv)" "source '${VENV_ACTIVATE}' && python -m pytest -q" || true
  else
    # macOS often has no 'python' shim; prefer python3 and only run if pytest is installed
    if command -v python3 >/dev/null 2>&1; then
      if python3 -c "import pytest" >/dev/null 2>&1; then
        run_step "unit: pytest" "python3 -m pytest -q" || true
      else
        log "SKIP: pytest not installed for python3; skipping unit tests."
      fi
    else
      log "SKIP: python3 not found; skipping unit tests."
    fi
  fi
else
  log "SKIP: pytest not configured (no pytest.ini and no ./tests directory)."
fi

# ==============================================================================
# 3) Phase gates (ALL gates discovered) — bash 3.2 compatible (no mapfile)
# ==============================================================================
GATE_DIR="$REPO_ROOT/scripts"
GATE_GLOB="${GATE_GLOB:-gate_*.sh}"

GATES=()
if ls "$GATE_DIR"/$GATE_GLOB >/dev/null 2>&1; then
  # shellcheck disable=SC2206
  GATES=( $(cd "$GATE_DIR" && ls -1 $GATE_GLOB 2>/dev/null | sort) )
fi

log ""
log "DISCOVERY: Phase gates (pattern scripts/$GATE_GLOB):"
if [[ ${#GATES[@]} -eq 0 ]]; then
  log "  (none found)"
else
  for g in "${GATES[@]}"; do
    log "  - ./scripts/$g"
  done
fi

if [[ ${#GATES[@]} -gt 0 ]]; then
  for g in "${GATES[@]}"; do
    local_path="./scripts/$g"
    if [[ -x "$REPO_ROOT/scripts/$g" ]]; then
      run_step "gate: $g" "$local_path" || true
    else
      run_step "gate: $g" "bash '$local_path'" || true
    fi
  done
fi


# ==============================================================================
# AMS Physical Truth Validation (runtime invariants)
# ==============================================================================
if [[ -x ./scripts/validate_ams.sh ]]; then
  run_step "ams: physical truth validation" "./scripts/validate_ams.sh"
else
  log "SKIP: validate_ams.sh not found or not executable."
fi

# ==============================================================================
# 4) Final summary + exit code
# ==============================================================================

hr
if [[ ${#FAIL_STEPS[@]} -eq 0 ]]; then
  log "STATUS: PASS ✅"
else
  log "STATUS: FAIL ❌"
fi
log "BRANCH: $BRANCH"
log "SHA   : $SHA"
log "LOGS  : $LOG_DIR"
log ""

log "CHECKLIST:"
if [[ ${#PASS_STEPS[@]} -gt 0 ]]; then
  for s in "${PASS_STEPS[@]}"; do log "  ✅ $s"; done
fi
if [[ ${#FAIL_STEPS[@]} -gt 0 ]]; then
  for s in "${FAIL_STEPS[@]}"; do log "  ❌ $s"; done
fi

log ""
log "ARTIFACT INDEX:"
log "  - $LOG_DIR/skill_test.log"
log "  - $LOG_DIR/git_status_porcelain.txt"
log ""

if [[ ${#FAIL_STEPS[@]} -eq 0 ]]; then
  exit 0
else
  exit 1
fi
