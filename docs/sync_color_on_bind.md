# Sync Color on Bind

## Problem
When a spool is bound to an AMS slot (manual assign or RFID match), the Spoolman filament's `color_hex` may not match the AMS tray-reported color. This causes 3MF filament matching to fail (color mismatch between slicer profile and Spoolman record).

## Goal
Automatically update the Spoolman filament's `color_hex` to match the AMS tray-reported color when a spool is bound to a slot.

## Approach

### Phase 1: Backend
1. Add `sync_color_hex` field to `FILAMENT_IQ_SLOT_ASSIGNED` event data (from `scripts.yaml` assign script)
2. In `ams_rfid_reconcile.py` `_on_slot_assigned()`: if `sync_color_hex` is present and differs from Spoolman filament color, PATCH the filament
3. Also sync on RFID bind path (existing `bambu_rfid_manual_enroll_tag_to_spool` handler)

### Phase 2: Dashboard UI
1. Add color preview swatch to slot assignment card
2. Add toggle: "Sync color to Spoolman on assign" (default on)
3. Show color diff when mismatch detected

## Data Flow
```
Dashboard assign → scripts.yaml (ams_slot_assign_and_update)
  → fires FILAMENT_IQ_SLOT_ASSIGNED with sync_color_hex from tray attributes
  → AppDaemon _on_slot_assigned()
  → reads tray color attribute
  → PATCHes Spoolman filament color_hex if different
```

## Key Entities
- Tray color attribute: `state_attr('sensor.p1s_01p00c5a3101668_ams_N_tray_M', 'color')`
- Spoolman filament endpoint: `PATCH /api/v1/filament/{filament_id}` with `{"color_hex": "RRGGBB"}`
- Note: color is on the **filament** (shared across spools of same type), not the spool

## Risks
- Updating filament color affects ALL spools of that filament type, not just the assigned one
- Consider: should we update spool-level color (if Spoolman supports it) vs filament-level?
- Need color normalization (strip `#`, lowercase, handle 3-char shorthand)
