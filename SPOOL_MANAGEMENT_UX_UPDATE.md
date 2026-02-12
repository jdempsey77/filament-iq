# Spool Management UX Update

## Overview

Complete redesign of the spool management UI for improved clarity and ease of use. Each of the 6 AMS slots now has its own self-contained card with all controls needed to assign a spool and update its weight in one place.

## What Changed

### New User Experience (Per-Slot Cards)

Each slot card now contains:

1. **Current Status Header** (markdown)
   - Slot number and special designation (HT1/HT2 for slots 5-6)
   - Current spool name from Spoolman
   - Current remaining weight from Spoolman

2. **Spool Selection Dropdown**
   - Dedicated dropdown per slot: `input_select.ams_slot_N_select_spool`
   - Auto-populated with all Spoolman spools
   - Each slot maintains its own selection state

3. **Weight Inputs**
   - Gross weight (scale reading)
   - Spool type selector (Bambu Lab / Overture / Custom)
   - Custom tare override (only shown when "Custom" selected)

4. **Single Action Button**
   - "Assign & Update" button
   - Combines both operations:
     1. If dropdown has a spool selected, assigns it to this slot
     2. Calculates remaining = gross - tare
     3. Updates Spoolman with the calculated remaining weight

### Layout

- Grid layout: 3 columns (2 slots per row)
- Slots 1-2 in top row
- Slots 3-4 in middle row
- Slots 5-6 in bottom row

### Removed Elements

- Global "Assign from warehouse" dropdown (still exists as fallback but not shown)
- Separate "Update" and "Assign selected spool here" buttons (replaced with single "Assign & Update")
- Manual "Refresh list" button (dropdowns auto-refresh on HA start + when Spoolman changes)
- Conditional visibility logic (simplified to always show structure)

## Technical Implementation

### New Entities (configuration.yaml)

Added 6 new `input_select` entities:
- `input_select.ams_slot_1_select_spool`
- `input_select.ams_slot_2_select_spool`
- `input_select.ams_slot_3_select_spool`
- `input_select.ams_slot_4_select_spool`
- `input_select.ams_slot_5_select_spool`
- `input_select.ams_slot_6_select_spool`

### Updated Automation (automations.yaml)

`ams_populate_spool_dropdown_on_rest_data`:
- Now populates all 7 dropdowns (global + 6 per-slot)
- Triggers: HA start + `sensor.ams_spool_list_options` changes
- All dropdowns get identical options from Spoolman integration entities

### New Scripts (scripts.yaml)

Added 6 combined scripts:
- `script.ams_slot_1_assign_and_update`
- `script.ams_slot_2_assign_and_update`
- `script.ams_slot_3_assign_and_update`
- `script.ams_slot_4_assign_and_update`
- `script.ams_slot_5_assign_and_update`
- `script.ams_slot_6_assign_and_update`

Each script:
1. Parses the slot's dropdown selection (e.g., "11 - Gray" → ID 11)
2. If ID > 0, sets `input_text.ams_slot_N_spool_id` to that ID
3. Calculates remaining weight using gross, spool type, and tare
4. Calls `spoolman.patch_spool` to update Spoolman

### Dashboard (dashboard.stage.yaml)

Redesigned the entire "Spools" view:
- Updated instructions markdown
- 6 self-contained slot cards (3-column grid)
- Each card uses `vertical-stack` with:
  - Markdown header (dynamic current status)
  - Per-slot dropdown
  - Gross weight input
  - Spool type selector
  - Conditional tare override
  - Single "Assign & Update" button

## Workflow Example

### Before (Old UX)
1. Select spool from global "Assign from warehouse" dropdown
2. Navigate to correct slot card
3. Click "Assign selected spool here"
4. Enter gross weight and spool type
5. Click "Update (gross − tare → Spoolman)"
6. Repeat for next slot (confusing if you forgot which spool was selected)

### After (New UX)
1. Go to Slot 1 card
2. Select spool from dropdown: "11 - Gray"
3. Enter gross weight: 1200g
4. Select spool type: "Bambu Lab (plastic)"
5. Click "Assign & Update" — done!
6. Move to Slot 2, repeat (each slot is independent)

## Deployment

### Files Changed

1. `/Users/jdempsey/code/home_assistant/configuration.yaml`
   - Added 6 `input_select` entities (ams_slot_N_select_spool)

2. `/Users/jdempsey/code/home_assistant/automations.yaml`
   - Updated `ams_populate_spool_dropdown_on_rest_data` to populate all 7 dropdowns

3. `/Users/jdempsey/code/home_assistant/scripts.yaml`
   - Added 6 combined `ams_slot_N_assign_and_update` scripts

4. `/Users/jdempsey/code/home_assistant/dashboards/dashboard.stage.yaml`
   - Complete redesign of "Spools" view with 6 self-contained slot cards

### Deployment Steps

```bash
cd /Users/jdempsey/code/home_assistant
./scripts/manage_ha.sh configuration.yaml automations.yaml scripts.yaml dashboards/dashboard.stage.yaml
```

### Verification

After deployment and HA reload:

1. Check `/lovelace-stage` (stage dashboard)
2. Verify 6 slot cards visible in 3-column grid
3. Confirm each slot's dropdown has options (auto-populated)
4. Test Slot 1:
   - Select "11 - Gray" from dropdown
   - Enter gross weight: 1200
   - Select spool type: "Bambu Lab (plastic)"
   - Tap "Assign & Update"
5. Verify in Developer Tools → States:
   - `input_text.ams_slot_1_spool_id` = "11"
   - `sensor.ams_slot_1_name` = "Gray"
   - `sensor.ams_slot_1_remaining_g` = calculated remaining (1200 - tare)
6. Check Spoolman web UI: spool 11 remaining weight updated

## Benefits

- **Clarity**: Each slot is self-contained, no confusion about global state
- **Efficiency**: One button per slot instead of two separate operations
- **Independence**: Work on multiple slots without losing context
- **Consistency**: Same workflow pattern for all 6 slots
- **Safety**: Dropdown selection is per-slot, can't accidentally assign wrong spool to wrong slot

## Rollback

If needed, revert via git:
```bash
git diff HEAD~1 configuration.yaml automations.yaml scripts.yaml dashboards/dashboard.stage.yaml
git checkout HEAD~1 -- configuration.yaml automations.yaml scripts.yaml dashboards/dashboard.stage.yaml
./scripts/manage_ha.sh configuration.yaml automations.yaml scripts.yaml dashboards/dashboard.stage.yaml
```
