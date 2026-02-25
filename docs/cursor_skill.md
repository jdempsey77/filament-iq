# Cursor Skill --- Validation, Deployment, and Code Management (Authoritative)

This repository runs on strict, deterministic engineering discipline.
Cursor must follow this doc exactly.

------------------------------------------------------------------------

## Goals

- Prevent broken deployments
- Prevent skipped testing / skipped gates
- Prevent accidental loss of scripts or helper definitions
- Ensure every deploy is auditable and reversible
- Default to deterministic "run all gates" behavior
- Provide a safe, read-only investigation mode (ANALYZE)
- Provide a minimal-impact reload-only deployment path (LIGHT_DEPLOY)

------------------------------------------------------------------------

## Triggers (ALL CAPS) --- script mapping

When the user types one of these in ALL CAPS, Cursor must execute the
corresponding workflow. **Never invent deployment steps.**

Trigger | Action
--------|--------
**TEST** | Run `./scripts/skill_test.sh`. Output structured summary (STATUS, COMMANDS RUN, ARTIFACTS, NEXT ACTION).
**DEPLOY** | Run `./scripts/skill_deploy.sh` (it runs TEST first, then `./scripts/manage_ha.sh` by change type). Output structured summary.
**LIGHT_DEPLOY** | Run `./scripts/light_deploy.sh` (reload-only deploy path; refuses if restart required).
**CHECKIN** | Follow CHECKIN workflow below (commit + audit summary). No standalone script.
**GUARDRAILS** | Restate and enforce repo rules from this doc. No script.
**ROLLBACK** | Provide safe rollback steps from this doc; use `./scripts/manage_ha.sh` for redeploy. No standalone script.
**PHASE** | Show current maturity posture (gates, flags, what TEST/DEPLOY enforce). No standalone script.
**ANALYZE** | Perform strict read-only investigation. No file edits. No deploy. No service calls. Output structured report only.

------------------------------------------------------------------------

# Non-Negotiable Rules

1. **Never deploy unless TEST passes.**
2. TEST runs ALL phase gates (deterministic; no skipping for speed).
3. DEPLOY must use `./scripts/manage_ha.sh` as the only deploy interface.
4. Every TEST/DEPLOY/LIGHT_DEPLOY ends with a structured summary:
   - STATUS: PASS/FAIL
   - COMMANDS RUN:
   - ARTIFACTS / LOGS:
   - NEXT ACTION:
5. Any proposed code change must include:
   - How it will be validated (which tests/gates)
   - What "done" looks like
6. Never delete/overwrite scripts or helper definitions without:
   - A git commit preserving the change
   - Updated validations/gates if behavior changes
7. If post-deploy verification fails:
   - Treat as a deployment failure
   - Recommend ROLLBACK steps immediately
8. If DEPLOY or LIGHT_DEPLOY fails because a skill script crashed or bugged, STOP:
   - (1) Propose a patch for the script bug.
   - (2) Require TEST (`./scripts/skill_test.sh`).
   - (3) Require CHECKIN (commit the fix with audit summary).
   - (4) Rerun DEPLOY only after CHECKIN.
   - Do not continue deploying after editing scripts in the same run.

------------------------------------------------------------------------

# LIGHT_DEPLOY Workflow (Authoritative)

LIGHT_DEPLOY provides a minimal-impact, reload-only deployment path.

## 0) Preconditions

- TEST must have passed on the current HEAD.
- Repo must be in a clean state (or explicitly approved dirty run).

If TEST has not passed, LIGHT_DEPLOY must refuse.

## Intent

Deploy only reloadable Home Assistant changes without restarting HA.

Eligible changes:
- automations.yaml
- scripts.yaml
- configuration.yaml (customize/customize_glob changes ONLY)

## Hard Rules (No Exceptions)

If git diff touches:

- Any `input_*:` helper block
- Any integration definition
- Any platform under `sensor:`, `rest:`, `template:`, `mqtt:`, etc.
- Any file outside `automations.yaml`, `scripts.yaml`, `configuration.yaml`
- Any `configuration.yaml` change outside `homeassistant.customize` or `customize_glob`

