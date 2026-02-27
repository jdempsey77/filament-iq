# RFID System

## Detection

From AMS sensor:
- tag_uid
- tray_uuid

Normalized:
- uppercase
- quotes stripped
- deterministic compare

---

## Matching Order

1. Exact UID match in Spoolman
2. Exclude location "New"
3. Deterministic selection
4. If none:
   - Notify
   - Allow user action

---

## Pending Window

Prevents premature demotion.

RFID matching occurs before non-RFID logic.
