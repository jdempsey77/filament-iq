# Spool Management (AMS Slot Manager) – Summary

## What we’re trying to accomplish

**Goal:** A single Spool management page in Home Assistant where you can:

1. **See and edit each AMS slot (1–6)**  
   For each slot: which spool is in it (name, remaining weight in Spoolman), spool type (Bambu Lab / Overture cardboard / Custom), gross weight on scale, tare override, and an **Update** button that writes remaining (gross − tare) to Spoolman.

2. **Change which spool is in a slot**  
   Pick a spool from a **dropdown list of all spools in Spoolman** (“Assign from warehouse”), then tap **Assign selected spool here** on the slot you want. No typing spool IDs.

3. **Keep everything in sync with Spoolman**  
   Slot → spool ID is stored in HA (`input_text.ams_slot_N_spool_id`). Name and remaining weight are shown from Spoolman (template sensors + Spoolman integration). Low-filament and subtract-after-print automations use the same mapping.

---

## What we have built

### Configuration (`configuration.yaml`)

- **Inputs**
  - `input_text.spoolman_base_url` – Spoolman API base URL (used by REST sensor; can also be set in Spoolman integration settings).
  - `input_text.ams_slot_1_spool_id` … `ams_slot_6_spool_id` – Persisted Spoolman spool ID per slot.
  - `input_number.ams_assign_source_spool_id` – Numeric “currently selected” spool ID (set by automation when you pick from the dropdown).
  - `input_select.ams_slot_N_spool_type` – Bambu Lab / Overture cardboard / Custom per slot.
  - `input_number.ams_slot_N_gross_weight`, `ams_slot_N_tare_override` – Scale and tare inputs per slot.
  - `input_select.ams_assign_source_spool` – **Dropdown for “Assign from warehouse”** (options are set by script from a sensor).

  - **Template sensors**
  - `sensor.ams_slot_N_name`, `sensor.ams_slot_N_remaining_g` – Name and remaining (g) per slot from Spoolman (using slot’s persisted spool ID).
  - `sensor.ams_spool_list_options` – **From Spoolman integration only (no REST):** `integration_entities('spoolman')` → filter by id/spool_id + label → `attributes.options` = list of `"ID - LABEL"`. State = entity count. Always returns a list (possibly empty).
  - `sensor.ams_spool_list_options_debug` – **Temporary:** state = count of Spoolman entities; attributes = first 10 entity_ids and first 10 option strings. Remove once Slot 1 works.

### Scripts (`scripts.yaml`)

- **reload_spoolman_integration** – Reloads Spoolman config entry only. Does **not** run refresh_ams_spool_list.
- **refresh_ams_spool_list** – Optional manual refresh: re-applies dropdown from `sensor.ams_spool_list_options.options` only (no Spoolman reload, no REST).
- **ams_assign_to_slot_1** … **ams_assign_to_slot_6** – Set `input_text.ams_slot_N_spool_id` to the current `input_number.ams_assign_source_spool_id` (only if &gt; 0).
- **ams_update_slot_1** … **ams_update_slot_6** – Compute remaining = max(0, gross − tare by type), call `spoolman.patch_spool` for that slot’s spool ID.

### Automations (`automations.yaml`)

- **ams_populate_spool_dropdown_on_rest_data** – Trigger: HA start **and** `sensor.ams_spool_list_options` state change. Condition: options attribute non-empty. Action: `input_select.set_options` with `["— Select spool —"] + options`. Populates dropdown from Spoolman entities only (no REST).
- **ams_assign_source_spool_from_select** – When `input_select.ams_assign_source_spool` changes, parse the selected option (e.g. `"27 - Blue"` → 27) and set `input_number.ams_assign_source_spool_id` so the assign-to-slot scripts have a numeric ID.

### Dashboard (`dashboards/dashboard.stage.yaml` – Spool management view)

- Top: **Back to 3D Printer**, **Reload Spoolman**.
- Instructions: scale-first flow; “To change which spool is in a slot: select a spool below, then tap **Assign selected spool here** on that slot.”
- **Refresh list** button + **Assign from warehouse – select spool** dropdown (entity: `input_select.ams_assign_source_spool`).
- Grid of 6 slots. Each slot: name, remaining (g), spool type, gross weight, tare override, **Update** button, **Assign selected spool here** button.
- Bottom: “Assign from warehouse” copy and (optionally) the source spool ID for reference.

