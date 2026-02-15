# Spoolman reset to 0 and reserved IDs 1–6

This doc describes how to reset Spoolman to zero spools and end up with exactly six spools whose IDs are **1, 2, 3, 4, 5, 6** (reserved for AMS slots 1–6). Home Assistant is then configured so each slot uses the matching spool ID.

---

## Full reset: nuke the database (recommended for clean slate)

When you want to wipe everything and start fresh:

1. **Stop Spoolman** (however you run it: Docker, OctoPrint plugin, systemd, etc.).
2. **Remove the Spoolman database file.**  
   - Typical locations: Spoolman data directory (e.g. `spoolman.db` or `database.db` inside the Spoolman data/volume). If using Docker, the path is inside the container’s volume; if using the OctoPrint Spoolman plugin, check the plugin’s or OctoPrint’s data directory for a SQLite file used by Spoolman.  
   - Optional: rename or copy the file (e.g. `spoolman.db.bak`) instead of deleting, so you can roll back.
3. **Start Spoolman again.** It will create a new empty database on first run.
4. **Create one filament** in Spoolman (UI or API). Spoolman requires at least one filament before you can create any spool. Example: name "Generic PLA 1.75mm", set diameter and density, save. Note the filament’s ID (often `1` on a fresh DB).
5. **Create the six slot spools** in order (see [Create six spools in order](#2-create-six-spools-in-order-reserve-16) below)—by hand in the UI or with the seed script—so they receive IDs 1–6.
6. **Set HA slot IDs to 1–6** (see [Set HA slot IDs to 1–6](#3-set-ha-slot-ids-to-1-6) below).

---

## Reserve IDs 1–6

Spoolman assigns spool IDs sequentially (auto-increment). You cannot set `id` on create. So:

- After Spoolman has **zero spools**, create exactly **six spools in order**: first for slot 1, then slot 2, … then slot 6.
- Spoolman will assign IDs **1, 2, 3, 4, 5, 6** in that order.
- Any spools you create later (e.g. "Add new spool" from HA) will get ID 7, 8, 9, …

**Slot → location** (must match HA/scripts):

| Slot | Location      |
|------|---------------|
| 1    | AMS1_Slot1    |
| 2    | AMS1_Slot2    |
| 3    | AMS1_Slot3    |
| 4    | AMS1_Slot4    |
| 5    | AMS2_HT_Slot1  |
| 6    | AMS2_HT_Slot2  |

---

## 1. Reset Spoolman to 0

**Option A – Delete all spools only (keep filaments/vendors)**

- In Spoolman UI: delete every spool. Or via API: `GET /api/v1/spool`, then `DELETE /api/v1/spool/{id}` for each.
- Leaves filaments/vendors intact so you can create spools with an existing `filament_id`.

**Option B – Full DB reset (SQLite) — “nuke from orbit”**

- Follow the steps in [Full reset: nuke the database](#full-reset-nuke-the-database-recommended-for-clean-slate) above: stop Spoolman, remove (or rename) the SQLite database file, restart, create one filament, then the six spools.

---

## 2. Create six spools in order (reserve 1–6)

Spoolman requires **at least one filament** before you can create any spool. On an empty DB, do this first, then run the seed script.

### 2a. Create one filament (required on empty DB)

**Option 1 – Spoolman UI**  
If your add-on exposes a web UI: add a filament (e.g. name “Generic PLA 1.75mm”, diameter 1.75, density 1.24). Note the filament ID (will be `1` on a fresh DB).

**Option 2 – API (curl)**  
Replace `http://localhost:7912` with your Spoolman URL (from HA: `input_text.spoolman_base_url`, or add-on port, often 7912):

```bash
curl -s -X POST "http://localhost:7912/api/v1/filament" \
  -H "Content-Type: application/json" \
  -d '{"name":"Generic PLA 1.75mm","diameter":1.75,"density":1.24}'
```

Response will include `"id": 1`. Use that ID in the seed script below.

### 2b. Run the seed script (creates spools 1–6)

From your machine (or wherever you have the repo and Python):

```bash
cd /path/to/home_assistant/spoolman_import
SPOOLMAN_URL=http://localhost:7912 python3 seed_six_slot_spools.py --filament-id 1
```

Replace `http://localhost:7912` with your Spoolman base URL and `1` with your filament ID. The script creates six spools in order (locations AMS1_Slot1 … AMS2_HT_Slot2); they will get IDs 1–6.

**Alternative:** Create the six spools by hand in Spoolman UI **in order** (slot 1 first, then 2, … 6), each with the location from the table above and `remaining_weight` 0.

---

## 3. Set HA slot IDs to 1–6

In Home Assistant, ensure:

- `input_text.ams_slot_1_spool_id` = `"1"`
- `input_text.ams_slot_2_spool_id` = `"2"`
- … through …
- `input_text.ams_slot_6_spool_id` = `"6"`

**How:**

- **Developer Tools → States:** Edit each `input_text.ams_slot_*_spool_id` and set the value to the string `"1"`, `"2"`, … `"6"`.
- **Note:** `configuration.yaml` sets `initial: "1"` … `initial: "6"` for these entities. That only applies to **new** entities; existing persisted values are not overwritten on reload. So after a Spoolman reset, if HA already had other IDs stored, you must set them once to 1–6 as above.

Then **Reload Spoolman integration** (dashboard button or Services → `script.reload_spoolman_integration`).

---

## 4. After reset

- Use **Spool management** in HA to assign spool type and enter scale weight for each slot; tap **Assign & Update** per slot (see [SPOOL_WEIGHT_INPUT_STEPS.md](SPOOL_WEIGHT_INPUT_STEPS.md)).
- Prints will keep weight updated via the existing finish automation (`use_spool_filament` per slot).
- Any new spool you add later (from HA or Spoolman) will get ID 7+ and appear in the "Select Spool" dropdown; you can assign it to a slot and use the same weight-input flow.
