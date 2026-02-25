# Cursor Skill — Validation, Deployment, and Code Management (Authoritative)

This repository runs on strict, deterministic engineering discipline.
Cursor must follow this doc exactly.

## Goals

- Prevent broken deployments
- Prevent skipped testing / skipped gates
- Prevent accidental loss of scripts or helper definitions
- Ensure every deploy is auditable and reversible
- Default to deterministic “run all gates” behavior

## Triggers (ALL CAPS) — script mapping

When the user types one of these in ALL CAPS, Cursor must execute the corresponding workflow. **Never invent deployment steps.**

| Trigger   | Action |
|-----------|--------|
| **TEST**  | Run `./scripts/skill_test.sh`. Output structured summary (STATUS, COMMANDS RUN, ARTIFACTS, NEXT ACTION). |
| **DEPLOY**| Run `./scripts/skill_deploy.sh` (it runs TEST first, then `./scripts/manage_ha.sh` by change type). Output structured summary. |
| **CHECKIN** | Follow CHECKIN workflow below (commit + audit summary). No standalone script. |
| **GUARDRAILS** | Restate and enforce repo rules from this doc. No script. |
| **ROLLBACK** | Provide safe rollback steps from this doc; use `./scripts/manage_ha.sh` for redeploy. No standalone script. |
| **PHASE**  | Show current maturity posture (gates, flags, what TEST/DEPLOY enforce). No standalone script. |

### TEST
Run the full validation workflow and output PASS/FAIL with a checklist summary. **Implementation:** `./scripts/skill_test.sh`.

### DEPLOY
Run TEST first. If PASS, deploy using `./scripts/manage_ha.sh` based on change type, then run post-deploy verification. Output a deployment record. **Implementation:** `./scripts/skill_deploy.sh` (runs TEST then deploy).

### CHECKIN
Create a clean, reviewable commit with an audit summary and validation notes.

### GUARDRAILS
Restate and enforce repo rules (what is never allowed). Used to re-anchor Cursor if it drifts.

### ROLLBACK
Provide safe rollback steps (revert + redeploy known-good + re-validate).

### PHASE
Show “current maturity posture”: phase gates available, feature flags, and which gates are enforced by TEST/DEPLOY.

---

## Non-Negotiable Rules

1. **Never deploy unless TEST passes.**
2. **TEST runs ALL phase gates** (deterministic; no skipping for speed).
3. **DEPLOY must use `./scripts/manage_ha.sh`** as the only deploy interface.
4. **Every TEST/DEPLOY ends with a structured summary**:
   - STATUS: PASS/FAIL
   - COMMANDS RUN:
   - ARTIFACTS / LOGS:
   - NEXT ACTION:
5. Any proposed code change must include:
   - How it will be validated (which tests/gates)
   - What “done” looks like
6. Never delete/overwrite scripts or helper definitions without:
   - A git commit preserving the change
   - Updated validations/gates if behavior changes
7. If post-deploy verification fails:
   - Treat as a deployment failure
   - Recommend ROLLBACK steps immediately
8. **If DEPLOY fails because skill_deploy.sh or other skill scripts crashed or bugged, STOP.** Do not fix scripts and continue deploying in the same run. Then:
   - (1) Propose a patch for the script bug.
   - (2) Require TEST (run `./scripts/skill_test.sh`).
   - (3) Require CHECKIN (commit the fix with audit summary).
   - (4) Rerun DEPLOY only after CHECKIN.
   DEPLOY must not amend commits or continue deploying in the same run after editing scripts.

---

## TEST Workflow (Authoritative)

### 0) Preconditions
- Repo is on the intended branch
- Working tree is clean (or user explicitly approves running dirty)

### 1) Preflights (must run)
Run the repo’s preflight scripts that validate Home Assistant helper integrity and integration wiring.
Examples (actual scripts may vary; update this list as canonical):
- `./scripts/preflight_input_text.sh`
- `./scripts/preflight_spoolman_filament_dropdown.sh`
- `./scripts/preflight_ams_matching.sh`
- `./scripts/preflight_spoolman_location_update.sh`
- Helper integrity validation scripts (manifest sync/validate if present)

Expected outcome:
- Each preflight prints PASS/FAIL and TEST fails on any FAIL.

### 2) Unit / integration tests (must run if present)
- Python unit tests for AppDaemon / reconciliation logic (pytest)
- Any additional scripted checks

Expected outcome:
- All tests pass.

### 3) Phase Gates (must run ALL)
Phase gates represent system-level correctness (state model, RFID/non-RFID, snapshot logic, etc).
TEST must run all gates deterministically.

Canonical examples (update to match what exists):
- `./scripts/gate_phase0_rfid_regression.sh`
- Any other `gate_phase*.sh` scripts

Expected outcome:
- All gates pass.

### 4) Output format (required)
At the end of TEST, output:

- STATUS: PASS/FAIL
- COMMANDS RUN: (bulleted)
- ARTIFACTS / LOGS: (paths to captured outputs)
- FAILURES: (if any, include exact step + error)
- NEXT ACTION: (minimal fix + re-run TEST)

---

## DEPLOY Workflow (Authoritative)

### 0) Always run TEST first
DEPLOY must run TEST and abort if FAIL.

### 1) Determine deploy target from change type
Use git diff to decide. Deploy only via `./scripts/manage_ha.sh`.

Rules:
- If AppDaemon code changed (e.g., `appdaemon/`): deploy with `--appdaemon`
- If HA scripts changed (`scripts.yaml`, HA script files): deploy with `--scripts`
- If HA config changed (`configuration.yaml`, `automations.yaml`, dashboards): deploy with `--config`

If multiple areas changed, deploy in the safest order:
1) `--scripts`
2) `--config`
3) `--appdaemon`
(Adjust if repo conventions differ.)

### 2) Post-deploy verification (must run)
After deployment:
- Confirm expected helper entities exist (and are writable when required)
- Confirm AppDaemon is restarted when AppDaemon changes are deployed
- Tail logs for known error signatures (websocket 502, DomainException, missing services)
- Re-run relevant phase gate(s) in verification mode if available
- Confirm system is stable (no repeating errors)

### 3) Deployment record (required)
DEPLOY must produce a record including:
- Date/time
- Branch + git SHA
- What changed (high level)
- Commands executed (exact)
- Verification results
- Rollback command(s) if needed

---

## CHECKIN Workflow (Authoritative)

CHECKIN produces a clean, reviewable commit:
- Confirm branch name
- Summarize changes (what/why)
- List validation steps run (TEST results)
- Commit message template:

  `<area>: <short summary>`
  - Why:
  - Risk:
  - Validated by:

---

## ROLLBACK Workflow (Authoritative)

Rollback approach:
1) Identify last known-good SHA
2) `git revert` or `git reset --hard` (depending on policy)
3) Re-deploy using `./scripts/manage_ha.sh` with the same target(s)
4) Run TEST (or at least verification gates)
5) Capture logs + record outcome

---

## PHASE Workflow (Authoritative)

PHASE outputs:
- List of phase gate scripts available
- Feature flags and expected default values (if known)
- Which gates are enforced by TEST/DEPLOY
- Any temporary waivers (avoid if possible; must be explicit)

---

## Maintenance

When new gates/scripts are added:
- Update this doc
- Update the canonical TEST runner (if used)
- Keep outputs deterministic and auditable
