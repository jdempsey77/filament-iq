#!/bin/bash
#
# Unified Home Assistant Workflow
# Validates → Deploys → Commits → Pushes (optional)
#
# Usage:
#   ./scripts/workflow.sh --config "commit message"
#   ./scripts/workflow.sh --automations "commit message"
#   ./scripts/workflow.sh --scripts "commit message"
#   ./scripts/workflow.sh --all "commit message"
#   ./scripts/workflow.sh --stage "commit message"  (dashboard only)
#
# Flags:
#   --no-commit     Skip git commit
#   --no-push       Skip git push (commit only)
#   --validate      Validate HA config before deploy
#   --restart       Restart HA after deploy
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${BLUE}ℹ${NC} $1"; }
log_success() { echo -e "${GREEN}✓${NC} $1"; }
log_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
log_error() { echo -e "${RED}✗${NC} $1"; }
log_step() { echo -e "${CYAN}▸${NC} $1"; }

# Parse arguments
DEPLOY_TARGET=""
COMMIT_MSG=""
DO_COMMIT=1
DO_PUSH=1
DO_VALIDATE=0
DO_RESTART=0

for arg in "$@"; do
    case "$arg" in
        --config|--automations|--scripts|--all|--stage)
            DEPLOY_TARGET="$arg"
            ;;
        --no-commit)
            DO_COMMIT=0
            ;;
        --no-push)
            DO_PUSH=0
            ;;
        --validate)
            DO_VALIDATE=1
            ;;
        --restart)
            DO_RESTART=1
            ;;
        --help|-h)
            cat << EOF
Unified Home Assistant Workflow

USAGE:
    $0 <target> "commit message" [flags]

TARGETS:
    --config       Deploy configuration.yaml + scripts.yaml
    --automations  Deploy automations.yaml
    --scripts      Deploy scripts.yaml only
    --all          Deploy all config (config + automations + restart)
    --stage        Deploy stage dashboard

FLAGS:
    --no-commit    Skip git commit
    --no-push      Skip git push (commit only)
    --validate     Validate HA config before deploy
    --restart      Restart HA after deploy

EXAMPLES:
    # Full workflow: validate → deploy → commit → push
    $0 --automations "feat: Add Step 1 safety fixes" --validate

    # Deploy + commit only (no push)
    $0 --config "fix: Update helpers" --no-push

    # Deploy only (no git)
    $0 --scripts "test: Trying new script" --no-commit

    # Full config deployment with restart
    $0 --all "feat: Major update" --validate --restart

WORKFLOW:
    1. ✓ Validate YAML syntax (always)
    2. ✓ Deploy to Home Assistant via manage_ha.sh
    3. ✓ Validate HA config (if --validate)
    4. ✓ Restart HA (if --restart)
    5. ✓ Git commit (unless --no-commit)
    6. ✓ Git push (unless --no-push)
EOF
            exit 0
            ;;
        -*)
            # Skip flags already handled
            ;;
        *)
            # Assume it's the commit message
            if [[ -z "$COMMIT_MSG" ]]; then
                COMMIT_MSG="$arg"
            fi
            ;;
    esac
done

# Validation
if [[ -z "$DEPLOY_TARGET" ]]; then
    log_error "No target specified"
    log_info "Usage: $0 <--config|--automations|--scripts|--all|--stage> \"commit message\""
    exit 1
fi

if [[ $DO_COMMIT -eq 1 && -z "$COMMIT_MSG" ]]; then
    log_error "Commit message required (or use --no-commit)"
    log_info "Usage: $0 $DEPLOY_TARGET \"your commit message\""
    exit 1
fi

# Check required scripts exist
if [[ ! -f "$SCRIPT_DIR/manage_ha.sh" ]]; then
    log_error "manage_ha.sh not found at $SCRIPT_DIR/manage_ha.sh"
    exit 1
fi

if [[ $DO_COMMIT -eq 1 && ! -f "$SCRIPT_DIR/git_workflow.sh" ]]; then
    log_error "git_workflow.sh not found at $SCRIPT_DIR/git_workflow.sh"
    exit 1
fi

# Banner
echo ""
log_step "═══════════════════════════════════════════════════════"
log_step "  Unified HA Workflow: Deploy → Commit → Push"
log_step "═══════════════════════════════════════════════════════"
echo ""

# Step 1: Validate YAML syntax locally
log_step "Step 1: Validating YAML syntax..."

FILES_TO_CHECK=()
case "$DEPLOY_TARGET" in
    --config)
        FILES_TO_CHECK=("configuration.yaml" "scripts.yaml")
        [[ -f "scenes.yaml" ]] && FILES_TO_CHECK+=("scenes.yaml")
        ;;
    --automations)
        FILES_TO_CHECK=("automations.yaml")
        ;;
    --scripts)
        FILES_TO_CHECK=("scripts.yaml")
        ;;
    --all)
        FILES_TO_CHECK=("configuration.yaml" "automations.yaml" "scripts.yaml")
        [[ -f "scenes.yaml" ]] && FILES_TO_CHECK+=("scenes.yaml")
        ;;
    --stage)
        FILES_TO_CHECK=("dashboards/dashboard.stage.yaml")
        ;;
