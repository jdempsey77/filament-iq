# RFID System
## Detection
From AMS sensor attributes:
- `tray_uuid` — factory serial number (SN). Stable, orientation-independent. **Primary identity.**
- `tag_uid` — hardware chip UID. Orientation-dependent. **Retired as match key.**

Non-RFID indicator: both fields are all-zero
- `tag_uid == "0000000000000000"`
- `tray_uuid == "00000000000000000000000000000000"`

---
## Identity Storage (v4)
`tray_uuid` is stored in Spoolman `lot_nr` (plain string, no encoding).
`tag_uid` is no longer stored or matched.
`extra.rfid_tag_uid` is retired — read-only during migration fallback window, never written.

**UUID-format `lot_nr`:** When `lot_nr` is a 32-char hex string, the spool is treated as RFID-enrolled. Such spools are **excluded from all non-RFID candidate pools** (they must not match non-RFID trays).

---
## Matching Order
1. Match `tray_uuid` against `spool.lot_nr` — primary path
2. **Unenrolled spool fallback:** on first insert, if `lot_nr` is empty, sig-based search (material + color) can match unenrolled spools; on match, write `tray_uuid` to `lot_nr`, bind
3. If no match at any tier: NEEDS_ACTION, notify user

Migration fallback (canonicalizer / `extra.rfid_tag_uid` → `lot_nr`) is **retired**. No canonicalizer tier.

Matching occurs before non-RFID logic. RFID path takes priority when `tray_uuid` is non-zero.

---
## Enrollment
On first bind of an RFID spool: write `tray_uuid` to `lot_nr` via plain PATCH.
No canonicalization required. No extra field writes.

---
## Pending Window
Prevents premature demotion. RFID sticky mapping: if `tray_uuid` matches stored
`tray_signature` helper AND `spool_id > 0`, re-bind immediately without waterfall.