→ REFUSE and instruct: **Use DEPLOY (restart required).**

If reload fails → FAIL.
If any domain disappears → FAIL.
If helpers become restored/unavailable → FAIL.
No silent success.
No auto-escalation to DEPLOY.

## Execution

Run:

`./scripts/light_deploy.sh`

## Required Output

- STATUS: PASS/FAIL
- COMMANDS RUN:
- RELOAD ACTIONS:
- VALIDATION RESULTS:
- NEXT ACTION:

------------------------------------------------------------------------

# TEST Workflow (Authoritative)

## 0) Preconditions

- Repo is on the intended branch
- Working tree is clean (or user explicitly approves running dirty)

## 1) Preflights (must run)

Examples (update canonical list as needed):

- `./scripts/preflight_input_text.sh`
- `./scripts/preflight_spoolman_filament_dropdown.sh`
- `./scripts/preflight_ams_matching.sh`
- `./scripts/preflight_spoolman_location_update.sh`
- Helper integrity validation scripts (manifest sync/validate if present)

Each preflight must PASS.

## 2) Unit / Integration Tests

- Python unit tests (pytest) if present
- Any additional scripted checks

All must pass.

## 3) Phase Gates (must run ALL)

Examples:
- `./scripts/gate_phase0_rfid_regression.sh`
- Any other `gate_phase*.sh`

All must pass.

## 4) Required TEST Output

- STATUS: PASS/FAIL
- COMMANDS RUN:
- ARTIFACTS / LOGS:
- FAILURES:
- NEXT ACTION:

------------------------------------------------------------------------

# DEPLOY Workflow (Authoritative)

## 0) Always run TEST first

Abort if TEST fails.

## 1) Determine Deploy Target from Change Type

Use `git diff` to detect area.

Rules:
- If `appdaemon/` changed → `--appdaemon`
- If HA scripts changed → `--scripts`
- If HA config changed → `--config`

If multiple areas changed, deploy in safest order:

1) `--scripts`
2) `--config`
3) `--appdaemon`

## 2) Post-Deploy Verification (Required)

Must verify:

- Helper entities exist and are writable
- AppDaemon restarted if relevant
- Tail logs for:
  - websocket 502
  - DomainException
  - missing services
- Re-run relevant phase gates
- Confirm no repeating errors

## 3) Deployment Record (Required)

Include:

- Date/time
- Branch + git SHA
- What changed
- Commands executed
- Verification results
- Rollback command(s)

------------------------------------------------------------------------

# CHECKIN Workflow (Authoritative)

Produces clean commit:

- Confirm branch
- Summarize changes (what + why)
- List TEST results
- Commit template:

<area>: <short summary>

- Why:
- Risk:
- Validated by:

------------------------------------------------------------------------

# ROLLBACK Workflow (Authoritative)

1) Identify last known-good SHA
2) `git revert` or `git reset --hard`
3) Re-deploy via `./scripts/manage_ha.sh`
4) Run TEST (or verification gates)
5) Capture logs + outcome

------------------------------------------------------------------------

# PHASE Workflow (Authoritative)

Outputs:

- Phase gate scripts available
- Feature flags + default states
- Which gates TEST enforces
- Any temporary waivers (explicit only)

------------------------------------------------------------------------

# ANALYZE Workflow (Authoritative)

ANALYZE is a strict read-only investigation mode.

ANALYZE must not:

- Edit files
- Create files
- Commit
- Run TEST
- Run DEPLOY
- Run ROLLBACK
- Call `./scripts/manage_ha.sh`
- Restart services
- Make service calls
- Mutate runtime state

ANALYZE may:

- Read files
- Trace control/data flow
- Identify regressions
- Propose patch snippets (NOT applied)
- Recommend next skill

ANALYZE must output:

- Executive Summary
- Observed Evidence
- System Walkthrough
- Findings
- Root Cause Hypothesis
- Blast Radius / Risk
- Suggested Fix (patch only)
- Suggested Validation
- Next Action

ANALYZE must stop after delivering the report.

------------------------------------------------------------------------

# Maintenance

When new gates/scripts are added:

- Update this document
- Update canonical TEST runner
- Keep outputs deterministic and auditable
