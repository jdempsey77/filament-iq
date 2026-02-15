# Input Text Persistence Stability - Operator Checklist

## Status: Ready for Verification

### Prerequisites Confirmed ✓
- ✓ No YAML definitions for persistence helpers in `configuration.yaml`
- ✓ All reads/writes to persistence helpers are safe (existence checks, string conversion)
- ✓ Validation script exists: `script.p1s_validate_persistence_infrastructure`
- ✓ Validation script safely restores state (uses '{}' if original unavailable)

---

## Operator Task: Create UI Helpers

### Step 1: Create Helper 1 - Binding JSON

1. Navigate to: **Settings → Devices & Services → Helpers**
2. Click: **+ Create Helper**
3. Select: **Text**
4. Configure:
   - **Name**: `P1S Slot to Spool Binding (JSON)`
   - **Entity ID**: Verify it shows `input_text.p1s_slot_to_spool_binding_json`
   - **Max length**: `1024`
   - **Initial value**: `{}`
5. Click: **Create**

### Step 2: Create Helper 2 - Mapping Log

1. Click: **+ Create Helper** (again)
2. Select: **Text**
3. Configure:
   - **Name**: `P1S Last Mapping Result`
   - **Entity ID**: Verify it shows `input_text.p1s_last_mapping_json`
   - **Max length**: `2048`
   - **Initial value**: (leave empty)
4. Click: **Create**

---

## Verification Protocol

### Test 1: Before Restart

**Run validation:**
```yaml
service: script.p1s_validate_persistence_infrastructure
```

**Expected Result:** 
- Persistent notification titled: **"P1S Infrastructure Validation: PASS"**
- Message shows: ✓ Binding helper exists and is writable
- Message shows: ✓ Mapping helper exists and is writable

**If FAIL:**
- Check notification for which helper is missing
- Verify entity IDs match exactly (no typos)
- Recreate missing helper(s)
- Run validation again

---

### Test 2: After Restart

**Restart Home Assistant:**
```yaml
service: homeassistant.restart
```

**Wait ~60 seconds for HA to fully start**

**Run validation again:**
```yaml
service: script.p1s_validate_persistence_infrastructure
```

**Expected Result:** 
- Same PASS notification as Test 1

**Confirm persistence:**
```yaml
# Check binding helper state
Developer Tools → States → input_text.p1s_slot_to_spool_binding_json
# Should show: {}

# Check mapping helper state  
Developer Tools → States → input_text.p1s_last_mapping_json
# Should show: validation=PASS | binding_writable=true | ...
```

---

## Exit Criteria

✅ **PASS** on both tests (before and after restart)

If both tests pass:
- ✓ Helpers persist across restarts
- ✓ Helpers are readable and writable
- ✓ Infrastructure is stable
- → **READY for E2E testing**

---

## Troubleshooting

### Validation shows FAIL after restart

**Cause:** HA didn't persist UI-created helpers (rare, but possible)

**Solution:**
1. Check: Developer Tools → States → search for "p1s_slot_to_spool"
2. If missing: Recreate helpers via UI
3. If present but unavailable: Check HA logs for errors
4. Try: Delete helpers, restart HA, recreate helpers

### Helper entity ID doesn't match

**Cause:** HA auto-generated a different entity ID

**Solution:**
1. Delete the helper
2. Before creating new helper, ensure no other helper has same/similar name
3. Create helper with exact name from checklist
4. HA should auto-generate correct entity ID

### Binding helper state is not '{}'

**Cause:** Something wrote to it (expected after validation)

**Solution:**
- This is normal! Validation writes '{}' as part of the test
- As long as validation passes, state doesn't matter

---

## Quick Reference

**Validation Command:**
```yaml
service: script.p1s_validate_persistence_infrastructure
```

**Required Entity IDs:**
- `input_text.p1s_slot_to_spool_binding_json` (max: 1024, initial: `{}`)
- `input_text.p1s_last_mapping_json` (max: 2048, initial: empty)

**Success Indicator:**
"P1S Infrastructure Validation: PASS" notification

---

**Next Step After PASS:** Proceed to E2E validation scenarios
