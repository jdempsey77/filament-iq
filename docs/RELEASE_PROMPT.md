FILAMENT IQ — ORCHESTRATOR PROMPT
==================================
AGENT TARGET: Releaser Agent
TRIGGER: RELEASE

INTENT:
Check for OSS drift after a successful deploy, classify the
version bump, and cut a release if needed.

CONTEXT:
Last OSS tag   : [LAST_TAG]        ← fill in e.g. v0.9.0
Commits since  : [COMMIT_LIST]     ← fill in from git log
Deploy commit  : [DEPLOY_COMMIT]   ← fill in e.g. 1bbc233

─────────────────────────────────────────────────────────────
STEP 1 — CHECK FOR DRIFT
─────────────────────────────────────────────────────────────
Run: ./scripts/sync-oss.sh

If NO drift → print "OSS in sync — no release needed" and STOP.
If drift found → continue to Step 2.

─────────────────────────────────────────────────────────────
STEP 2 — CLASSIFY VERSION BUMP
─────────────────────────────────────────────────────────────
Scan all commit messages since [LAST_TAG] using:
  git log [LAST_TAG]..HEAD --oneline

Apply this formula:

  MAJOR (x.0.0):
    - Breaking change to apps.yaml schema
    - Renamed or removed config keys
    - Changed HA entity naming conventions
    - Any commit with "breaking:" prefix
    → STOP and ask user to approve before proceeding

  MINOR (0.x.0):
    - New feature added (new method, new app behaviour)
    - New config key added (backwards compatible)
    - Any commit with "feat:" prefix
    → Auto-proceed

  PATCH (0.0.x):
    - Bug fix, guard adjustment, test addition
    - Refactor with no behaviour change
    - Any commit with "fix:", "refactor:", "test:", "docs:"
    → Auto-proceed

Compute next version from [LAST_TAG]:
  PATCH: increment Z   e.g. v0.9.0 → v0.9.1
  MINOR: increment Y   e.g. v0.9.1 → v0.10.0, reset Z to 0
  MAJOR: increment X   e.g. v0.10.0 → v1.0.0, reset Y+Z to 0

If MAJOR: print classification + proposed version and STOP.
Ask: "This is a MAJOR release ([reason]). Approve v[X.0.0]?"
Wait for explicit user approval before continuing.

─────────────────────────────────────────────────────────────
STEP 3 — GENERATE CHANGELOG ENTRY
─────────────────────────────────────────────────────────────
Generate a CHANGELOG.md entry for the new version using
conventional commit messages since [LAST_TAG].

Format:
  ## [vX.Y.Z] - UNRELEASED

  ### Added
  - (feat: commits)

  ### Fixed
  - (fix: commits)

  ### Changed
  - (refactor: commits)

  ### Tests
  - (test: commits, test count change)

  ### Docs
  - (docs: commits)

Insert above the previous version entry in CHANGELOG.md.
Do not overwrite existing entries.

─────────────────────────────────────────────────────────────
STEP 4 — CUT RELEASE
─────────────────────────────────────────────────────────────
Run: ./scripts/cut-release.sh v[X.Y.Z]

This will:
  1. Sync OSS repo (--copy)
  2. Run OSS test suite — ABORT if any failures
  3. Update CHANGELOG date (UNRELEASED → today)
  4. Commit + push release/vX.Y.Z branch to filament-iq
  5. Open PR on filament-iq: release/vX.Y.Z → main

Do NOT pass --merge. PR requires human review.

─────────────────────────────────────────────────────────────
STEP 5 — REPORT
─────────────────────────────────────────────────────────────
Print release summary:

  RELEASE SUMMARY
  ───────────────────────────────────────────
  Version       : vX.Y.Z
  Bump type     : PATCH / MINOR / MAJOR
  Commits synced: N
  OSS tests     : NNN passed
  PR            : https://github.com/jdempsey77/filament-iq/pull/N
  ───────────────────────────────────────────
  NEXT ACTION: Review and merge PR, then tag will be applied
  automatically by cut-release.sh after merge.

─────────────────────────────────────────────────────────────
CONSTRAINTS
─────────────────────────────────────────────────────────────
- Never push directly to main on filament-iq (branch protected)
- Never tag before PR is merged
- Never proceed past Step 2 on MAJOR without explicit approval
- OSS test failure always aborts — never force a release
- home_assistant repo: git push origin main after RELEASER
  completes (no PR required on private repo yet)
