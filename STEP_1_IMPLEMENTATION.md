# Step 1 Implementation: Correctness + Safety Fixes

## Changes Summary

### ✅ **CRITICAL BUG FIXES**

1. **Negative End Value Clamp (Lines 563, 576, 666, 755)**
   - **Bug:** Negative end values from unavailable sensors would inflate `used_g` calculation
   - **Fix:** All `grams` calculations now use `[0, effective] | max` to clamp to >= 0
   - **Impact:** Prevents impossible scenarios like "used 851g from 850g start"

2. **Failed Print Policy (Line 726, 795-802)**
   - **Bug:** Failed prints always decremented filament (inaccurate)
   - **Fix:** Default NO decrement on `failed`/`error`/`canceled` states
   - **Toggle:** `input_boolean.p1s_decrement_on_failed` (default OFF) for future flexibility
   - **Notification:** User informed when decrement skipped due to failure

3. **Print Mutex/Lock (Lines 508-513, 719)**
   - **Bug:** Finish automation could run twice for same print
   - **Fix:** `input_boolean.p1s_print_active` + `input_text.p1s_print_job_key`
   - **Behavior:** Set ON at print start, OFF at finish, condition checks prevent duplicate runs

### ✅ **SAFETY FEATURES ADDED**

4. **Reconcile Flag + Notifications (Lines 95-104 config, 736-745, 783-792 automations)**
   - **New:** `input_boolean.p1s_needs_reconcile` (print-level flag)
   - **Triggers:**
     - Start snapshot empty (line 736)
     - Any slot has unknown end reading (line 783)
     - Spool mapping changes during print (line 854)
   - **Notifications:** Persistent notification with reason + affected slots

5. **Spool Swap Detection (Lines 839-862 automations)**
   - **New automation:** `p1s_detect_spool_swap_during_print`
   - **Monitors:** All 6 `input_text.ams_slot_N_spool_id` entities
   - **Action:** Sets reconcile flag + notifies with old/new spool IDs
   - **Policy:** Freeze mapping at print start, any change → manual reconcile required

6. **Manual Update Blocking (Lines 283, 329, 374, 419, 464, 509 scripts)**
   - **New condition:** All 6 `ams_slot_N_assign_and_update` scripts
   - **Check:** `input_boolean.p1s_print_active == 'off'`
   - **Behavior:** Script fails silently if print active (prevents delta corruption)
   - **Future:** Can add explicit notification or override helper

---

## Files Modified

### `configuration.yaml`
- **Lines 87-89:** Added `input_text.p1s_print_job_key` (mutex tracking)
- **Lines 93-104:** Added 3 `input_boolean` helpers:
  - `p1s_print_active` (mutex lock)
  - `p1s_needs_reconcile` (safety flag)
  - `p1s_decrement_on_failed` (policy toggle, default OFF)

### `automations.yaml`
- **Lines 508-513:** `p1s_remaining_snapshot_init` - Set mutex + clear reconcile flag
- **Lines 563, 576:** `p1s_remaining_snapshot_init` - Clamp grams to >= 0
- **Line 666:** `p1s_remaining_snapshot_on_tray_first_active` - Clamp grams to >= 0
- **Lines 719-727:** `p1s_remaining_snapshot_on_finish` - Add mutex condition + variables
- **Lines 736-745:** `p1s_remaining_snapshot_on_finish` - Reconcile check for empty start
- **Lines 755, 762-776:** `p1s_remaining_snapshot_on_finish` - Clamp end values + unknown detection
- **Lines 783-802:** `p1s_remaining_snapshot_on_finish` - Reconcile + failed print policy
- **Lines 810-821:** `p1s_remaining_snapshot_on_finish` - Simplified notification (removed tray_entities)
- **Lines 839-862:** New automation `p1s_detect_spool_swap_during_print`

### `scripts.yaml`
- **Lines 283, 329, 374, 419, 464, 509:** Block all 6 `ams_slot_N_assign_and_update` scripts when print active

---

## Testing Instructions

### Pre-Deployment

1. **Backup current config:**
   ```bash
   cp automations.yaml automations.yaml.backup
   cp configuration.yaml configuration.yaml.backup
   cp scripts.yaml scripts.yaml.backup
   ```

2. **Deploy changes:**
   ```bash
   ./scripts/manage_ha.sh --automations
   ./scripts/manage_ha.sh --scripts
   ./scripts/manage_ha.sh --config
   ```

3. **Restart Home Assistant** (required for new helpers)

### Validation Tests

