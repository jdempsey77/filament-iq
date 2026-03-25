# Adding a New AMS Unit to Filament IQ

## Overview

Adding a new AMS unit (e.g. a third AMS HT) requires changes across two repos:
the `filament-iq` package (slot mapping, card, monitor, tests) and the
`home_assistant` config repo (helpers, automations, scripts, dashboard). The
architecture is config-driven — `consumption_engine.py`, `threemf_parser.py`,
and `ams_print_usage_sync.py` require **zero changes**. All slot knowledge
flows from `base.py` through `build_slot_mappings()`.

HT3 (ams_index 130, slot 7) is the worked example throughout.

## Prerequisites

Confirm the HA entity exists and has the expected attribute schema:

```bash
source ~/code/home_assistant/scripts/deploy.env.local
curl -s -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" \
  "http://192.168.4.124:8123/api/states/sensor.p1s_YOUR_SERIAL_ams_INDEX_tray_1" \
  | python3 -m json.tool
```

Required attributes: `tag_uid`, `tray_uuid`, `remain`, `remain_enabled`,
`type`, `filament_id`, `color`, `tray_weight`, `empty`, `active`.

---

## Step 1 — base.py: register the unit

File: `apps/filament_iq/base.py`

In `_default_ams_units()`, add the new unit:

```python
# Before:
{"type": "ams_ht", "ams_index": 129, "slots": [6]},

# After:
{"type": "ams_ht", "ams_index": 129, "slots": [6]},
{"type": "ams_ht", "ams_index": 130, "slots": [7]},
```

Update the docstring and canonical location comment to include the new
ams_index and slot.

**Verification:** Run `build_slot_mappings()` and confirm slot 7 maps to
`sensor.{prefix}_ams_130_tray_1`.

## Step 2 — ams_rfid_reconcile.py: location map + bounds

File: `apps/filament_iq/ams_rfid_reconcile.py`

**Change 1:** Add to `DEPRECATED_LOCATION_TO_CANONICAL`:

```python
"AMS2_HT_Slot3": "AMS130_Slot1",
```

**Change 2:** Update the `LEGACY_LOCATION_PATTERN` regex to include the
new HT unit name.

**Change 3:** Find the error message with `Expected 1..N` and update the
upper bound.

## Step 3 — monitor.py: slot mapping

File: `monitor/monitor.py`

Add to `_SLOT_TO_AMS`:

```python
7: ("130", 1),
```

## Step 4 — Lovelace card: location tables

Three files, three additions:

**LocationSelect.jsx** — add to `LOCATIONS` array:
```javascript
{ value: 'AMS130_Slot1', label: 'HT3 · Slot 7' },
```

**SpoolsTab.jsx** — add to `LOCATION_TO_SLOT`:
```javascript
'AMS130_Slot1': 7,
```

**SpoolsTab.jsx** — add to `LocationBadge`:
```javascript
else if (location === 'AMS130_Slot1') label = 'HT3 · Slot 7'
```

Rebuild after all card edits:
```bash
cd packages/lovelace-card && npm run build
```

## Step 5 — HA helpers

In the `home_assistant` repo's `configuration.yaml`, add these input
helpers for the new slot (copy from slot 6, change `6` to `7` and
`ams_129` to `ams_130`):

- `input_text.ams_slot_7_spool_id`
- `input_text.ams_slot_7_unbound_reason`
- `input_text.ams_slot_7_expected_spool_id`
- `input_text.ams_slot_7_status`
- `input_text.ams_slot_7_tray_signature`
- `input_text.ams_slot_7_expected_color_hex`
- `input_text.ams_slot_7_rfid_pending_until`
- `input_text.ams_slot_7_filament_id`
- `input_number.ams_slot_7_gross_weight`
- `input_number.ams_slot_7_tare_override`
- `input_number.ams_slot_7_extras_weight`
- `input_select.ams_slot_7_spool_type`
- `input_select.ams_slot_7_select_spool`

Add 6 template sensors: `sensor.ams_slot_7_name`, `_remaining_g`,
`_material`, `_vendor`, `_color_hex`, `_status`.

Update `helpers_manifest.yaml` with slot 7 entries.

## Step 6 — HA automations and scripts

**automations.yaml:** Add `input_text.ams_slot_7_spool_id` and
`input_text.ams_slot_7_unbound_reason` to entity trigger lists in:
- AMS sync dropdown to bound spool
- AMS bind reminder push
- AMS bind reminder clear
- AMS populate spool dropdown on rest data

**scripts.yaml:** Add:
- `ams_assign_to_slot_7` script
- `ams_update_slot_7` script
- `ams_slot_7_assign_and_update` script
- Slot 7 entry in `sync_spool_dropdowns_to_bound`

## Step 7 — HA dashboard

**Edit `dashboards/dashboard.stage.yaml` only.** This is the most
fragile step — test on stage before promoting to prod.

Add a slot 7 card section. Copy the slot 6 (HT2) card block and
update all entity references from `ams_129` to `ams_130` and slot 6
to slot 7. The Jinja2 conditionals for `ai == 129` need a new
branch for `ai == 130`.

## Step 8 — apps.yaml

In the deployed `apps.yaml` on the HA host, add slot 7 to `AMS_SLOTS`:

```yaml
# Before:
# AMS_SLOTS not specified → uses default from base.py

# If explicitly set:
AMS_SLOTS: "1,2,3,4,5,6,7"
```

This activates the slot in the reconciler and usage sync.

## Step 9 — Tests

**test_ams_print_usage_sync.py:** Add `test_resolve_active_tray_slot_htN`
— assert `ams_index=130, tray_index=0` maps to slot 7.

**test_ams_rfid_reconcile.py:** Add assertion to
`test_deprecated_locations_map_to_canonical` — assert
`AMS2_HT_Slot3 → AMS130_Slot1`.

**test_base_validate_config.py:** Update `test_get_all_slots` expected
list to include slot 7.

## Step 10 — Deploy order

1. **HA config reload** — helpers must exist before AppDaemon starts
   listening for them
2. **AppDaemon restart** — picks up new slot from `base.py` defaults
3. **Card JS deploy** — `scp` built JS to HA, browser hard refresh
4. **Run full test suite** — `pytest tests/ -x -q`

## Step 11 — Validation

Post-deploy checklist:

- [ ] Slot 7 appears in dashboard with correct entity bindings
- [ ] Location dropdown in Filament IQ Manager card shows "HT3 · Slot 7"
- [ ] Reconciler log shows `slot=7 entity=sensor.p1s_*_ams_130_tray_1`
- [ ] No startup errors in AppDaemon log
- [ ] `RFID_RECONCILE_SUMMARY` shows `ok=7` (or correct count)
- [ ] All tests pass

## Layers that never change

These files are **slot-agnostic** and require zero edits when adding a
new AMS unit:

| File | Why |
|---|---|
| `consumption_engine.py` | Operates on `SlotInput` structs — no slot knowledge |
| `threemf_parser.py` | Matches by color/material/filament index — no slot knowledge |
| `ams_print_usage_sync.py` | Slot mapping derived from `build_slot_mappings()` at init |
| `spoolman_dropdown_sync.py` | Reads from Spoolman API — no slot knowledge |
| `filament_weight_tracker.py` | Iterates slot mappings dynamically |
