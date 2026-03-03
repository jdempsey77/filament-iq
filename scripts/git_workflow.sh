#!/usr/bin/env bash
# ==============================================================================
# scripts/git_workflow.sh — Cursor-enforced git workflow for Home Assistant repo
#
# Usage:
#   ./scripts/git_workflow.sh start feat|fix <slug>   — Create branch from main
#   ./scripts/git_workflow.sh status                   — Show branch, changes, gate status
#   ./scripts/git_workflow.sh commit "<message>"       — Pre-commit gates → commit
#   ./scripts/git_workflow.sh push                     — Push current branch to origin
#   ./scripts/git_workflow.sh pr ["title"]             — Open PR via gh CLI
#   ./scripts/git_workflow.sh finish ["title"]         — Full gates → push → PR
#   ./scripts/git_workflow.sh sync                     — Rebase on latest main
#   ./scripts/git_workflow.sh abort                    — Abandon branch, return to main
#   ./scripts/git_workflow.sh gate-commit              — Run pre-commit gates only
#   ./scripts/git_workflow.sh gate-merge               — Run pre-merge gates only
# ==============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No color
BOLD='\033[1m'

# --- Helpers ---

log_ok()   { echo -e "${GREEN}✅ $*${NC}"; }
log_fail() { echo -e "${RED}❌ $*${NC}"; }
log_info() { echo -e "${CYAN}ℹ️  $*${NC}"; }
log_warn() { echo -e "${YELLOW}⚠️  $*${NC}"; }
log_step() { echo -e "${BOLD}▶ $*${NC}"; }

current_branch() { git rev-parse --abbrev-ref HEAD; }

is_main() { [[ "$(current_branch)" == "main" ]]; }

is_feature_branch() {
  local branch
  branch="$(current_branch)"
  [[ "$branch" =~ ^(feat|fix)/ ]]
}