#### Test 1: Negative End Value Protection
**Setup:**
1. Manually set `sensor.p1s_tray_1_fuel_gauge_remaining` to unavailable (or disconnect printer temporarily)
2. Start a print, wait for snapshot
3. Finish print

**Expected:**
- End snapshot stores `0` (not negative)
- `used_g` calculation: `max(0, start - 0)` (reasonable)
- No notification about impossible usage

#### Test 2: Failed Print Policy
**Setup:**
1. Start a print
2. Cancel/fail it (via Bambu Studio or printer)

**Expected:**
- Notification: "P1S Print Failed - No Decrement"
- No `spoolman.use_spool_filament` calls
- Spoolman weights unchanged
- `input_boolean.p1s_print_active` turned OFF

#### Test 3: Print Mutex
**Setup:**
1. Start a print
2. Quickly restart HA or trigger finish automation manually

**Expected:**
- Only ONE decrement per print
- `input_boolean.p1s_print_active` prevents duplicate runs

#### Test 4: Reconcile Flag (Empty Start)
**Setup:**
1. Manually clear `input_text.p1s_tray_remaining_start_json` to `{}`
2. Trigger print finish

**Expected:**
- Notification: "P1S Filament Tracking: Reconcile Needed" (start snapshot empty)
- `input_boolean.p1s_needs_reconcile` turns ON
- No decrement attempted

#### Test 5: Spool Swap Detection
**Setup:**
1. Start a print (wait for `input_boolean.p1s_print_active` ON)
2. Change `input_text.ams_slot_1_spool_id` value (via UI or Developer Tools)

**Expected:**
- Notification: "P1S Spool Swap During Print Detected" (shows old→new IDs)
- `input_boolean.p1s_needs_reconcile` turns ON
- Print can continue, but decrement will be skipped at finish

#### Test 6: Manual Update Blocking
**Setup:**
1. Start a print
2. Try to run `script.ams_slot_1_assign_and_update` via UI

**Expected:**
- Script fails (condition not met)
- No Spoolman update
- No notification (silent fail per HA script behavior)

---

## Rollback Instructions

If any issues occur:

```bash
# Restore backups
cp automations.yaml.backup automations.yaml
cp configuration.yaml.backup configuration.yaml
cp scripts.yaml.backup scripts.yaml

# Redeploy
./scripts/manage_ha.sh --automations
./scripts/manage_ha.sh --scripts
./scripts/manage_ha.sh --config

# Restart HA
```

---

## Known Limitations (To Address in Next Steps)

1. **Manual Update Blocking:** No user notification when blocked (silent fail)
   - **Future:** Add persistent_notification in scripts when condition fails
   - **Future:** Add `input_boolean.p1s_override_print_lock` for emergency updates

2. **Reconcile Flag UX:** No per-slot status badges yet
   - **Future:** Template sensors `sensor.ams_slot_N_status` (OK/Reconcile/Low/Critical)
   - **Future:** Dashboard cards showing reconcile state per slot

3. **Snapshot Storage:** Still using 255-char `input_text` JSON
   - **Future:** Replace with `input_number.p1s_slot_N_start_g` / `_end_g` (robust)
   - **Current:** Safe for up to 6 slots at 4-digit weights (~60 chars)

4. **Test Mode:** Not yet implemented
   - **Next:** `input_boolean.filament_test_mode` + mock entities + simulation scripts

---

## Policy Summary

| Scenario | Behavior | Rationale |
|----------|----------|-----------|
| **Successful print** | Decrement per-tray deltas | Accuracy: best case |
| **Failed print** | NO decrement (default) | Safety: preserve accuracy |
| **Unknown end values** | Store as 0, set reconcile flag | Safety: never inflate usage |
| **Empty start snapshot** | Set reconcile flag, skip decrement | Safety: missing data |
| **Spool swap during print** | Set reconcile flag, skip decrement | Safety: mapping changed mid-print |
| **Manual update during print** | BLOCKED | Safety: prevent delta corruption |
| **Duplicate finish trigger** | Prevented by mutex | Correctness: one decrement per print |

---

## Next Steps (Step 2-5)

**Step 2:** Reconcile UX enhancements
- Per-slot status sensors
- Dashboard badges
- Manual reconcile script

**Step 3:** Already complete (mutex added in Step 1)

**Step 4:** Robust snapshot storage
- Replace JSON with per-slot `input_number` helpers
- Increase reliability for 6-slot multi-color prints

**Step 5:** Test Mode + Simulation
- Mock entities
- Simulation scripts
- 6 regression test scenarios

---

*Implementation completed: 2025-02-13*
*Safety-first approach: Never silently produce bad data*
