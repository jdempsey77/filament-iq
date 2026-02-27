# Data Model

## Slot

A logical slot maps to:

- AMS tray
- Spool identity
- Tray signature
- State machine state

---

## Tray Identity

Primary:
- tray_uuid

Fallback:
- tag_uid (RFID)

Stored in:
input_text.ams_slot_{slot}_tray_signature

Rules:
- Sticky
- Only updated on confirmed physical change
- Cleared when spool_id becomes 0

---

## Spool Identity

- HA helper stores spool_id
- Spoolman is source of truth
- spool_id mutates only when tray identity changes

---

## Spoolman extra fields

IMPORTANT:

Spoolman extra values must be JSON-encoded literals.

Example:
"rfid_tag_uid": "\"A71B987C00000100\""

PATCH replaces entire extra block.
Malformed JSON will be rejected.
