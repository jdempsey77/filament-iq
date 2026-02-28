# Non-RFID System
Feature flag controlled:
`input_boolean.p1s_nonrfid_enabled`

Slots with:
- `tag_uid == "0000000000000000"` AND
- `tray_uuid == "00000000000000000000000000000000"`

Auto-seed `expected_spool_id` on first bind.
Transitions to `NON_RFID_REGISTERED`.
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
1. Match computed sig against `spool.lot_nr` — primary path
2. Fallback (migration only): match legacy HA_SIG against `spool.comment`
   - On match: write sig to `lot_nr`, bind
3. filament_id exact match against `filament.external_id` (Shelf only)
4. Vendor + material match (Shelf only)
5. Color fuzzy tiebreaker (only when multiple candidates remain)
6. If no match at any tier: NEEDS_ACTION, notify user

---
## Generic Sentinel Short-Circuit
Any `filament_id` ending in `99` (e.g. `GFL99`, `GFG99`) → immediate NEEDS_ACTION.
Reason: `GENERIC_FILAMENT_NO_AUTO_MATCH`. No waterfall attempted.

---
## Confidence Gating
Reject and emit `LOW_CONFIDENCE_NO_AUTO_MATCH` when:
- `type` is missing or empty, OR
- `color` is missing or empty, OR
- tray state starts with "GENERIC" AND `filament_id` is a generic sentinel
