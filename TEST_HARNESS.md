# Test Harness: Filament Tracking (Print-Free)

## Overview
This harness validates all P1S filament tracking paths without requiring real prints.
All tests use `input_boolean.filament_test_mode` to disable real Spoolman calls.

## Test Mode Flag
- `input_boolean.filament_test_mode = ON`: Test mode (dry-run, no real Spoolman updates)
- `input_boolean.filament_test_mode = OFF`: Production mode (real Spoolman calls)

## Test Scripts (scripts.yaml)

### Test 1: Single-Color Success Path
**Script:** `test_filament_single_color_success`
**Setup:**
- Clear all helpers
- Set `start_slot_1_g = 500`
- Set `ams_slot_1_spool_id = 1`
- Set `filament_test_mode = ON`

**Execute:**
- Set `end_slot_1_g = 450`
- Trigger finish automation

**Expected:**
- Checkpoint: `processing_slots | 1:500`
- Checkpoint: `slot1_decrement_50g`
- Checkpoint: `complete`
- Test result: `PASS | single-color | used=50g`

**Failure Modes:**
- `no_start_data`: Init didn't seed start helpers
- Checkpoint stuck at `processing_slots`: No decrement called
- Test result: `FAIL | reason`

---

### Test 2: Multi-Color Success Path
**Script:** `test_filament_multi_color_success`
**Setup:**
- `start_slot_1_g = 300`, `start_slot_2_g = 200`
- `ams_slot_1_spool_id = 1`, `ams_slot_2_spool_id = 2`
- `filament_test_mode = ON`

**Execute:**
- `end_slot_1_g = 280`, `end_slot_2_g = 150`
- Trigger finish

**Expected:**
- Checkpoint: `processing_slots | 1:300, 2:200`
- Checkpoint: `slot1_decrement_20g`, `slot2_decrement_50g`
- Checkpoint: `complete`
- Test result: `PASS | multi-color | slot1=20g, slot2=50g`

---

### Test 3: HA Restart Mid-Print (Persisted Start Values)
**Script:** `test_filament_ha_restart_persistence`
**Setup:**
- Seed `start_slot_1_g = 600`
- Simulate HA restart (helpers persist, mutex resets)
- Verify finish still works

**Execute:**
- Trigger finish with `end_slot_1_g = 550`

**Expected:**
- Checkpoint shows `1:600` (start persisted)
- Decrement called with `used_g = 50`
- Test result: `PASS | restart_persistence`

---

### Test 4: Failed Print Policy
**Script:** `test_filament_failed_print_no_decrement`
**Setup:**
- `start_slot_1_g = 400`
- `p1s_decrement_on_failed = OFF`
- Trigger finish with `trigger_state = 'failed'`

**Expected:**
- Notification: "P1S Print Failed - No Decrement"
- No `slotN_decrement` checkpoint
- Test result: `PASS | failed_no_decrement`

---

### Test 5: Unknown End Reading → Reconcile
**Script:** `test_filament_unknown_end_reconcile`
**Setup:**
- `start_slot_1_g = 500`
- Mock sensor to return `-1` (unknown)

**Execute:**
- Trigger finish

**Expected:**
- `p1s_needs_reconcile = ON`
- Notification: "Reconcile Needed"
- Test result: `PASS | reconcile_triggered`

---

### Test 6: Start Snapshot Empty (Init Failed)
**Script:** `test_filament_start_snapshot_empty`
**Setup:**
- Clear ALL `start_slot_N_g` to 0
- Trigger finish

**Expected:**
- Notification: "Start Snapshot Empty"
- Checkpoint: `no_start_data | slots=none`
- No decrement
- Test result: `PASS | start_empty_handled`

---

## Test Matrix

| Test # | Scenario | Expected Checkpoint Sequence | Pass Criteria |
|--------|----------|------------------------------|---------------|
| 1 | Single-color success | `ENTERED → processing_slots \\| 1:500 → slot1_decrement_50g → complete` | Decrement called, used_g correct |
| 2 | Multi-color success | `ENTERED → processing_slots \\| 1:300, 2:200 → slot1_decrement_20g → slot2_decrement_50g → complete` | Both slots decremented |
| 3 | HA restart mid-print | `ENTERED → processing_slots \\| 1:600 → slot1_decrement_50g → complete` | Start values persisted |
| 4 | Failed print (no decrement) | `ENTERED → processing_slots \\| 1:400 → complete` | No `slotN_decrement`, notification sent |
| 5 | Unknown end → reconcile | `ENTERED → processing_slots \\| 1:500 → complete` | `p1s_needs_reconcile = ON`, notification |
| 6 | Start snapshot empty | `ENTERED → no_start_data \\| slots=none` | Stop early, no decrement, notification |

---

## How to Run Tests

1. **Enable test mode:**
   ```
   input_boolean.filament_test_mode = ON
   input_boolean.filament_debug_mode = ON
   ```

2. **Run a test script:**
   Developer Tools → Services → `script.test_filament_single_color_success`

3. **Check results:**
   - `input_text.p1s_finish_automation_checkpoint` (sequence)
   - `input_text.filament_test_last_result` (PASS/FAIL)
   - Persistent notifications (for policy/reconcile tests)

4. **Reset between tests:**
   Run `script.test_filament_reset_all_helpers`

---

## Test Mode Implementation Notes

**In test mode (`filament_test_mode = ON`):**
- `spoolman.use_spool_filament` → dry-run (log to checkpoint, don't call)
- `spoolman.patch_spool` → dry-run
- All other logic runs normally (start seeding, end computation, math)

**Finish automation changes (minimal):**
```yaml
- if:
    - condition: template
      value_template: "{{ should_decrement and spool_id > 0 and used_g > 0 }}"
  then:
    - service: input_text.set_value
      target:
        entity_id: input_text.p1s_finish_automation_checkpoint
      data:
        value: "slot{{ repeat.index }}_decrement_{{ used_g }}g"
    - choose:
        - conditions:
            - condition: state
              entity_id: input_boolean.filament_test_mode
              state: "on"
          sequence:
            - service: input_text.set_value
              target:
                entity_id: input_text.filament_test_last_result
              data:
                value: "DRY-RUN | slot{{ repeat.index }} | spool_id={{ spool_id }} | used_g={{ used_g }}"
      default:
        - service: spoolman.use_spool_filament
          data:
            id: "{{ spool_id }}"
            use_weight: "{{ used_g }}"
```

---

## Rollback

```bash
# To previous known-good on fix/eliminate-json-parsing
git checkout fix/eliminate-json-parsing
./scripts/manage_ha.sh --config --restart
./scripts/manage_ha.sh --automations

# To before test harness changes
git revert HEAD~N  # N = number of test harness commits
```

---

## GO/NO-GO Criteria

**GO if all tests PASS:**
1. Single-color: Checkpoint reaches complete, used_g correct
2. Multi-color: Both slots decremented correctly
3. HA restart: Start values persist, finish works
4. Failed policy: No decrement, notification sent
5. Reconcile: Flag set, notification sent
6. Start empty: Early exit, notification sent

**NO-GO if:**
- Any test shows incorrect used_g math
- Checkpoint stuck before complete (automation crashed)
- Test mode calls real Spoolman (dry-run not working)
- Start values don't persist across simulated restarts
