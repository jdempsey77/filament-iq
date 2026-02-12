# Spoolman filament tracking automations (Bambu P1S)

Two automations in `automations.yaml` link Spoolman to your printer:

- **Low filament:** Notify when the **active** spool (from printer `active_tray` or `input_select.active_filament_spool`) has &lt; 50 g (critical) or &lt; 100 g (warning). Triggers on tray/dropdown change or daily at 09:00.
- **Subtract after print:** When a print finishes, subtract the job’s filament weight from that same active spool in Spoolman and notify.

**Which spool is “active”:** The automations use the printer’s **active_tray** sensor when it reports a valid name; otherwise they use **input_select.active_filament_spool**. Keep the dropdown in sync with the AMS so it’s correct when the printer doesn’t report a matching name.

---

## 1. Entity IDs you must confirm or fill in

Do **not** guess. Look up in **Developer Tools → States** (filter as below).

| What | Where to look | What to paste |
|------|----------------|---------------|
| **Printer print status** | Filter: `p1s` and `print_status`. You should see something like `sensor.p1s_01p00c5a3101668_print_status`. | Already set in the automations. If your entity is different, replace every `sensor.p1s_01p00c5a3101668_print_status` in the subtract automation. |
| **State that means “finished”** | Click that sensor and watch **State** when a print completes. Typical values: `finish`, `finished`, or `completed`. | The automations already trigger on all three. If your integration uses another value (e.g. `complete`), add it to the `to:` list in **Spoolman subtract filament after print**. |
| **Filament used (grams)** | Filter: `p1s` and `weight` or `print`. Bambu integration often exposes **Print weight** (current job) as e.g. `sensor.p1s_01p00c5a3101668_print_weight`. Check that the state is in **grams** when a print finishes. | If your entity is different, in the **Spoolman subtract filament after print** automation replace `sensor.p1s_01p00c5a3101668_print_weight` in the `filament_used_g` variable. |
| **Job name (optional)** | Filter: `p1s` and `task`. Often `sensor.p1s_01p00c5a3101668_task_name`. | Already used in the notification. Change only if your entity_id differs. |
| **Spoolman spool IDs** | Filter: `spoolman_spool`. You get one entity per spool, e.g. `sensor.spoolman_spool_10`, `sensor.spoolman_spool_11`. The number is the **spool ID**. | In **both** automations, in the `spool_id` variable, replace the placeholder numbers `1`…`6` with your real IDs so they match the 6 dropdown options (see below). |
| **Filament in mm?** | If your Bambu integration only exposes length (mm), not weight (g), the subtract automation will not work as-is. | Use an entity that provides grams, or add a template sensor that converts mm→g using filament density, or implement a separate “notify and stop” path for mm in the automation. |

---

## 2. Mapping display name → Spoolman spool IDs

The automations map **display name** (from `active_tray` or `input_select.active_filament_spool`) → Spoolman spool ID. The option strings must match what the printer or dropdown shows. Update the IDs in **both** automations in `automations.yaml` (search for `spool_id: "{% set s`):

- `Overture PETG Black (AMS2 HT Slot 1)` → replace `1` with your spool ID  
- `Overture PETG Clear (AMS2 HT Slot 2)` → replace `2` with your spool ID  
- `Overture PLA White (AMS1 Slot 1)` → replace `3` with your spool ID  
- `Overture PLA Red (AMS1 Slot 2)` → replace `4` with your spool ID  
- `Overture PLA Black (AMS1 Slot 3)` → replace `5` with your spool ID  
- `Bambu Lab PLA Light Blue (AMS1 Slot 4)` → replace `6` with your spool ID  

**Where to edit:** In `automations.yaml`, search for `spool_id: "{% set s = states`. You’ll see six `{% elif s == '...' %}N{% endif %}` blocks. Change each `N` (1–6) to the Spoolman spool ID for that option. Option strings must match the dropdown **exactly** (including spaces and parentheses).

If an option is missing or renamed, add a new `{% elif s == 'Your exact option text' %}YOUR_SPOOL_ID{% endif %}` before the `{% else %}0{% endif %}`.

---

## 3. Validation and testing

### A) Low filament warning

1. **Reload automations:** Settings → Automations → ⋮ → Reload.
2. **Trigger once:** Change **input_select.active_filament_spool** to a spool whose remaining weight in Spoolman is &lt; 200 g (or &lt; 100 g for critical).  
   Or wait until 09:00 with a low-remaining spool selected.
3. **Check:** A persistent notification should appear: “Spoolman – low filament warning” or “Spoolman – critical low filament” with spool name and remaining grams.
4. **Traces:** Automations → **Spoolman low filament warning** → Run trace. Trigger again (e.g. change dropdown), then open the trace and confirm: trigger fired, conditions passed, and the correct `remaining_entity` / `remaining_g` are used.

### B) Auto-subtract after print

1. **Prerequisite:** The printer’s active tray (or the dropdown) must match the spool you used so the name→spool_id mapping resolves.
2. **Trigger:** Run a short print and let it reach **finished** (`finish` / `finished` / `completed`).
3. **Check:**  
   - In Spoolman (or Spoolman UI), that spool’s remaining weight should decrease by the print weight.  
   - A persistent notification: “Spoolman – filament used” with job name, grams used, spool name, and new remaining grams.
4. **If no notification:**  
   - Developer Tools → States: confirm `sensor.p1s_01p00c5a3101668_print_weight` (or your entity) shows &gt; 0 after the print.  
   - Confirm **input_select.active_filament_spool** matches one of the 6 mapped options and that the mapped spool_id is correct.  
   - Automations → **Spoolman subtract filament after print** → Run trace; run a print to completion and inspect the trace for trigger, conditions, and service call.

### C) Logs

- **Settings → Logs:** After a run, look for errors mentioning `spoolman`, `use_spool_filament`, or the automation aliases.
- If the subtract automation fails (e.g. “entity not found”), check that every `sensor.p1s_01p00c5a3101668_*` and `sensor.spoolman_spool_*` entity_id in the YAML exists under Developer Tools → States.

---

## 4. Summary

- **Low filament:** Uses printer **active_tray** when available, else **input_select.active_filament_spool**. Triggers on tray/dropdown change or daily 09:00; notifies if remaining &lt; 50 g (critical) or &lt; 100 g (warning).  
- **Subtract after print:** Same “active spool” source; on print finish calls `spoolman.use_spool_filament` with that spool and Bambu print weight (g), then notifies.  
- **Mapping:** Display name → Spoolman spool ID is in the `spool_id` template in both automations. Update IDs to match Developer Tools → States → `spoolman_spool_*`; ensure option strings match what the printer or dropdown shows.
