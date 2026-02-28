# ANALYZE: 11 Automation Repair Errors (Read-Only)

**Context:** Errors are 4–5 days old; `input_text` integration was broken during that period, causing cascading failures for automations that use `input_text.set_value`. Some may have self-healed now that `input_text` is working; some may be genuinely broken or from automations no longer in YAML.

**How to check if a service exists now:**  
`curl -sS -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" "$HOME_ASSISTANT_URL/api/services" | jq '.[] | select(.domain == "input_text")'`  
Or in HA: **Developer Tools → Services** and search for `input_text` / `input_text.set_value`.

---

## 1. Per-error summary table

| # | Automation name (from HA) | Location in repo | Failing action/service | Service exists now? | Likely cause | Recommended action |
|---|---------------------------|------------------|------------------------|---------------------|---------------|--------------------|
| 1 | P1S – record trays used during print | `automations.yaml` id `p1s_record_trays_used_during_print` | `action: input_text.set_value` (target `input_text.p1s_trays_used_this_print`) | Yes (if input_text loaded) | input_text outage | **Ignore (self-healed)** |
| 2 | P1S Phase 2A – non-RFID spool usage from print_weight on finish | **Not in repo** (no alias match in automations.yaml) | Unknown (likely `input_text.set_value` or similar) | — | Was transient (UI/storage or removed) | **Dismiss** (not in YAML; remove from HA if still in storage) |
| 3 | P1S – snapshot remaining on print finish | `automations.yaml` id `p1s_remaining_snapshot_on_finish` | Multiple `service: input_text.set_value` (e.g. `p1s_finish_automation_checkpoint`, `ams_slot_*_spool_id`) | Yes | input_text outage | **Ignore (self-healed)** |
| 4 | P1S – capture active tray entity during print | `automations.yaml` id `p1s_capture_active_tray_entity` | `action: input_text.set_value` (e.g. `p1s_trays_used_this_print`, `p1s_last_tray_entity`) | Yes | input_text outage | **Ignore (self-healed)** |
| 5 | P1S – seed start grams on tray first active | `automations.yaml` id `p1s_remaining_snapshot_on_tray_first_active` | `action: input_text.set_value` (e.g. `p1s_init_seed_debug`) | Yes | input_text outage | **Ignore (self-healed)** |
| 6 | AMS – clear expected state when tray is Empty | **Not in repo** (no alias match) | Unknown | — | Transient or UI automation | **Dismiss** (verify not in YAML; remove from HA if duplicate) |
| 7 | Spoolman – refresh filament dropdown on startup | **Not in repo** (closest: `AMS – populate Add spool filament dropdown` id `ams_filament_list_populate_dropdown`) | Likely `input_select.set_options` or `input_text.set_value` | Yes | input_text/input_select outage or old name | **Ignore or Dismiss** (if same automation, self-healed; else remove from storage) |
| 8 | Spoolman: Auto-heal extra JSON (15m) | **Not in repo** | Unknown | — | Transient / removed | **Dismiss** (remove from HA if still in storage) |
| 9 | P1S Phase 2A – non-RFID… (duplicate) | Same as #2 | Same as #2 | — | Same as #2 | **Dismiss** |
| 10 | Spoolman truth sync remaining after print end | **Not in repo** | Unknown | — | Transient / removed | **Dismiss** (remove from HA if still in storage) |
| 11 | P1S – init remaining filament snapshots | `automations.yaml` id `p1s_remaining_snapshot_init` | `action: input_text.set_value` (e.g. `p1s_print_job_key`, `p1s_init_seed_debug`, `p1s_tray_remaining_start_json`, `p1s_tray_remaining_end_json`) | Yes | input_text outage | **Ignore (self-healed)** |

---

## 2. Grouped by likely cause

### A) Likely input_text outage (self-healed) — **Ignore**

All of these live in `automations.yaml` and use `input_text.set_value`. When `input_text` was broken, the action would be reported as "unknown." Now that the integration is fixed, the same automations should run without that error.

| Automation | Id | First failing step in action list |
|------------|-----|-----------------------------------|
| P1S – record trays used during print | `p1s_record_trays_used_during_print` | `input_text.set_value` → `p1s_trays_used_this_print` |
| P1S – snapshot remaining on print finish | `p1s_remaining_snapshot_on_finish` | `input_text.set_value` → `p1s_finish_automation_checkpoint` |
| P1S – capture active tray entity during print | `p1s_capture_active_tray_entity` | `input_text.set_value` → `p1s_trays_used_this_print` |
| P1S – seed start grams on tray first active | `p1s_remaining_snapshot_on_tray_first_active` | `input_text.set_value` → `p1s_init_seed_debug` (inside conditional) |
| P1S – init remaining filament snapshots | `p1s_remaining_snapshot_init` | `input_text.set_value` → `p1s_print_job_key` |

**Action:** No code change. Clear/dismiss the repair entries in HA if desired; re-run one automation manually to confirm it runs.

---

### B) Not in repo (transient / UI / removed) — **Dismiss**

These names do not match any `alias` in `automations.yaml`. They may be from HA storage (UI-created), old versions, or renames.

| Automation name |
|-----------------|
| P1S Phase 2A – non-RFID spool usage from print_weight on finish (#2, #9) |
| AMS – clear expected state when tray is Empty |
| Spoolman – refresh filament dropdown on startup |
| Spoolman: Auto-heal extra JSON (15m) |
| Spoolman truth sync remaining after print end |

**Action:** In HA: **Settings → Automations & Scenes → 11 repairs**. For each of the above, either "Ignore" or fix/delete the automation if it still exists in storage. If you no longer use them, delete from storage so they don’t reappear in the repair list.

---

### C) Genuinely broken (need fix)

**None identified.** Every automation that exists in the repo and appears in the 11 errors uses standard actions (`input_text.set_value`, `input_select.set_options`, etc.) that are valid once the corresponding integration is loaded. No wrong service name or missing entity was found in YAML for these.

---

## 3. Verification steps (read-only)

1. **Confirm input_text is loaded**  
   In HA: Developer Tools → Services → search `input_text` → ensure `input_text.set_value` exists.

2. **Confirm no repair after re-run**  
   Developer Tools → Automations → run one of the 5 automations in group A (e.g. "P1S – init remaining filament snapshots") with a test trigger if possible. Check that the repair list does not gain a new "unknown action" entry.

3. **Optional: list services via API**  
   `curl -sS -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" "$HOME_ASSISTANT_URL/api/services" | jq 'keys'`  
   Ensure `input_text` is present and contains `set_value`.

---

## 4. Summary

| Group | Count | Recommended action |
|-------|-------|--------------------|
| A) input_text outage (in repo) | 5 | **Ignore** (self-healed) |
| B) Not in repo / transient | 5 (incl. 1 duplicate) | **Dismiss** (and remove from storage if not needed) |
| C) Genuinely broken | 0 | — |

**Root cause hypothesis:** The "uses an unknown action" errors coincided with the `input_text` integration being broken (helpers unavailable, `input_text.set_value` missing or failing). Repairs were logged at that time. Now that `input_text` is working again, the YAML automations that only use standard actions should be fine; the remaining repair items are either duplicates or from automations that are not in the current YAML (UI/storage or removed).

**Next action:** In HA, open the 11 repairs and "Ignore" or "Dismiss" each. Optionally run one of the P1S/AMS automations to confirm no new errors. If any repair persists after that, note the exact automation id/alias and the action HA reports as unknown and re-analyze that single case.
