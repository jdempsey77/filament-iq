# Non-RFID System

Feature flag controlled:
input_boolean.p1s_nonrfid_enabled

Slots with:
- spool_id > 0
- expected_spool_id == 0

Auto-seed expected_spool_id.

Transitions to NON_RFID_REGISTERED.

Does not depend on rfid_pending_until.