validate_slug() {
  local slug="$1"
  if [[ ! "$slug" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]; then
    log_fail "Invalid slug: '$slug'. Must be lowercase, hyphenated, no spaces."
    echo "    Examples: fix-filament-dropdown, add-ams-slot-cards, rewire-sensor"
    return 1
  fi
}

guard_not_main() {
  if is_main; then
    log_fail "Cannot perform this operation on main. Use './scripts/git_workflow.sh start' to create a feature branch."
    exit 1
  fi
}

guard_is_feature() {
  if ! is_feature_branch; then
    log_fail "Current branch '$(current_branch)' is not a feature branch (must start with feat/ or fix/)."
    exit 1
  fi
}

guard_clean_tree() {
  if [[ -n "$(git status --porcelain)" ]]; then
    log_fail "Working tree is dirty. Commit or stash changes first."
    git status --short
    return 1
  fi
}

# --- Gates ---

run_precommit_gates() {
  log_step "Running pre-commit gates..."
  local failed=0

  # Gate 1: Not on main
  if is_main; then
    log_fail "GATE: Cannot commit to main directly"
    failed=1
  else
    log_ok "GATE: On feature branch ($(current_branch))"
  fi

  # Gate 2: serious_mode_check
  log_info "Running serious_mode_check.sh..."
  if ./scripts/serious_mode_check.sh 2>&1; then
    log_ok "GATE: serious_mode_check passed"
  else
    log_fail "GATE: serious_mode_check FAILED"
    failed=1
  fi

  # Gate 3: Has staged changes
  if git diff --cached --quiet 2>/dev/null; then
    log_warn "GATE: No staged changes (nothing to commit)"
    # Not a hard fail — caller may want to stage first
  else
    log_ok "GATE: Staged changes found"
  fi

  return $failed
}

run_premerge_gates() {
  log_step "Running pre-merge gates..."
  local failed=0

  # All pre-commit gates first
  if ! run_precommit_gates; then
    failed=1
  fi

  # Gate: skill_test
  log_info "Running skill_test.sh..."
  if ./scripts/skill_test.sh 2>&1; then
    log_ok "GATE: skill_test passed"
  else
    log_fail "GATE: skill_test FAILED"
    failed=1
  fi

  # Gate: validate_helpers
  log_info "Running validate_helpers.sh..."
  if ./scripts/validate_helpers.sh 2>&1; then
    log_ok "GATE: validate_helpers passed"
  else
    log_fail "GATE: validate_helpers FAILED"
    failed=1
  fi

  return $failed
}

# --- Commands ---

cmd_start() {
  local type="${1:-}"
  local slug="${2:-}"

  if [[ -z "$type" || -z "$slug" ]]; then
    echo "Usage: $0 start <feat|fix> <slug>"
    echo "  Example: $0 start feat add-ams-slot-cards"
    echo "  Example: $0 start fix filament-dropdown"
    exit 1
  fi

  if [[ "$type" != "feat" && "$type" != "fix" ]]; then
    log_fail "Type must be 'feat' or 'fix', got: '$type'"
    exit 1
  fi

  validate_slug "$slug" || exit 1

  local branch="${type}/${slug}"

  # Check if branch already exists
  if git show-ref --verify --quiet "refs/heads/$branch" 2>/dev/null; then
    log_warn "Branch '$branch' already exists."
    echo -n "Switch to it? [y/N] "
    read -r answer
    if [[ "$answer" =~ ^[Yy] ]]; then
      git checkout "$branch"
      log_ok "Switched to existing branch: $branch"
    else
      log_info "Aborted."
    fi
    return
  fi

  # Ensure we're starting from latest main
  log_info "Fetching latest main..."
  git fetch origin main 2>/dev/null || log_warn "Could not fetch origin/main (offline?)"

  log_info "Creating branch '$branch' from main..."
  git checkout main 2>/dev/null || true
  git checkout -b "$branch"

  log_ok "Branch created: $branch"
  log_info "You're ready to work. When done:"
  echo "  1. ./scripts/git_workflow.sh commit \"your message\""
  echo "  2. ./scripts/git_workflow.sh finish"
}

cmd_status() {
  local branch
  branch="$(current_branch)"

  echo -e "${BOLD}Branch:${NC} $branch"

  if is_main; then
    echo -e "${BOLD}Type:${NC}   main (base branch)"
  elif is_feature_branch; then
    echo -e "${BOLD}Type:${NC}   feature branch"
  else
    echo -e "${BOLD}Type:${NC}   ⚠️  non-standard branch name"
  fi

  echo ""

  # Dirty files
  local dirty
  dirty="$(git status --porcelain)"
  if [[ -n "$dirty" ]]; then
    echo -e "${BOLD}Changes:${NC}"
    git status --short
  else
    echo -e "${BOLD}Changes:${NC} clean"
  fi

  echo ""

  # Commits ahead of main
  if ! is_main; then
    local ahead
    ahead="$(git rev-list --count main..HEAD 2>/dev/null || echo '?')"
    echo -e "${BOLD}Commits ahead of main:${NC} $ahead"
  fi

  echo ""

  # Changed files vs main
  if ! is_main; then
    echo -e "${BOLD}Files changed vs main:${NC}"
    git diff --stat main...HEAD 2>/dev/null || echo "  (could not compare)"
  fi
}

cmd_commit() {
  local message="${1:-}"

  if [[ -z "$message" ]]; then
    echo "Usage: $0 commit \"your commit message\""
    exit 1
  fi

  guard_not_main

  # Stage all changes
  git add -A

  # Check for something to commit
  if git diff --cached --quiet 2>/dev/null; then
    log_warn "Nothing to commit (no changes staged)."
    return
  fi

  # Run pre-commit gates
  if ! run_precommit_gates; then
    log_fail "Pre-commit gates FAILED. Fix issues and try again."
    exit 1
  fi

  # Commit with trailer
  git commit --trailer "Made-with: Cursor" -m "$message"
  log_ok "Committed: $message"
}

cmd_push() {
  guard_not_main
  guard_is_feature

  local branch
  branch="$(current_branch)"

  log_info "Pushing '$branch' to origin..."
  git push -u origin "$branch"
  log_ok "Pushed: $branch"
}

cmd_pr() {
  guard_not_main
  guard_is_feature

  local branch title
  branch="$(current_branch)"
  title="${1:-$branch}"

  # Ensure pushed
  log_info "Ensuring branch is pushed..."
  git push -u origin "$branch" 2>/dev/null || true

  # Check gh is available
  if ! command -v gh &>/dev/null; then
    log_fail "'gh' CLI not found. Install it: https://cli.github.com/"
    echo "    Or push manually and open PR in browser."
    exit 1
  fi

  # Check if PR already exists
  local existing_pr
  existing_pr="$(gh pr list --head "$branch" --json number --jq '.[0].number' 2>/dev/null || echo '')"
  if [[ -n "$existing_pr" && "$existing_pr" != "null" ]]; then
    log_warn "PR #$existing_pr already exists for '$branch'."
    gh pr view "$existing_pr" --web 2>/dev/null || true
    return
  fi

  # Build PR body
  local body
  body="## Changes\n\n"
  body+="$(git log main..HEAD --oneline 2>/dev/null || echo 'Could not determine commits')\n\n"
  body+="## Files changed\n\n"
  body+="\`\`\`\n$(git diff --stat main...HEAD 2>/dev/null || echo 'n/a')\n\`\`\`\n\n"
  body+="## Gates\n\n"
  body+="- [ ] serious_mode_check.sh\n"
  body+="- [ ] skill_test.sh\n"
  body+="- [ ] validate_helpers.sh\n"
  body+="- [ ] Stage deploy verified\n\n"
  body+="_Created by Cursor git workflow_"

  log_info "Creating PR: '$title'..."
  gh pr create \
    --base main \
    --head "$branch" \
    --title "$title" \
    --body "$(echo -e "$body")"

  log_ok "PR created for '$branch'"
}

cmd_finish() {
  guard_not_main
  guard_is_feature

  local branch title
  branch="$(current_branch)"
  title="${1:-$branch}"

  log_step "=== FINISH: $branch ==="
  echo ""

  # 1. Stage and commit any pending changes
  if [[ -n "$(git status --porcelain)" ]]; then
    log_info "Staging pending changes..."
    git add -A
    if ! git diff --cached --quiet 2>/dev/null; then
      log_info "Committing pending changes..."
      if ! run_precommit_gates; then
        log_fail "Pre-commit gates FAILED. Fix and re-run finish."
        exit 1
      fi
      git commit --trailer "Made-with: Cursor" -m "Final changes before PR"
    fi
  fi

  # 2. Run pre-merge gates
  echo ""
  if ! run_premerge_gates; then
    log_fail "Pre-merge gates FAILED. Fix issues and re-run finish."
    exit 1
  fi

  echo ""

  # 3. Push
  log_info "Pushing to origin..."
  git push -u origin "$branch"

  # 4. Create PR
  cmd_pr "$title"

  echo ""
  log_ok "=== FINISH COMPLETE ==="
  log_info "PR is ready for review. After approval, squash-merge in GitHub."
}

cmd_sync() {
  guard_not_main
  guard_is_feature

  local branch
  branch="$(current_branch)"

  log_info "Fetching latest main..."
  git fetch origin main

  log_info "Rebasing '$branch' on main..."
  if git rebase origin/main; then
    log_ok "Rebased successfully."
  else
    log_fail "Rebase had conflicts. Resolve them, then 'git rebase --continue'."
    exit 1
  fi
}

cmd_abort() {
  guard_not_main

  local branch
  branch="$(current_branch)"

  echo -e "${YELLOW}This will:"
  echo "  1. Discard all uncommitted changes"
  echo "  2. Switch back to main"
  echo -e "  3. Delete local branch '$branch'${NC}"
  echo ""
  echo -n "Are you sure? [y/N] "
  read -r answer

  if [[ ! "$answer" =~ ^[Yy] ]]; then
    log_info "Aborted."
    return
  fi

  git checkout -- . 2>/dev/null || true
  git clean -fd 2>/dev/null || true
  git checkout main
  git branch -D "$branch" 2>/dev/null || true
  log_ok "Abandoned branch '$branch'. Back on main."
}

# --- Main ---

command="${1:-}"
shift || true

case "$command" in
  start)       cmd_start "$@" ;;
  status)      cmd_status ;;
  commit)      cmd_commit "$@" ;;
  push)        cmd_push ;;
  pr)          cmd_pr "$@" ;;
  finish)      cmd_finish "$@" ;;
  sync)        cmd_sync ;;
  abort)       cmd_abort ;;
  gate-commit) run_precommit_gates ;;
  gate-merge)  run_premerge_gates ;;
  *)
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  start <feat|fix> <slug>   Create branch from main"
    echo "  status                    Show branch, changes, gate status"
    echo "  commit \"<message>\"        Pre-commit gates → commit"
    echo "  push                      Push current branch to origin"
    echo "  pr [\"title\"]              Open PR via gh CLI"
    echo "  finish [\"title\"]          Full gates → push → PR"
    echo "  sync                      Rebase on latest main"
    echo "  abort                     Abandon branch, return to main"
    echo "  gate-commit               Run pre-commit gates only"
    echo "  gate-merge                Run pre-merge gates only"
    exit 1
    ;;
esac