esac

YAML_VALID=1
for file in "${FILES_TO_CHECK[@]}"; do
    if [[ ! -f "$file" ]]; then
        log_warning "File not found: $file (skipping)"
        continue
    fi
    
    # Try python3 + PyYAML first; fallback to basic check if yaml module missing
    if command -v python3 &> /dev/null && python3 -c "import yaml" 2>/dev/null; then
        if python3 -c "import yaml; yaml.safe_load(open('$file'))" 2>/dev/null; then
            log_success "Valid: $file"
        else
            log_error "Invalid YAML: $file"
            python3 -c "import yaml; yaml.safe_load(open('$file'))" 2>&1 | head -5
            YAML_VALID=0
        fi
    else
        # No python3 or no PyYAML - basic check only
        if grep -q "^[[:space:]]*\t" "$file"; then
            log_error "Tabs found in $file (YAML must use spaces)"
            YAML_VALID=0
        else
            log_success "Basic check: $file (install PyYAML for full validation)"
        fi
    fi
done

if [[ $YAML_VALID -eq 0 ]]; then
    log_error "YAML validation failed - fix errors before deploying"
    exit 1
fi

echo ""
log_step "Step 2: Deploying to Home Assistant..."

# Build manage_ha.sh command
MANAGE_CMD="$SCRIPT_DIR/manage_ha.sh $DEPLOY_TARGET"
[[ $DO_VALIDATE -eq 1 ]] && MANAGE_CMD="$MANAGE_CMD --validate"
[[ $DO_RESTART -eq 1 ]] && MANAGE_CMD="$MANAGE_CMD --restart"

if $MANAGE_CMD; then
    log_success "Deployed successfully"
else
    log_error "Deployment failed"
    exit 1
fi

# Step 3: Git commit (optional)
if [[ $DO_COMMIT -eq 0 ]]; then
    echo ""
    log_warning "Skipping git commit (--no-commit)"
    log_info "Changes deployed but not committed to git"
    exit 0
fi

echo ""
log_step "Step 3: Committing to git..."

# Determine which files to stage based on target
GIT_CMD="$SCRIPT_DIR/git_workflow.sh"

case "$DEPLOY_TARGET" in
    --config)
        # Stage config files
        git add configuration.yaml scripts.yaml 2>/dev/null || true
        [[ -f "scenes.yaml" ]] && git add scenes.yaml 2>/dev/null || true
        ;;
    --automations)
        git add automations.yaml 2>/dev/null || true
        ;;
    --scripts)
        git add scripts.yaml 2>/dev/null || true
        ;;
    --all)
        git add configuration.yaml automations.yaml scripts.yaml 2>/dev/null || true
        [[ -f "scenes.yaml" ]] && git add scenes.yaml 2>/dev/null || true
        ;;
    --stage)
        git add dashboards/dashboard.stage.yaml 2>/dev/null || true
        ;;
esac

# Check if there are changes to commit
if git diff --cached --quiet; then
    log_warning "No changes to commit"
    log_info "Files already in sync with git"
    exit 0
fi

# Show what will be committed
log_info "Files to commit:"
git diff --cached --name-status | while read status file; do
    echo "  $status  $file"
done

# Commit
if git commit -m "$COMMIT_MSG"; then
    log_success "Committed: $(git log -1 --oneline)"
else
    log_error "Commit failed"
    exit 1
fi

# Step 4: Git push (optional)
if [[ $DO_PUSH -eq 0 ]]; then
    echo ""
    log_warning "Skipping git push (--no-push)"
    log_info "Changes committed locally only"
    log_info "To push later: ./scripts/git_workflow.sh push"
    exit 0
fi

# Check if remote exists
if ! git remote get-url origin &>/dev/null; then
    echo ""
    log_warning "No git remote configured"
    log_info "Changes committed locally only"
    log_info "To add remote: ./scripts/git_workflow.sh setup-remote <url>"
    exit 0
fi

echo ""
log_step "Step 4: Pushing to git remote..."

if $GIT_CMD push; then
    log_success "Pushed to remote"
else
    log_error "Push failed"
    exit 1
fi

# Success banner
echo ""
log_step "═══════════════════════════════════════════════════════"
log_success "  Workflow Complete!"
log_step "═══════════════════════════════════════════════════════"
echo ""
log_info "Summary:"
log_success "  ✓ YAML validated"
log_success "  ✓ Deployed to HA"
[[ $DO_VALIDATE -eq 1 ]] && log_success "  ✓ HA config validated"
[[ $DO_RESTART -eq 1 ]] && log_success "  ✓ HA restarted"
log_success "  ✓ Committed to git"
[[ $DO_PUSH -eq 1 ]] && git remote get-url origin &>/dev/null && log_success "  ✓ Pushed to remote"
echo ""
