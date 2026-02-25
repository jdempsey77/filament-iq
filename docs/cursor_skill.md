# Cursor Skill --- Validation, Deployment, and Code Management (Authoritative)

This repository runs on strict, deterministic engineering discipline.\
Cursor must follow this doc exactly.

------------------------------------------------------------------------

## Goals

-   Prevent broken deployments\
-   Prevent skipped testing / skipped gates\
-   Prevent accidental loss of scripts or helper definitions\
-   Ensure every deploy is auditable and reversible\
-   Default to deterministic "run all gates" behavior\
-   Provide a safe, read-only investigation mode (ANALYZE)

------------------------------------------------------------------------

## Triggers (ALL CAPS) --- script mapping

When the user types one of these in ALL CAPS, Cursor must execute the
corresponding workflow. **Never invent deployment steps.**

  ------------------------------------------------------------------------
  Trigger                                    Action
  ------------------------------------------ -----------------------------
  **TEST**                                   Run
                                             `./scripts/skill_test.sh`.
                                             Output structured summary
                                             (STATUS, COMMANDS RUN,
                                             ARTIFACTS, NEXT ACTION).

  **DEPLOY**                                 Run
                                             `./scripts/skill_deploy.sh`
                                             (it runs TEST first, then
                                             `./scripts/manage_ha.sh` by
                                             change type). Output
                                             structured summary.

  **CHECKIN**                                Follow CHECKIN workflow below
                                             (commit + audit summary). No
                                             standalone script.

  **GUARDRAILS**                             Restate and enforce repo
                                             rules from this doc. No
                                             script.

  **ROLLBACK**                               Provide safe rollback steps
                                             from this doc; use
                                             `./scripts/manage_ha.sh` for
                                             redeploy. No standalone
                                             script.

  **PHASE**                                  Show current maturity posture
                                             (gates, flags, what
                                             TEST/DEPLOY enforce). No
                                             standalone script.

  **ANALYZE**                                Perform strict read-only
                                             investigation. No file edits.
                                             No deploy. No service calls.
                                             Output structured report
                                             only.
  ------------------------------------------------------------------------

------------------------------------------------------------------------

# Non-Negotiable Rules

1.  **Never deploy unless TEST passes.**
2.  **TEST runs ALL phase gates** (deterministic; no skipping for
    speed).
3.  **DEPLOY must use `./scripts/manage_ha.sh`** as the only deploy
    interface.
4.  **Every TEST/DEPLOY ends with a structured summary:**
    -   STATUS: PASS/FAIL
    -   COMMANDS RUN:
    -   ARTIFACTS / LOGS:
    -   NEXT ACTION:
5.  Any proposed code change must include:
    -   How it will be validated (which tests/gates)
    -   What "done" looks like
6.  Never delete/overwrite scripts or helper definitions without:
    -   A git commit preserving the change
    -   Updated validations/gates if behavior changes
7.  If post-deploy verification fails:
    -   Treat as a deployment failure
    -   Recommend ROLLBACK steps immediately
8.  **If DEPLOY fails because skill_deploy.sh or other skill scripts
    crashed or bugged, STOP.**
    -   (1) Propose a patch for the script bug.

    -   (2) Require TEST (`./scripts/skill_test.sh`).

    -   (3) Require CHECKIN (commit the fix with audit summary).

    -   (4) Rerun DEPLOY only after CHECKIN.

    -   DEPLOY must not amend commits or continue deploying in the same
        run after editing scripts.

------------------------------------------------------------------------

# TEST Workflow (Authoritative)

## 0) Preconditions

-   Repo is on the intended branch\
-   Working tree is clean (or user explicitly approves running dirty)

## 1) Preflights (must run)

Examples (update canonical list as needed):

-   `./scripts/preflight_input_text.sh`
-   `./scripts/preflight_spoolman_filament_dropdown.sh`
-   `./scripts/preflight_ams_matching.sh`
-   `./scripts/preflight_spoolman_location_update.sh`
-   Helper integrity validation scripts (manifest sync/validate if
    present)

Expected outcome:\
Each preflight prints PASS/FAIL and TEST fails on any FAIL.

