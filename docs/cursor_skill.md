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
**PHASE_2_5** | Implement Phase 2.5 deterministic matching policy (code + tests). Must NOT deploy or restart services. Output structured summary.
**PHASE_2_6** | Implement Non-RFID Shelf-first matching + controlled New fallback + NEEDS_ACTION UX + tests. Must NOT deploy or restart services. Output structured summary.
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

# Phase 3b — Bind-flow verification (diagnostics)

When reconciler reports ok=6 but dashboard shows "N ports not bound", or "Bind Spool" does nothing, run these checks (from repo root; set `HOME_ASSISTANT_TOKEN` and use your HA URL, e.g. `http://192.168.4.124:8123`).

**1. Script present on server (if 0, script never deployed):**
```bash
./scripts/ssh_ha.sh "grep -c 'ams_slot_6_assign_and_update' /config/scripts.yaml"
```

**2. Dropdown options populated (empty/None → regex parse yields spool_id=0):**
```bash
curl -s -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" \
  "http://192.168.4.124:8123/api/states/input_select.ams_slot_6_select_spool" \
  | python3 -m json.tool | grep -E "state|options" | head -10
```

**3. Manual enroll event uses `slot` (not `slot_number`) on server:**
```bash
./scripts/ssh_ha.sh "grep -A5 'bambu_rfid_manual_enroll' /config/scripts.yaml | head -20"
```

**4. Unbound_reason vs dashboard (stale helpers → dashboard shows unbound despite ok=6):**
```bash
for i in 1 2 3 4 5 6; do
  val=$(curl -s -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" \
    "http://192.168.4.124:8123/api/states/input_text.ams_slot_${i}_unbound_reason" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['state'])")
  echo "Slot $i: $val"
done
```
If slots show non-empty `unbound_reason` but reconciler says bound, clear with `./scripts/clear_ams_unbound_reasons.sh` or the one-liner from the script.

------------------------------------------------------------------------

# PHASE_2_5 Workflow (Authoritative)

PHASE_2_5 implements deterministic spool matching policy:

- Shelf-only matching (primary candidate pool is **location == "Shelf"**)
- Controlled New fallback (only after no Shelf match and only if unambiguous)
- Needs-action UX on no-match or ambiguity
- Sticky tray mapping using tray identity + `ams_slot_{slot}_tray_signature`
- Tie-break: **least remaining grams wins**
- Mismatch requires user intervention (no silent corrections)
- End-of-life: when a spool hits 0g and is removed, move to **location "Empty"**

## PHASE_2_5 Hard Rules (No Exceptions)

1. PHASE_2_5 **MUST NOT**:
   - Deploy
   - Restart HA / AppDaemon
   - Call `./scripts/manage_ha.sh`
   - Mutate runtime state via service calls

2. PHASE_2_5 **MAY**:
   - Edit code
   - Edit/add tests
   - Refactor for determinism and clarity

3. PHASE_2_5 must end with a structured summary:
   - STATUS: PASS/FAIL (PASS means code+tests updated coherently; does NOT imply deployed)
   - COMMANDS RUN:
   - ARTIFACTS / LOGS:
   - NEXT ACTION:

4. PHASE_2_5 must NOT automatically run TEST.
   It may only recommend TEST as NEXT ACTION.

## Required Policy Implementation

### Canonical Identity
- RFID spools are identified 1:1 by normalized `extra.rfid_tag_uid`.
- HA signature (HA_SIG) is written after successful match and persists for spool lifetime.

### RFID Matching
- Match ONLY by normalized `extra.rfid_tag_uid`.
- Consider ONLY spools where `location == "Shelf"`.
- If multiple matches: choose the one with the least remaining grams.
- If no Shelf match:
  - DO NOT create a Spoolman record
  - Set tray to NEEDS_ACTION
  - Send notification describing the RFID UID and required action

### Non-RFID Matching
- Primary candidate pool: `location == "Shelf"`.
- Fuzzy match is allowed but must be deterministic and bounded (avoid guessing when ambiguous).
- If multiple matches: choose least remaining grams.
- If no Shelf match, New fallback:
  - Consider `location == "New"` only after Shelf yields no match
  - Proceed ONLY if exactly one strong/unambiguous candidate
  - If New fallback used: send notification ("matched from New; moved to AMS slot")
  - If ambiguous: NEEDS_ACTION + notification

### Sticky Mapping (No Churn)
- Tray identity = `tray_uuid` if present else `tag_uid` (normalized).
- If tray identity unchanged AND current helper spool_id is valid in Spoolman:
  - DO NOT change spool_id
- Only update spool_id when tray identity changes OR helper spool_id is 0/invalid.

### Signature Handling
- On successful match:
  - Write spool_id helper
  - Write tray_signature helper
  - Write HA_SIG once (persist for spool lifetime)
- On empty tray:
  - Set spool_id to 0
  - Clear tray_signature

### Mismatch Handling
- If tray attributes disagree with matched spool: flag mismatch and require user intervention.

### End-of-Life (EOL)
- If spool remaining reaches 0g AND is removed: move spool in Spoolman to `location == "Empty"`.
- Exclude `Empty` from matching candidate pools.

