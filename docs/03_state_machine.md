# Slot State Machine

Possible States:

EMPTY
PENDING_RFID_READ
RFID_REGISTERED
NON_RFID_REGISTERED
STABILIZED
ERROR
EXCLUDED_NEW

---

## Example Flow (RFID)

EMPTY
  ->
PENDING_RFID_READ
  ->
RFID_REGISTERED
  ->
STABILIZED

---

## Guarantees

- spool_id does not change during internal churn
- Swap alerts only trigger on true tray identity change
- Pending window expires deterministically
