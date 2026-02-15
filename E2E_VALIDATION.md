# P1S Filament Tracking System: End-to-End Validation
# Minimal V1 Opinionated Mode
# Date: 2026-02-13

## Test Configuration
- Mode: Deterministic behavioral validation
- Branch: bugfix/start-snapshot-empty-and-ams-spam
- Auto Mode: p1s_auto_mode_opinionated = ON
- Test Mode: filament_test_mode = OFF (for real service calls)

---

## SCENARIO 1: Single Slot Print (35g usage)

**Setup:**
- Clear all start/end helpers
- Clear binding JSON
- Slot 1: start=350g, end=315g
- Expected usage: 35g

**Test Steps:**
1. Set input_number.p1s_start_slot_1_g = 350
2. Set input_number.p1s_end_slot_1_g = 315
3. Trigger finish automation
4. Verify spoolman.use_spool_filament called with 35g
5. Verify binding JSON populated
6. Verify notification shows confidence + rule

**Expected Results:**
- ✓ p1s_start_slot_1_g = 350
- ✓ Finish processes slot 1
- ✓ Decrement call: 35g
- ✓ p1s_last_mapping_json populated
- ✓ binding_json: {"1": <spool_id>}
- ✓ Notification: "Slot 1: 350g → 315g (used ~35g, spool X)"
- ✓ Confidence logged

**FAIL Conditions:**
- No decrement call
- Multiple decrements for same slot
- No mapping persisted
- Mapping not deterministic

---

## SCENARIO 2: Duplicate Material Spools (lightest wins)

**Setup:**
- Two PLA spools: A (420g), B (180g)
- Slot 2: 200g start, 160g end (40g used)

**Expected:**
- Chooses spool B (180g - lowest remaining)
- After decrement: B has 140g
- Binding: {"1": X, "2": B_id}
- Confidence: medium (material match)
- Rule: material_lightest

**FAIL Conditions:**
- Chooses 420g spool instead
- Mapping unstable on re-run
- Wrong confidence level

---

## SCENARIO 3: Restart Behavior

**Setup:**
- Simulate HA restart (no actual restart, check state)
- Verify no automation triggers
- Verify binding unchanged

**Expected:**
- No AMS remap spam
- No finish automation triggered
- binding_json unchanged
- last_mapping_json unchanged

**FAIL Conditions:**
- Slot rebinds without print
- Mapping JSON mutated
- Automation spuriously fires

---

## SCENARIO 4: Multi-Slot Print

**Setup:**
- Slot 1: 300g → 270g (30g used)
- Slot 2: 200g → 165g (35g used)
- Trigger finish

**Expected:**
- Both slots processed
- Two decrement calls
- Checkpoint: "processing_slots | 1:300, 2:200"
- Both mappings persisted
- No "Start Snapshot Empty" error

**FAIL Conditions:**
- Only one slot decremented
- Snapshot empty error
- Checkpoint doesn't show both slots

---

## SCENARIO 5: No Binding Yet (deterministic creation)

**Setup:**
- Clear binding JSON: {}
- Run single slot test again
- Re-run same test

**Expected:**
- First run: creates binding
- Second run: reuses same spool_id (confidence=high, rule=binding_reused)
- Deterministic: same slot always maps to same spool

**FAIL Conditions:**
- Mapping differs between runs
- Binding not created on first run
- Binding not reused on second run

---

## Test Execution Log

[To be filled during execution]

---

## Final Verdict

[GO / NO-GO + reason]

---

## Race Conditions Observed

[To be documented if any]
