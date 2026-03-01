# Cursor Skills

Skills:

TEST
DEPLOY
ANALYZE
ROLLBACK
GUARDRAILS
PHASE

---

TEST:
- Runs full deterministic gate suite
- Produces PASS/FAIL checklist

---

ANALYZE:
- **Strict read-only investigation.** Never mutates state; no restart; no deploy; report only.
- Must not: edit files, create files, commit, run TEST/DEPLOY/ROLLBACK, call `./scripts/manage_ha.sh`, restart services, or make service calls.
- May: read files, trace control/data flow, identify regressions, propose patch snippets (NOT applied), recommend next skill.
- **Output (full phase structure):**
  - Executive Summary
  - Observed Evidence
  - System Walkthrough (optional)
  - Findings
  - Root Cause Hypothesis
  - Blast Radius / Risk
  - Suggested Fix (patch only, not applied)
  - Suggested Validation
  - Next Action
- ANALYZE must stop after delivering the report. No file edits, no deploy.

---

ROLLBACK:
- Provide safe rollback steps; redeploy only via `./scripts/manage_ha.sh`.

GUARDRAILS:
- Restate and enforce repo rules (see docs/cursor_skill.md).

PHASE:
- Show current maturity posture (gates, flags, what TEST/DEPLOY enforce).

---

## Dashboard update process
The main dashboard is **storage-type** (UI-managed). There is **no script deploy** for it. Updates are done by **manual copy/paste**: edit the source YAML (e.g. `dashboards/dashboard.test.storage.yaml` or stage dashboard) in the repo for version control, then copy the relevant YAML into Home Assistant (**Settings → Dashboards → … → Edit → Raw configuration**) and save. Deploy script only pushes **stage** dashboard to `/lovelace-stage` when `dashboards/dashboard.stage.yaml` is changed (`./scripts/manage_ha.sh --stage`).
