# Private GitHub Repository Setup Guide

## 🔐 Step 1: Create Private Repo on GitHub

### Manual Steps (5 minutes)

1. **Go to GitHub** (in your browser)
   - URL: https://github.com/new
   - (Sign in if needed)

2. **Fill in the form:**
   ```
   Repository name: home-assistant-config
   Description: Private Home Assistant configuration with filament tracking
   Visibility: ⚫ Private ← SELECT THIS!
   
   ❌ DO NOT check "Add a README file"
   ❌ DO NOT check ".gitignore"
   ❌ DO NOT check "Choose a license"
   
   (Your repo already has files, so keep these unchecked)
   ```

3. **Click "Create repository"**

4. **Copy the HTTPS URL** from the next page
   - Should look like: `https://github.com/jdempsey/home-assistant-config.git`
   - Or SSH: `git@github.com:jdempsey/home-assistant-config.git`

---

## 🔗 Step 2: Connect Your Local Repo

Once you have the URL, run:

```bash
cd /Users/jdempsey/code/home_assistant

# Setup remote (replace URL with yours)
./scripts/git_workflow.sh setup-remote https://github.com/YOUR-USERNAME/home-assistant-config.git
```

**Example:**
```bash
./scripts/git_workflow.sh setup-remote https://github.com/jdempsey/home-assistant-config.git
```

---

## 📤 Step 3: Push Your Step 1 Changes

### Option A: Full Workflow (Recommended)

```bash
# This validates → deploys → restarts → commits → pushes
./scripts/workflow.sh --all "feat: Add Step 1 filament tracking safety fixes

CRITICAL FIXES:
- Fix negative end value bug (clamp all grams to >= 0)
- Implement failed print policy (no decrement by default)
- Add print mutex to prevent duplicate decrements

SAFETY FEATURES:
- Add reconcile flag for unsafe conditions
- Add spool swap detection during prints
- Block manual updates during active prints
" --restart
```

### Option B: Just Push Existing Commits

```bash
# If already committed locally, just push
./scripts/git_workflow.sh push
```

---

## 🔒 Security: Protect Your Secrets

### Create .gitignore FIRST (Critical!)

```bash
cd /Users/jdempsey/code/home_assistant

# Create .gitignore to prevent committing secrets
cat > .gitignore << 'EOF'
# Secrets - NEVER commit these!
secrets.yaml
SERVICE_ACCOUNT.json
.storage/
scripts/deploy.env
*.pem
*.key

# Backups
*.bak
*.backup
*.bak[0-9]
*.bak2
*.bak3

# macOS
.DS_Store
.AppleDouble
.LSOverride

# Temporary files
*.tmp
*.temp

# IDE
.vscode/
.idea/
EOF

# Commit the .gitignore
git add .gitignore
git commit -m "chore: Add .gitignore for secrets and backups"
```

---

## 🎯 Complete Setup (Copy-Paste Ready)

**After creating the GitHub repo, run these commands:**

```bash
cd /Users/jdempsey/code/home_assistant

# 1. Protect secrets FIRST
cat > .gitignore << 'EOF'
secrets.yaml
SERVICE_ACCOUNT.json
.storage/
scripts/deploy.env
*.pem
*.key
*.bak
*.backup
*.bak[0-9]
.DS_Store
*.tmp
EOF

git add .gitignore
git commit -m "chore: Add .gitignore for secrets"

# 2. Setup remote (REPLACE WITH YOUR URL!)
./scripts/git_workflow.sh setup-remote https://github.com/YOUR-USERNAME/home-assistant-config.git

# 3. Push everything
./scripts/workflow.sh --all "feat: Add Step 1 filament tracking safety fixes" --restart
```

---

## 🆘 Troubleshooting

### "Permission denied (publickey)"
If using SSH URL, you need to setup SSH keys. **Use HTTPS instead:**
```bash
./scripts/git_workflow.sh setup-remote https://github.com/YOUR-USERNAME/home-assistant-config.git
```

### "Authentication failed"
- GitHub will prompt for credentials
- Or: Create a Personal Access Token
  - GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
  - Generate new token with `repo` scope
  - Use token as password when prompted

### "Remote already exists"
```bash
# Remove old remote first
git remote remove origin

# Then add new one
./scripts/git_workflow.sh setup-remote YOUR-URL
```

---

## 📋 Quick Reference

| Step | Command |
|------|---------|
| Create .gitignore | See "Protect Your Secrets" above |
| Setup remote | `./scripts/git_workflow.sh setup-remote <url>` |
| Check connection | `git remote -v` |
| Push changes | `./scripts/workflow.sh --all "message"` |
| Check status | `./scripts/git_workflow.sh status` |

---

## 🎉 What You Get

**Private GitHub Repo Benefits:**
- ✅ Full version history
- ✅ Backup in the cloud (private - only you can see it)
- ✅ Access from anywhere
- ✅ Compare changes over time
- ✅ Rollback to any point
- ✅ No one else can see your config

---

## 🚀 Next Steps

**NOW:**
1. Go to https://github.com/new
2. Create **private** repo named `home-assistant-config`
3. Copy the URL
4. Come back with the URL

**THEN:**
I'll give you the exact commands with your URL filled in!

Or just follow the "Complete Setup" commands above and replace `YOUR-USERNAME` with your GitHub username.

---

**Create the repo now, then paste the URL here!** 🔐