## 2) Unit / Integration Tests

-   Python unit tests (pytest) if present
-   Any additional scripted checks

All must pass.

## 3) Phase Gates (must run ALL)

Examples: - `./scripts/gate_phase0_rfid_regression.sh` - Any other
`gate_phase*.sh`

All gates must pass.

## 4) Required TEST Output

-   STATUS: PASS/FAIL\
-   COMMANDS RUN:\
-   ARTIFACTS / LOGS:\
-   FAILURES:\
-   NEXT ACTION:

------------------------------------------------------------------------

# DEPLOY Workflow (Authoritative)

## 0) Always run TEST first

Abort if TEST fails.

## 1) Determine Deploy Target from Change Type

Use `git diff` to detect area.

Rules: - If `appdaemon/` changed → `--appdaemon` - If HA scripts changed
→ `--scripts` - If HA config changed → `--config`

If multiple areas changed, deploy in safest order:

1)  `--scripts`\
2)  `--config`\
3)  `--appdaemon`

## 2) Post-Deploy Verification (Required)

Must verify:

-   Helper entities exist and are writable (if required)
-   AppDaemon restarted if relevant
-   Tail logs for known signatures:
    -   websocket 502
    -   DomainException
    -   missing services
-   Re-run relevant phase gates (verification mode if supported)
-   Confirm no repeating errors

## 3) Deployment Record (Required)

Include:

-   Date/time
-   Branch + git SHA
-   What changed
-   Commands executed (exact)
-   Verification results
-   Rollback command(s)

------------------------------------------------------------------------

# CHECKIN Workflow (Authoritative)

Produces clean commit:

-   Confirm branch
-   Summarize changes (what + why)
-   List TEST results
-   Commit template:

```{=html}
<!-- -->
```
    <area>: <short summary>

    - Why:
    - Risk:
    - Validated by:

------------------------------------------------------------------------

# ROLLBACK Workflow (Authoritative)

1)  Identify last known-good SHA\
2)  `git revert` or `git reset --hard` (per policy)\
3)  Re-deploy via `./scripts/manage_ha.sh`\
4)  Run TEST (or verification gates)\
5)  Capture logs + outcome

------------------------------------------------------------------------

# PHASE Workflow (Authoritative)

Outputs:

-   Phase gate scripts available
-   Feature flags + default states (if known)
-   Which gates TEST enforces
-   Any temporary waivers (explicit only)

------------------------------------------------------------------------

# ANALYZE Workflow (Authoritative)

ANALYZE is a **strict read-only investigation mode.**

## Hard Rules (Non-Negotiable)

ANALYZE must not:

-   Edit files\
-   Create files\
-   Format files\
-   Commit\
-   Run TEST\
-   Run DEPLOY\
-   Run ROLLBACK\
-   Call `./scripts/manage_ha.sh`\
-   Restart services\
-   Make service calls to HA/AppDaemon/Spoolman\
-   Mutate Home Assistant or Spoolman state\
-   Execute state-changing scripts

ANALYZE must not mutate:

-   Repo\
-   Runtime system\
-   Remote hosts\
-   Home Assistant state\
-   Spoolman state

## Allowed Actions

-   Read files\
-   Trace control + data flow\
-   Identify regressions and invariants\
-   Identify likely root causes\
-   Propose patch snippets (NOT applied)\
-   Recommend next skill (TEST / CHECKIN / DEPLOY)

## Required ANALYZE Output Format

ANALYZE must output:

-   Executive Summary
-   Observed Evidence (files + symbols)
-   System Walkthrough (data flow)
-   Findings (ranked)
-   Root Cause Hypothesis (with confidence level)
-   Blast Radius / Risk
-   Suggested Fix (patch snippets only)
-   Suggested Validation (which TEST/gate to run)
-   Next Action (explicit skill recommendation)

ANALYZE must stop after delivering the report.\
It must never automatically transition into another skill.

------------------------------------------------------------------------

# Maintenance

When new gates/scripts are added:

-   Update this document\
-   Update canonical TEST runner\
-   Keep outputs deterministic and auditable
