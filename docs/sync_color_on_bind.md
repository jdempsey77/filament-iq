# Sync Color on Bind

## Problem
When loading non-RFID filament into the AMS, Bambu forces the user to pick from ~20-30 preset colors — they can't enter an exact hex. So the AMS tray reports e.g. `161616` but the Spoolman filament record has the product listing color `000000`. This causes:
- `lot_sig` mismatches (the sig includes color, so the enrolled sig won't match next time)
- 3MF matching failures (slicer profile color differs from Spoolman record)

## Solution
On manual bind (`FILAMENT_IQ_SLOT_ASSIGNED` event), automatically PATCH the Spoolman **filament** `color_hex` to match the AMS tray-reported color, then re-enroll `lot_nr` with the corrected signature.

## Event Contract

The `ams_slot_assign_and_update` script fires `FILAMENT_IQ_SLOT_ASSIGNED` with:

| Field | Type | Description |
|-------|------|-------------|
| `slot` | int | Slot number (1-6) |
| `spool_id` | int | Spoolman spool ID |
| `sync_color_hex` | string | `"auto"` = sync tray color, 6-char hex = explicit color, `""` = no sync |

Default is `"auto"` — sync is on by default for all manual assigns.

## Data Flow
```
Dashboard assign → scripts.yaml (ams_slot_assign_and_update)
  → fires FILAMENT_IQ_SLOT_ASSIGNED with sync_color_hex="auto"
  → AppDaemon _on_slot_assigned()
    → _read_tray_color_hex(slot) reads AMS tray color attribute
    → _sync_filament_color_on_bind() PATCHes Spoolman filament color_hex if different
    → _build_lot_sig() builds new sig with corrected color
    → _enroll_lot_nr(force=True) overwrites old sig with corrected one
```

## Key Methods

### `_read_tray_color_hex(slot)`
- Reads `state_attr(tray_entity, 'color')` (format: `#161616FF`)
- Strips `#`, strips alpha channel (last 2 chars if 8-char), uppercases
- Returns 6-char uppercase hex or `None`

### `_sync_filament_color_on_bind(slot, spool_id, sync_mode)`
- Resolves target color from tray (auto) or explicit hex
- GETs spool from Spoolman to find `filament.id` and `filament.color_hex`
- Compares colors; skips if already matching
- PATCHes `/api/v1/filament/{filament_id}` with `{"color_hex": target}`
- Returns `True` if PATCHed, `False` if skipped

### `_enroll_lot_nr(force=True)`
- `force=False` (default): existing behavior — refuses overwrite of different lot_nr
- `force=True`: allows overwrite, logs `LOT_NR_FORCE_OVERWRITE` with reason `color_sync_re_enrollment`

## Guards

### RFID Guard
Spools with RFID lot_nr (32-char hex UUID) skip color sync entirely. RFID spools have authoritative color from Bambu's cloud — no need to sync from tray preset.

### Same Color Guard
If Spoolman filament color already matches tray color, skip the PATCH and don't force re-enroll.

## Risks and Edge Cases
- **Shared filament records**: Updating filament color affects ALL spools of that filament type in Spoolman, not just the assigned spool. This is intentional — the filament's "true" color should match what's physically loaded.
- **Same filament, different presets**: If a user picks different Bambu preset colors for the same filament in different slots, the last bind wins. This is acceptable — the user should pick the closest preset consistently.
- **Backwards compatible**: Empty/missing `sync_color_hex` = no sync = existing behavior unchanged.

## Testing Checklist
- [ ] Assign non-RFID spool where Spoolman color differs from tray → filament PATCHed, lot_nr re-enrolled
- [ ] Assign non-RFID spool where colors already match → no PATCH, no force re-enroll
- [ ] Assign RFID spool → color sync skipped entirely
- [ ] Assign with `sync_color_hex=""` → no sync (backwards compatible)
- [ ] Verify 3MF matching works after color sync (slicer color now matches Spoolman)
