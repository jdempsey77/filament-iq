# Slot State Machine

Possible States:

EMPTY
PENDING_RFID_READ
RFID_REGISTERED
NON_RFID_REGISTERED
STABILIZED
ERROR
EXCLUDED_NEW

**Additional status / unbound codes (reconciler):**
- **OK_NON_RFID_REGISTERED** — non-RFID slot bound and stable
- **NEEDS_MANUAL_BIND** — no auto-match or ambiguous; user must assign spool (e.g. via dashboard)
- **WAITING_FOR_CONFIRMATION** — temporary hold (e.g. low-confidence non-RFID)
- **UNBOUND_TRAY_EMPTY** — tray is empty; no spool to bind; safe-list for “no notification”

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

## Example Flow (Non-RFID)

EMPTY (or tray with all-zero tag/tray_uuid)
  ->
Computed tray sig vs Spoolman (lot_nr or material+color)
  ->
Single match → bind, write sig to lot_nr → **OK_NON_RFID_REGISTERED**
  ->
Multiple match → tiebreak (location, lowest remaining) → bind → OK_NON_RFID_REGISTERED
  ->
Zero match / generic sentinel → **NEEDS_MANUAL_BIND**
  ->
(Optional) WAITING_FOR_CONFIRMATION if low confidence before final NEEDS_MANUAL_BIND

Empty tray → **UNBOUND_TRAY_EMPTY**, spool_id cleared.

---

## Guarantees

- spool_id does not change during internal churn
- Swap alerts only trigger on true tray identity change
- Pending window expires deterministically