### Cursor rules (`.cursorrules`)

- Deploy-steps table: which command to run when (e.g. `--stage` for dashboard, `--automations` for automations, `--scripts` for scripts, `--config` for configuration).

### Docs

- **SPOOL_MANAGEMENT_GET_WORKING.md** – Deploy steps and quick test/troubleshooting.
- **AMS_SLOT_MANAGER.md** – Design and behavior (may be slightly out of date with latest UI).

---

## Current issues

1. **Dropdown stays empty after “Refresh list”**  
   `input_select.ams_assign_source_spool` ends up with only the placeholder or no options. So either:
   - `sensor.ams_spool_list_options` has an empty (or missing) `options` attribute when the script runs, or  
   - The script runs before the template/REST sensor have ever had data (e.g. right after boot), or  
   - The template that builds `options` (from Spoolman entities or from REST sensor state) never produces a list in this setup.

2. **Spinning cursor after “Reload Spoolman” when opening the list**  
   Reload Spoolman triggers the Spoolman sensor platform (and our script runs afterward). Opening the dropdown then can show a spinner, suggesting the UI or backend is waiting on something slow (e.g. Spoolman or REST sensor setup still running, or a large options list).

3. **We deliberately avoid triggering updates on Refresh**  
   Calling `homeassistant.update_entity` on the REST or template sensor caused HA to restart or hang (logs showed “Setup of sensor platform spoolman/rest is taking over 10 seconds”). So “Refresh list” only repopulates the dropdown from **existing** template sensor data; it does not trigger a new fetch.

---

## Data flow (intended)

1. **Spool list for dropdown**
   - **REST** `spoolman_spool_list_api`: state = count. **REST** `spoolman_spool_list_options_raw`: state = JSON string of `["id - name", ...]` built from value_json. **Template** `ams_spool_list_options`: options attribute = fromjson(options_raw state); always a list.
   - **Automation** `ams_populate_spool_dropdown_on_rest_data`: trigger = options_raw state change; condition = template sensor options non‑empty; action = set_options with `["— Select spool —"] + options`. Dropdown fills when REST data arrives; no Reload Spoolman needed.
   - Both REST sensors update at startup (may take 10+ s) and every 3600 s.

2. **When you tap “Refresh list”**  
   Script runs `input_select.set_options` from current template sensor options (safe; no `update_entity`). Optional; automation already populates when REST data is available.

3. **When you select a spool and tap “Assign selected spool here”**  
   Automation has already set `input_number.ams_assign_source_spool_id` from the selection. The script sets `input_text.ams_slot_N_spool_id` to that value. Slot name/remaining then update from Spoolman (template sensors).

---

## What’s working vs not (as of this summary)

| Piece | Status |
|-------|--------|
| Slot cards (name, remaining, type, gross, tare, Update, Assign button) | Working (per your earlier “no errors” and layout). |
| Assign-to-slot scripts (set slot spool ID from selection) | Implemented; need a non-empty dropdown to test. |
| Dropdown populated from Spoolman | **Not working** – list stays empty after Refresh list. |
| Reload Spoolman + open list | Spinner when opening list (slow or blocking). |
| Refresh list without restart | Fixed (no `update_entity` call). |

---

## Possible next steps (for the empty list)

- **In HA:** In Developer Tools → States, check:
  - `sensor.ams_spool_list_options` – Is there an `options` attribute? Is it a list with entries or empty?
  - `sensor.spoolman_spool_list_api` – What is the state? (e.g. `"11 - Gray,27 - Blue"` or `unknown`?)
- If the REST sensor state is good but the template sensor’s `options` is empty, the fallback branch (splitting REST state) may not be running or may be wrong (e.g. attribute type, or template error).
- If the REST sensor state is `unknown`/empty, Spoolman URL or network from HA to Spoolman may be wrong, or the REST sensor may not have completed its first run yet (wait well past the “taking over 10 seconds” setup).
- Consider simplifying to a single source (e.g. only REST sensor, or only Spoolman entities) and ensure that one path reliably fills `options` and that the script runs only after that sensor has data (e.g. after first successful REST scan or after Spoolman integration is ready).

This file is the single summary of the effort and current state; use it plus the deploy steps in **SPOOL_MANAGEMENT_GET_WORKING.md** and the table in **.cursorrules** for deploy/test.
