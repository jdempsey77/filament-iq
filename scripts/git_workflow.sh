#!/bin/bash
#
# Home Assistant Git Workflow Helper
# Handles staging, committing, and pushing changes with proper workflow
#
# Usage:
#   ./scripts/git_workflow.sh commit "your message"
#   ./scripts/git_workflow.sh push
#   ./scripts/git_workflow.sh setup-remote <github-url>
#   ./scripts/git_workflow.sh auto "your message"  (commit + push)
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
NC='\033[0m' # No Color

# Functions
log_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1"
}

check_remote() {
    if git remote get-url origin &>/dev/null; then
        return 0
    else
        return 1
    fi
}

get_current_branch() {
    git rev-parse --abbrev-ref HEAD
}

check_main_branch() {
    local branch=$(get_current_branch)
    if [[ "$branch" == "main" || "$branch" == "master" ]]; then
        log_warning "You're on $branch branch"
        log_warning "Per .cursorrules, direct commits to main are discouraged"
        read -p "Continue anyway? (y/N): " confirm
        if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
            log_info "Aborting. Create a feature branch first:"
            log_info "  git checkout -b feature/my-changes"
            exit 1
        fi
    fi
}

setup_remote() {
    local remote_url="$1"
    
    if [[ -z "$remote_url" ]]; then
        log_error "Usage: $0 setup-remote <github-url>"
        log_info "Example: $0 setup-remote https://github.com/username/home-assistant.git"
        exit 1
    fi
    
    log_info "Setting up remote repository..."
    
    if check_remote; then
        log_warning "Remote 'origin' already exists: $(git remote get-url origin)"
        read -p "Replace it? (y/N): " confirm
        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            git remote remove origin
            log_success "Removed old remote"
        else
            log_info "Keeping existing remote"
            exit 0
        fi
    fi
    
    git remote add origin "$remote_url"
    log_success "Added remote: $remote_url"
    
    log_info "Testing connection..."
    if git ls-remote origin &>/dev/null; then
        log_success "Remote connection successful!"
    else
        log_error "Could not connect to remote. Check URL and credentials."
        exit 1
    fi
}

stage_step1_files() {
    log_info "Staging Step 1 files..."
    
    local files=(
        "automations.yaml"
        "configuration.yaml"
        "scripts.yaml"
        "STEP_1_IMPLEMENTATION.md"
        "STEP_1_CHECKLIST.md"
        "FILAMENT_TRACKING_ARCHITECTURE.md"
        "FILAMENT_TRACKING_DEEP_DIVE.md"
    )
    
    local staged_count=0
    for file in "${files[@]}"; do
        if [[ -f "$file" ]]; then
            git add "$file"
            log_success "Staged: $file"
            ((staged_count++))
        fi
    done
    
    if [[ $staged_count -eq 0 ]]; then
        log_warning "No Step 1 files found to stage"
        return 1
    fi
    
    log_success "Staged $staged_count file(s)"
    return 0
}

commit_changes() {
    local message="$1"
    
    if [[ -z "$message" ]]; then
        log_error "Usage: $0 commit \"your commit message\""
        exit 1
    fi
    
    check_main_branch
    
    # Check if there are changes to commit
    if git diff --cached --quiet; then
        log_warning "No staged changes to commit"
        log_info "Staging Step 1 files automatically..."
        if ! stage_step1_files; then
            log_error "No changes to commit"
            exit 1
        fi
    fi
    
    # Show what will be committed
    log_info "Files to be committed:"
    git diff --cached --name-status | while read status file; do
        echo "  $status  $file"
    done
    
    # Create commit
    log_info "Creating commit..."
    git commit -m "$message"
    
    log_success "Committed successfully!"
    log_info "Commit: $(git log -1 --oneline)"
}

