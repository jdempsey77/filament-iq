# AMS Slot Manager — Home Assistant–first, Spoolman-backed

## Design principles

- **Home Assistant is primary:** UI, control plane, and automation live in HA.
- **Spoolman is storage only:** Filament definitions, spool records, remaining weight. No day-to-day operations in Spoolman UI except add/remove filament types or warehouse spools.
- **All AMS operations in HA:** Assign slot, set weight (scale-first), reload — all from dashboards and scripts.

---

## AMS slot model

- **6 AMS slots.** Each slot has a **reserved** spool record in Spoolman.
- **Slot → Spool:** We try spool ID from HA (`ams_slot_N_spool_id`, initial 1–6). If that spool is missing (404), the manager creates a placeholder via POST and **persists whatever ID Spoolman returns** in HA. The Spoolman API does **not** let you set `id` on create—IDs are always server-assigned (sequential). So we never rely on IDs being 1–6 or any specific range (e.g. 1000+); we always rely on the persisted mapping.
- **Persistence:** `input_text.ams_slot_1_spool_id` … `ams_slot_6_spool_id` hold the Spoolman spool ID for each slot.
- **Placeholder marker:** Created slot spools use `location = "HA_AMS_SLOT_N"` and `comment = "HA_AMS_SLOT=N"` so they can be identified and not treated as warehouse spools.

---

## Spool assignment semantics

- **Source spool** = warehouse spool in Spoolman (any spool not used as an AMS slot).
- **Assigning a spool to an AMS slot** means:
  1. Copy **filament_id** from the source spool to the slot spool record.
  2. Compute **remaining filament (g)** from the weight model (below).
  3. Write **remaining_weight** (and optionally **spool_weight** = tare) into the **slot** spool.
- The **AMS slot spool** is the active record; the **source spool** is never modified by the manager.

---

## Weight model (scale-first UX)

- UI accepts **gross scale weight** (what you read from the scale).
- **remaining_grams = max(0, gross_weight_grams − tare_grams)**
- **remaining_grams** is written to the AMS slot spool in Spoolman (via `spoolman.patch_spool` or REST PATCH).
- Tare comes from **spool type** or **custom override** (see below).

---

## Spool type and tare (extensible)

- **Spool type** = which kind of physical spool is in that slot (plastic reusable, cardboard, custom).
- Spool type defines **default tare (g)**. Stored as HA helpers (no hardcoded brands).
- **Tare resolution:**
  - If spool type ≠ custom → use that type’s default tare.
  - If spool type = custom → use the **per-slot tare override** (user-entered).
- **Persist per AMS slot:** Each slot has `input_select.ams_slot_N_spool_type` and optionally `input_number.ams_slot_N_tare_override` (used when type = custom). Default tares live in `input_number.tare_plastic_reusable`, `tare_cardboard`, `tare_custom` (0).

---

## Configuration summary

| What | Where |
|------|--------|
| Spoolman API base URL | `rest_command` URL (use `!secret spoolman_base_url` or equivalent). |
| Placeholder filament ID | One filament must exist in Spoolman; set `input_number.ams_placeholder_filament_id` (or in script) for create-placeholder. |
| Spool types | `input_select.ams_slot_N_spool_type` options: `plastic_reusable`, `cardboard`, `custom`. Default tares: `input_number.tare_plastic_reusable` (256), `tare_cardboard` (185), `tare_custom` (0). |
| Slot → spool ID | `input_text.ams_slot_1_spool_id` … `ams_slot_6_spool_id` (initial `"1"` … `"6"`; updated by ensure script if placeholder created). |

---

## Startup: ensure slot spools

1. On HA start, automation runs **script.ams_ensure_slot_spools**.
2. For each slot N (1…6):
   - **GET** Spoolman spool by ID = current `input_text.ams_slot_N_spool_id`.
   - If **404** (or missing): **POST** create placeholder spool (filament_id = placeholder, location = `HA_AMS_SLOT_N`, comment = `HA_AMS_SLOT=N`), then set `input_text.ams_slot_N_spool_id` to the returned spool **id**.
3. No change if spool exists (IDs 1–6 or previously created placeholders).

---

## Day-to-day flows in HA

1. **Weigh and set remaining (scale-first)**  
   Enter **gross weight (g)** and choose **spool type** (or custom + tare override). Tap **Update** → script computes **remaining = max(0, gross − tare)** and patches the slot spool.

2. **Assign from warehouse**  
   Enter **source spool ID**, choose **slot**, tap **Assign** → script GETs source spool, copies **filament_id** to slot spool, sets **remaining_weight** from current gross − tare for that slot (and optionally **spool_weight** = tare).

3. **Reload Spoolman**  
   Existing **Reload Spoolman** button reloads the Spoolman config entry so HA entities reflect latest data.

---

## Files touched

- **configuration.yaml:** `rest_command` (Spoolman GET/POST/PATCH), `input_text` (slot spool IDs), `input_number` (gross, tare overrides, default tares, placeholder filament id), `input_select` (spool type per slot).
- **scripts.yaml:** `ams_ensure_slot_spools`, `ams_update_slot_N_weight` (×6 or parameterized), `ams_assign_source_to_slot_N` (×6 or with slot in data).
- **automations.yaml:** Trigger `homeassistant.start` → `script.ams_ensure_slot_spools`.
- **dashboards/dashboard.stage.yaml:** Spool management view updated for gross weight, spool type, tare override, computed remaining, Assign from warehouse.
- **SPOOLMAN_WHEN_YOU_SWAP.md:** Updated for AMS Slot Manager flow and Spoolman UI usage.

---

## Validation

- **Spoolman:** Ensure at least one filament exists; use its ID as placeholder filament for new slot spools.
- **rest_command:** Use correct Spoolman base URL (e.g. `http://host:8080`); no trailing slash on base path for `/api/v1/spool`.
- **Entity IDs:** All slot spool references use `input_text.ams_slot_N_spool_id` (or derived sensor) so reserved IDs 1–6 or manager-created IDs both work.
