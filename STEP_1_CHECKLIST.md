# Step 1 Quick Validation Checklist

## Pre-Deployment Checks

- [ ] Backed up `automations.yaml`, `configuration.yaml`, `scripts.yaml`
- [ ] Reviewed diffs in `STEP_1_IMPLEMENTATION.md`
- [ ] Confirmed understanding of new policy (failed prints = no decrement by default)

## Deployment

```bash
cd /Users/jdempsey/code/home_assistant

# Deploy changes
./scripts/manage_ha.sh --automations
./scripts/manage_ha.sh --scripts  
./scripts/manage_ha.sh --config

# Restart HA (required for new helpers)
# Settings → System → Restart
```

## Post-Deployment Verification

### New Helpers Exist
- [ ] `input_boolean.p1s_print_active` exists (Developer Tools → States)
- [ ] `input_boolean.p1s_needs_reconcile` exists
- [ ] `input_boolean.p1s_decrement_on_failed` exists (should be OFF)
- [ ] `input_text.p1s_print_job_key` exists

### Automations Updated
- [ ] `p1s_remaining_snapshot_init` has 3 new actions at start (mutex + reconcile clear)
- [ ] `p1s_remaining_snapshot_on_finish` has new condition (print_active = on)
- [ ] `p1s_detect_spool_swap_during_print` automation exists (check Automations page)

### Scripts Protected
- [ ] Try running `script.ams_slot_1_assign_and_update` → should show "Conditions not met" if print active

## Quick Smoke Tests

### Test 1: Start/Finish Cycle
1. Start any print
2. Check: `input_boolean.p1s_print_active` = ON
3. Check: `input_boolean.p1s_needs_reconcile` = OFF
4. Wait for print to finish successfully
5. Check: Notification appears with usage summary
6. Check: `input_boolean.p1s_print_active` = OFF
7. Check: Spoolman weight decreased

**PASS:** ✅ / **FAIL:** ❌

### Test 2: Failed Print (Critical)
1. Start a print
2. Cancel it via Bambu Studio
3. Check: Notification "P1S Print Failed - No Decrement"
4. Check: Spoolman weight UNCHANGED
5. Check: `input_boolean.p1s_print_active` = OFF

**PASS:** ✅ / **FAIL:** ❌

### Test 3: Manual Update Blocking
1. Start a print
2. Try to run "Assign & Update" on any slot via dashboard
3. Check: Script fails silently (no Spoolman update)
4. Finish or cancel print
5. Try "Assign & Update" again
6. Check: Script succeeds now

**PASS:** ✅ / **FAIL:** ❌

## Rollback (If Needed)

```bash
cd /Users/jdempsey/code/home_assistant

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

## Next Actions

Once Step 1 is validated:
- [ ] Document any issues found
- [ ] Run full 6-test suite from `STEP_1_IMPLEMENTATION.md`
- [ ] Request Step 2 implementation (Reconcile UX enhancements)
- [ ] Request Step 4 implementation (Replace JSON storage with input_numbers)
- [ ] Request Step 5 implementation (Test Mode + Simulation)

---

**Status:** 🟡 Awaiting Deployment & Testing
**Deployed:** _____ (date/time)
**Tested:** _____ (date/time)
**Issues:** _____