## Required Tests
PHASE_2_5 must add/update tests proving:
- RFID Shelf-only match + tie-break least remaining
- RFID no-match => NEEDS_ACTION + notification (no auto-create)
- Non-RFID fuzzy match on Shelf + tie-break least remaining
- New fallback only after no Shelf match and only if unambiguous; else NEEDS_ACTION
- Sticky mapping prevents churn when tray identity unchanged
- Empty tray clears spool_id + tray_signature
- EOL: 0g + removal => move to Empty

## Post-Phase Next Action
- Recommend running **TEST** (and only then optionally DEPLOY) after PHASE_2_5 changes are reviewed/checked in.
------------------------------------------------------------------------

# PHASE_2_6 Workflow (Authoritative)

PHASE_2_6 implements the Non-RFID deterministic matching path (no tag_uid)
using Shelf-first selection, controlled New fallback, NEEDS_ACTION UX, and
least-remaining-grams tie-break. RFID behavior from PHASE_2_5 remains strict
and must NOT regress (no metadata fallback when tag_uid is present).

## PHASE_2_6 Hard Rules (No Exceptions)

1. PHASE_2_6 **MUST NOT**:
   - Deploy
   - Restart HA / AppDaemon
   - Call `./scripts/manage_ha.sh`
   - Mutate runtime state via service calls

2. PHASE_2_6 **MAY**:
   - Edit code
   - Edit/add tests
   - Refactor for determinism and clarity

3. PHASE_2_6 must end with a structured summary:
   - STATUS: PASS/FAIL (PASS means code+tests updated coherently; does NOT imply deployed)
   - COMMANDS RUN:
   - ARTIFACTS / LOGS:
   - NEXT ACTION:

## Required Policy Implementation (Non-RFID only)

### Entry Condition
- This phase applies ONLY when the tray has **no RFID tag_uid** (Non-RFID tray).
- If tag_uid is present, PHASE_2_5 rules apply (Shelf-only UID match; no metadata fallback).

### Candidate Pool Rules
- Primary candidate pool: Spoolman spools where `location == "Shelf"`.
- `location == "New"` is excluded from normal matching.
- `location == "Empty"` is always excluded from matching.

### Matching Inputs
Use tray metadata from the AMS tray (as available), including:
- vendor/brand (mapped to Spoolman `filament.vendor.name`)
- material (PLA/PETG/etc)
- filament name and/or color signal (name, color_hex, etc)
- HA signature (HA_SIG) when present (strong signal)

### Matching Algorithm (Deterministic + Bounded Fuzzy)
1) If HA_SIG is available and valid:
   - Prefer candidates that match HA_SIG (exact or deterministic normalized match).
2) Otherwise, compute a deterministic fuzzy score for candidates using:
   - vendor match (strong weight)
   - material match (strong weight)
   - filament name token overlap / normalized similarity (bounded)
   - color similarity if a reliable color signal exists (bounded)
3) Enforce determinism:
   - Do not “guess” when ambiguous.
   - If multiple candidates are within a small scoring band OR score is below threshold:
     - set NEEDS_ACTION (no bind) + send notification.
4) If exactly one unambiguous best match exists:
   - bind to it.
5) Tie-break:
   - If multiple candidates remain after matching filters, choose the one with the least remaining grams.

### New Fallback (Controlled)
If and only if there is **no match from Shelf**:
- Consider candidates where `location == "New"`.
- Proceed ONLY if exactly one strong/unambiguous candidate exists.
- If New fallback is used:
  - bind to it
  - move it to the AMS slot location
  - send notification stating New fallback occurred
- If New candidates are ambiguous:
  - NEEDS_ACTION + notification, no bind

### Writes on Success
On a successful bind (Shelf or New fallback):
- Write spool_id helper for the slot
- Write tray_signature helper (tray identity) for sticky mapping
- Stamp HA_SIG once for the spool lifetime (do not churn it)
- Move matched Spoolman spool location to the AMS slot location

### Sticky Mapping (No Churn)
- Tray identity = `tray_uuid` if present else `tag_uid` (normalized).
- For Non-RFID trays, tray_uuid may be absent; use the best available stable identity source.
- If tray identity unchanged AND current helper spool_id is valid in Spoolman:
  - DO NOT change spool_id
- Only update spool_id when tray identity changes OR helper spool_id is 0/invalid.

### No-Match UX
If no unambiguous match exists (Shelf and New fallback fails):
- Set tray status to NEEDS_ACTION / UNBOUND_ACTION_REQUIRED
- Send notification with:
  - slot number
  - vendor/material/color/name signals observed
  - whether Shelf had 0 matches vs ambiguous matches vs New ambiguous
- Do not bind. Do not create new Spoolman records.

## Required Tests
PHASE_2_6 must add/update tests proving:
- Non-RFID Shelf-only matching binds when unambiguous
- Non-RFID tie-break chooses least remaining grams
- Non-RFID no Shelf match triggers New fallback ONLY when unambiguous (bind + notify)
- Non-RFID ambiguity causes NEEDS_ACTION (no bind + notify)
- Sticky mapping prevents churn when tray identity unchanged
- Empty/New exclusions behave correctly (New excluded unless fallback; Empty always excluded)
- Regression: RFID behavior from PHASE_2_5 remains strict (tag_uid present + no Shelf UID match => NEEDS_ACTION, no bind)

## Post-Phase Next Action
- Recommend CHECKIN (commit) then run **TEST** (and only then optionally DEPLOY).

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


