# Data Model
## Slot
A logical slot maps to:
- AMS tray sensor
- Spool identity
- Tray signature (HA helper)
- State machine state

---
## Tray Identity
Primary:
- `tray_uuid` (RFID spools) — factory serial, orientation-independent
- `type|filament_id|color_hex` sig (non-RFID spools) — filament-property-derived

Stored in:
`input_text.ams_slot_{slot}_tray_signature`

Rules:
- Sticky — only updated on confirmed physical change
- Cleared when `spool_id` becomes 0
- `tag_uid` is no longer used as an identity field

---
## Spool Identity
- HA helper stores `spool_id`
- Spoolman is source of truth
- `spool_id` mutates only when tray identity changes

---
## Spoolman Identity Field (v4)
**`lot_nr`** is the single identity storage field for all spool types.

| Spool type | `lot_nr` value |
|---|---|
| Bambu RFID | `tray_uuid` e.g. `38D1181E8F024FDA9D040D3BE3A20312` |
| Non-RFID | sig e.g. `pla\|gfl05\|898989` |

- Plain string — no encoding required
- Direct top-level PATCH — no GET-merge-PATCH needed
- `comment` field is free for human use

---
## Retired Fields (v4)
| Field | Status | Notes |
|---|---|---|
| `extra.rfid_tag_uid` | Retired | Read-only during migration fallback. Never written. |
| `extra.ha_spool_uuid` | Retired | No longer generated or written. |
| Spoolman `comment` | Freed | Was HA_SIG storage. Now human-only. |

**Spoolman extra field encoding rules no longer apply.** The canonicalizer is **retired** (moved to _retired/). Identity is stored only in `lot_nr`; no migration fallback tier.
