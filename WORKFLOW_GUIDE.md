# Unified Workflow Guide - One Command Does Everything

## 🎯 The Magic Command

This **one command** does it all: validates → deploys → commits → pushes

```bash
./scripts/workflow.sh --automations "your commit message"
```

---

## 🚀 Common Workflows

### Deploy Automations (Step 1 Changes)
```bash
./scripts/workflow.sh --automations "feat: Add Step 1 filament tracking safety fixes"
```

**This will:**
1. ✓ Validate automations.yaml syntax
2. ✓ Deploy to Home Assistant
3. ✓ Commit to git
4. ✓ Push to GitHub (if configured)

### Deploy Configuration + Scripts
```bash
./scripts/workflow.sh --config "feat: Add new helpers for filament tracking"
```

**Deploys:** configuration.yaml, scripts.yaml, scenes.yaml

### Deploy Everything + Restart
```bash
./scripts/workflow.sh --all "feat: Major filament tracking update" --restart
```

**Deploys:** All YAML files + restarts HA

### Deploy Stage Dashboard
```bash
./scripts/workflow.sh --stage "feat: Update dashboard layout"
```

---

## 🎛️ Control Flags

### Skip Push (Commit Local Only)
```bash
./scripts/workflow.sh --automations "work in progress" --no-push
```

### Skip Commit (Just Deploy)
```bash
./scripts/workflow.sh --automations "testing" --no-commit
```

### Validate Before Deploy
```bash
./scripts/workflow.sh --config "update helpers" --validate
```

### Restart After Deploy
```bash
./scripts/workflow.sh --config "critical fix" --restart
```

### Combine Flags
```bash
./scripts/workflow.sh --all "feat: Complete Step 1" --validate --restart --no-push
```

---

## 📋 Your Specific Use Cases

### Case 1: Step 1 Changes (What You Need Now)

**Without GitHub (Local Only):**
```bash
cd /Users/jdempsey/code/home_assistant

# Deploy automations + commit locally
./scripts/workflow.sh --automations "feat: Add Step 1 filament tracking safety fixes" --no-push

# Deploy config + commit locally
./scripts/workflow.sh --config "feat: Add filament tracking helpers" --no-push

# Deploy scripts + commit locally
./scripts/workflow.sh --scripts "feat: Add print mutex to slot updates" --no-push
```

**Or all at once:**
```bash
./scripts/workflow.sh --all "feat: Complete Step 1 - filament tracking safety fixes

CRITICAL FIXES:
- Fix negative end value bug (clamp all grams to >= 0)
- Implement failed print policy (no decrement by default)
- Add print mutex to prevent duplicate decrements

SAFETY FEATURES:
- Add reconcile flag for unsafe conditions
- Add spool swap detection during prints
- Block manual updates during active prints
" --no-push --restart
```

### Case 2: Future GitHub Integration

**First Time Setup:**
```bash
# 1. Create repo on GitHub
# 2. Setup remote
./scripts/git_workflow.sh setup-remote https://github.com/jdempsey/home-assistant.git

# 3. Push existing commits
./scripts/git_workflow.sh push
```

**Then Normal Workflow (with GitHub):**
```bash
# Just remove --no-push flag
./scripts/workflow.sh --automations "feat: New automation"
```

---

## 🔧 Standalone Git Commands

If you just want git operations without deployment:

```bash
# Check status
./scripts/git_workflow.sh status

# Commit only
./scripts/git_workflow.sh commit "message"

# Push only
./scripts/git_workflow.sh push

# Quick commit + push
./scripts/git_workflow.sh auto "message"
```

---

## 💡 Smart Features

### Auto-Stage
Automatically stages the right files based on target:
- `--config` → configuration.yaml, scripts.yaml, scenes.yaml
- `--automations` → automations.yaml
- `--scripts` → scripts.yaml
- `--all` → all of the above
- `--stage` → dashboards/dashboard.stage.yaml

### YAML Validation
Before deploying, checks for:
- Python YAML parser validation (if available)
- Tab characters (common YAML error)
- Syntax errors

### Safe Defaults
- Won't commit if no changes
- Won't push if no remote
- Warns if on main branch
- Shows what's being committed

---

## 🎯 For Your Step 1 Changes RIGHT NOW

**Quick Local Commit (Recommended):**
```bash
cd /Users/jdempsey/code/home_assistant

./scripts/workflow.sh --all "feat: Add Step 1 filament tracking safety fixes" --no-push --restart
```

**What this does:**
1. Validates all YAML files ✓
2. Deploys to HA (already done, but safe to re-run) ✓
3. Restarts HA (new helpers will be available) ✓
4. Commits to git ✓
5. Skips push (no GitHub yet) ✓

**Time:** ~2 minutes (including HA restart)

---

## 🆘 Troubleshooting

**"YAML validation failed"**
- Fix the syntax error shown
- Re-run the command

**"Deployment failed"**
- Check `scripts/deploy.env` exists and is configured
- Verify SSH access to HA

**"No changes to commit"**
- Files already committed
- Check: `git status`

**"No remote configured"**
- Normal! Just working locally
- Add remote later: `./scripts/git_workflow.sh setup-remote <url>`

---

## 📝 Quick Reference Card

```bash
# Most common: Deploy automations + commit (local)
./scripts/workflow.sh --automations "message" --no-push

# Most common: Deploy config + restart
./scripts/workflow.sh --config "message" --no-push --restart

# Check git status
./scripts/git_workflow.sh status

# Get help
./scripts/workflow.sh --help
./scripts/git_workflow.sh --help
```

---

## 🎉 You're All Set!

**Two scripts created:**
1. `scripts/workflow.sh` - Unified: Deploy → Commit → Push
2. `scripts/git_workflow.sh` - Git-only operations

**Try it now:**
```bash
./scripts/workflow.sh --all "feat: Add Step 1 filament tracking safety fixes" --no-push --restart
```

This will validate, deploy, restart HA, and commit your Step 1 changes! 🚀
