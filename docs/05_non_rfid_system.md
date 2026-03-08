# Non-RFID System
Feature flag controlled:
`input_boolean.filament_iq_nonrfid_enabled`

Slots with:
- `tag_uid == "0000000000000000"` AND
- `tray_uuid == "00000000000000000000000000000000"`

Auto-seed `expected_spool_id` on first bind.
Transitions to `NON_RFID_REGISTERED` (status `OK_NON_RFID_REGISTERED`).
Does not depend on `rfid_pending_until`.

---
## Identity Storage (v4)
Non-RFID spools are identified by a sig stored in Spoolman `lot_nr`:

Format: `type|filament_id|color_hex`
Example: `pla|gfl05|898989`

- `name` and `tag_uid` are NOT included in the sig
- Sig is purely filament-property-derived — stable across renames
- Plain string, no encoding, direct PATCH to `lot_nr`

`comment` field is free for human use. Reconciler no longer writes HA_SIG to comment.
`extra.ha_spool_uuid` is retired — no longer generated or written.

---
## Matching Order
1. **Enrolled candidates** — match computed tray sig against `spool.lot_nr` (primary path)
2. **Unenrolled candidates** — material + color search (Shelf/New); excludes spools whose `lot_nr` is UUID-format (RFID-enrolled)
3. **Exclusions:** RFID-enrolled spools (UUID `lot_nr`) are excluded from all non-RFID pools; spools active in another slot are excluded from tiebreak
4. **Single candidate** → resolve and enroll (write sig to `lot_nr`)
5. **Multiple candidates** → tiebreak by location (slot/Shelf) then lowest remaining weight
6. **Zero candidates** → generic sentinel skip (filament_id ending in 99) → NEEDS_MANUAL_BIND, or NEEDS_MANUAL_BIND with reason

Generic sentinel short-circuit is **last resort**: only when zero lot_nr matches and zero unenrolled candidates. Any `filament_id` ending in `99` (e.g. `GFL99`) then gets NEEDS_MANUAL_BIND with reason `GENERIC_FILAMENT_NO_AUTO_MATCH`. No earlier waterfall.

---
## Swap detection
Spool swap is **auto-detected** on tray signature mismatch: reconciler compares current tray sig to the bound spool’s `lot_nr` (or material+color if no lot_nr). No manual clear needed; helper is updated on match or set to unbound on mismatch.

---
## Empty tray clear
When tray state is empty (or tray_state_str indicates empty), reconciler sets `unbound_reason` to `UNBOUND_TRAY_EMPTY`, clears `spool_id` for that slot, and clears sticky expected state. Safe-list for “no bind needed” includes `UNBOUND_TRAY_EMPTY` (e.g. bind reminder automation does not fire for empty trays).

---
## Material normalization
For matching and truth guard, material is normalized before comparison:
- PLA+, PLA-CF → PLA
- PETG-CF → PETG
- ABS* (any variant) → ABS

---
## Confidence Gating
Reject and emit `LOW_CONFIDENCE_NO_AUTO_MATCH` when:
- `type` is missing or empty, OR
- `color` is missing or empty, OR
- tray state starts with "GENERIC" AND `filament_id` is a generic sentinel
