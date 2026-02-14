# Git Workflow Script - Quick Start Guide

## 🚀 Easy One-Command Workflow

### First Time: Setup Remote (Optional)
If you want to push to GitHub, do this once:

```bash
./scripts/git_workflow.sh setup-remote https://github.com/YOUR-USERNAME/home-assistant-config.git
```

Replace with your actual GitHub repo URL.

### Daily Use: Auto Workflow (Recommended)
This does everything: stage → commit → push

```bash
./scripts/git_workflow.sh auto "your commit message here"
```

**Example:**
```bash
./scripts/git_workflow.sh auto "feat: Add Step 1 filament tracking safety fixes"
```

---

## 📋 All Available Commands

### Check Status
```bash
./scripts/git_workflow.sh status
```
Shows: branch, remote, uncommitted files, recent commits, sync state

### Stage Files
```bash
./scripts/git_workflow.sh stage
```
Automatically stages all Step 1 files (automations.yaml, config, docs, etc.)

### Commit Only
```bash
./scripts/git_workflow.sh commit "your message"
```
Stages Step 1 files + commits (no push)

### Push Only
```bash
./scripts/git_workflow.sh push
```
Pushes current branch to remote

### Get Help
```bash
./scripts/git_workflow.sh help
```

---

## 🎯 For Your Situation (No Remote Yet)

### Option A: Local-Only (Right Now)
```bash
# Just commit locally, no GitHub needed
./scripts/git_workflow.sh commit "feat: Add Step 1 filament tracking safety fixes"
```

### Option B: Setup GitHub (Future)
1. Create repo on GitHub.com
2. Get the URL (e.g., `https://github.com/jdempsey/home-assistant.git`)
3. Run:
```bash
./scripts/git_workflow.sh setup-remote https://github.com/jdempsey/home-assistant.git
./scripts/git_workflow.sh push
```

---

## ✨ Features

- ✅ **Auto-stages Step 1 files** (no need to remember which files)
- ✅ **Checks for main branch** (warns per your .cursorrules)
- ✅ **Color-coded output** (success, warnings, errors)
- ✅ **Safe error handling** (won't break your repo)
- ✅ **Smart push** (handles first push vs. normal push)
- ✅ **Status checking** (shows sync state with remote)

---

## 🏃 Quick Actions

**Commit Step 1 changes now (local only):**
```bash
cd /Users/jdempsey/code/home_assistant
./scripts/git_workflow.sh commit "feat: Add Step 1 filament tracking safety fixes"
```

**Setup GitHub and push:**
```bash
# 1. Create repo on GitHub first
# 2. Then:
./scripts/git_workflow.sh setup-remote YOUR-GITHUB-URL
./scripts/git_workflow.sh push
```

**Check what's changed:**
```bash
./scripts/git_workflow.sh status
```

---

## 🔧 What Gets Committed

The script automatically includes these Step 1 files:
- `automations.yaml`
- `configuration.yaml`
- `scripts.yaml`
- `STEP_1_IMPLEMENTATION.md`
- `STEP_1_CHECKLIST.md`
- `FILAMENT_TRACKING_ARCHITECTURE.md`
- `FILAMENT_TRACKING_DEEP_DIVE.md`

It ignores:
- Backup files (`.bak`, `.bak2`, etc.)
- macOS files (`.DS_Store`)
- Test scripts (unless you manually stage them)
- Dashboard changes (handle separately)

---

## 📝 Examples

### Example 1: Quick commit (no remote)
```bash
./scripts/git_workflow.sh auto "feat: Step 1 safety fixes"
# Output: Staged → Committed → "No remote configured"
```

### Example 2: With remote
```bash
./scripts/git_workflow.sh auto "feat: Step 1 safety fixes"
# Output: Staged → Committed → Pushed to origin
```

### Example 3: Step by step
```bash
./scripts/git_workflow.sh status           # Check current state
./scripts/git_workflow.sh stage            # Stage files
./scripts/git_workflow.sh commit "message" # Commit
./scripts/git_workflow.sh push             # Push to remote
```

---

## 🆘 Troubleshooting

**"No remote configured"**
- Either setup remote or just commit locally
- Local commits still give you version control!

**"You're on main branch"**
- Script warns you (per your .cursorrules)
- You can continue or create a feature branch:
  ```bash
  git checkout -b feature/filament-tracking
  ```

**"No changes to commit"**
- Script automatically tries to stage Step 1 files
- If still nothing, you're already committed!

---

**Ready to use!** Try:
```bash
./scripts/git_workflow.sh status
```