push_changes() {
    local branch=$(get_current_branch)
    
    if ! check_remote; then
        log_error "No remote repository configured"
        log_info "Set up remote first:"
        log_info "  $0 setup-remote https://github.com/username/repo.git"
        exit 1
    fi
    
    # Check if there are unpushed commits
    if git diff origin/$branch..HEAD --quiet 2>/dev/null && \
       git log origin/$branch..HEAD --oneline 2>/dev/null | grep -q .; then
        : # Has unpushed commits
    elif ! git rev-parse origin/$branch &>/dev/null; then
        log_info "Branch '$branch' doesn't exist on remote yet"
    else
        log_warning "No new commits to push"
        exit 0
    fi
    
    log_info "Pushing to origin/$branch..."
    
    if git rev-parse origin/$branch &>/dev/null; then
        # Branch exists on remote, normal push
        git push
        log_success "Pushed to origin/$branch"
    else
        # First push of this branch
        git push -u origin "$branch"
        log_success "Pushed new branch to origin/$branch"
        log_info "Branch tracking set up"
    fi
}

auto_workflow() {
    local message="$1"
    
    if [[ -z "$message" ]]; then
        log_error "Usage: $0 auto \"your commit message\""
        exit 1
    fi
    
    log_info "=== Starting Auto Workflow ==="
    
    # Stage files
    log_info ""
    log_info "Step 1: Staging files..."
    stage_step1_files || {
        log_error "Failed to stage files"
        exit 1
    }
    
    # Commit
    log_info ""
    log_info "Step 2: Creating commit..."
    git commit -m "$message" || {
        log_error "Failed to create commit"
        exit 1
    }
    log_success "Committed: $(git log -1 --oneline)"
    
    # Push (if remote exists)
    if check_remote; then
        log_info ""
        log_info "Step 3: Pushing to remote..."
        push_changes
    else
        log_warning "No remote configured - commit saved locally only"
        log_info "To push later, set up remote:"
        log_info "  $0 setup-remote <github-url>"
        log_info "Then push:"
        log_info "  $0 push"
    fi
    
    log_info ""
    log_success "=== Workflow Complete ==="
}

show_status() {
    log_info "=== Git Status ==="
    echo ""
    
    log_info "Current branch: $(get_current_branch)"
    
    if check_remote; then
        log_info "Remote: $(git remote get-url origin)"
    else
        log_warning "No remote configured"
    fi
    
    echo ""
    log_info "Uncommitted changes:"
    git status --short
    
    echo ""
    log_info "Recent commits:"
    git log --oneline -5
    
    echo ""
    if check_remote && git rev-parse @{u} &>/dev/null; then
        local ahead=$(git rev-list --count @{u}..HEAD 2>/dev/null || echo "0")
        local behind=$(git rev-list --count HEAD..@{u} 2>/dev/null || echo "0")
        
        if [[ $ahead -gt 0 ]]; then
            log_info "Ahead of remote by $ahead commit(s) - ready to push"
        fi
        if [[ $behind -gt 0 ]]; then
            log_warning "Behind remote by $behind commit(s) - pull needed"
        fi
        if [[ $ahead -eq 0 && $behind -eq 0 ]]; then
            log_success "In sync with remote"
        fi
    fi
}

show_help() {
    cat << EOF
Home Assistant Git Workflow Helper

USAGE:
    $0 <command> [options]

COMMANDS:
    status
        Show current git status, branch, and sync state
    
    setup-remote <github-url>
        Configure remote repository
        Example: $0 setup-remote https://github.com/user/repo.git
    
    stage
        Stage Step 1 files for commit
    
    commit "message"
        Stage Step 1 files and commit with message
        Example: $0 commit "feat: Add filament tracking fixes"
    
    push
        Push current branch to remote
    
    auto "message"
        Complete workflow: stage → commit → push
        Example: $0 auto "feat: Add Step 1 safety features"

EXAMPLES:
    # First time setup
    $0 setup-remote https://github.com/username/home-assistant-config.git
    
    # Quick commit + push
    $0 auto "feat: Add filament tracking safety fixes"
    
    # Step by step
    $0 stage
    $0 commit "feat: Step 1 implementation"
    $0 push
    
    # Check status
    $0 status

NOTES:
    - Follows .cursorrules (no direct main commits)
    - Automatically stages Step 1 files
    - Safe error handling
    - Color-coded output
EOF
}

# Main script logic
case "${1:-}" in
    setup-remote)
        setup_remote "$2"
        ;;
    stage)
        stage_step1_files
        ;;
    commit)
        commit_changes "$2"
        ;;
    push)
        push_changes
        ;;
    auto)
        auto_workflow "$2"
        ;;
    status)
        show_status
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        log_error "Unknown command: ${1:-}"
        echo ""
        show_help
        exit 1
        ;;
esac
