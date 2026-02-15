# E2E Validation Results: P1S Filament Tracking (V1 Opinionated)

## Date: 2026-02-15

## Executive Summary

**Final Verdict:** ❌ **NO-GO**

**Primary Blocker:** Critical infrastructure components not loading in Home Assistant

---

## Infrastructure Status

### Components Deployed ✓
1. **Scripts**: 
   - `script.p1s_choose_spool_for_slot_v1` (loaded successfully)
   - Test scenario scripts (loaded)
2. **Automations**: 
   - `automation.p1s_snapshot_remaining_on_print_finish` (exists, triggered)
3. **Flags**:
   - `input_boolean.p1s_auto_mode_opinionated` (exists, state: ON)

### Components FAILED ✗
1. **Helpers from configuration.yaml**:
   - `input_text.p1s_slot_to_spool_binding_json` - **NOT LOADED**
   - `input_text.p1s_last_mapping_json` - **NOT LOADED**
   - **Root cause**: Unknown HA loading issue despite:
     - Valid YAML syntax ✓
     - Helpers defined in config (lines 93-98) ✓
     - Config deployed to remote ✓
     - Multiple HA restarts performed ✓
     - Unicode characters removed ✓

2. **Workaround Attempted**:
   - Manual helper creation via API: **Temporary success**
   - Helpers created but showed as "unavailable" after automation run
   - Persistence layer completely non-functional

---

## Test Execution Log

### Scenario 1: Single Slot Print (35g usage)

**Setup:**
- Test script: `script.test_scenario_1_single_slot`
- Slot 1: 350g → 315g (35g consumption)
- Debug trigger: Fired successfully

**Results:**
- ✓ Test script executed
- ✓ Helpers seeded (start/end values set)
- ✓ Debug trigger turned ON
- ✓ Finish automation triggered at `2026-02-15T01:28:00.520511+00:00`
- ✗ **Binding JSON: Empty** (`{}`)
- ✗ **Last mapping JSON: Empty**
- ✗ **Finish checkpoint: unavailable**
- ✗ **Slot 1 spool_id: unavailable**

**Analysis:**
The finish automation ran, but:
1. Script `p1s_choose_spool_for_slot_v1` either didn't execute or failed silently
2. No mapping was persisted
3. No logging occurred
4. Checkpoint helper not set (suggests automation may have exited early)

**Failure Mode:** Silent failure - no errors in HA log, but expected state changes didn't occur.

---

### Scenarios 2-5: NOT EXECUTED

Cannot proceed without functional infrastructure from Scenario 1.

---

## Critical Issues Identified

### Issue 1: Helper Loading Failure
**Severity:** CRITICAL  
**Description:** New `input_text` helpers defined in `configuration.yaml` do not load in HA despite valid syntax and multiple deployment/restart cycles.

**Evidence:**
```
# grep output from remote HA
93:  p1s_slot_to_spool_binding_json:
94:    name: "P1S Slot to Spool Binding (JSON)"
95:    max: 1024
96:  p1s_last_mapping_json:
97:    name: "P1S Last Mapping Result"
98:    max: 2048

# API query result
Entity: NOT FOUND (after 3+ restarts)
```

**Impact:** Cannot persist slot-to-spool bindings or log mapping decisions.

### Issue 2: Helper State Corruption
**Severity:** HIGH  
**Description:** Manually created helpers (via API) immediately show as "unavailable" after automation interactions.

**Evidence:**
- Created via API: Success (state: `{}`)
- After automation run: State becomes `unavailable`
- Checkpoint helper (`p1s_finish_automation_checkpoint`): also `unavailable`

**Impact:** Even workarounds fail. No persistent storage functional.

### Issue 3: Silent Script Failure
**Severity:** HIGH  
**Description:** Script `p1s_choose_spool_for_slot_v1` appears to execute but produces no observable output or state changes.

**Expected:** 
- `p1s_last_mapping_json` populated with mapping details
- `p1s_slot_to_spool_binding_json` updated with `{"1": spool_id}`
- `ams_slot_1_spool_id` set to chosen spool

**Actual:**
- All helpers remain empty or unavailable
- No error messages in HA log

**Impact:** Cannot verify deterministic mapping logic.

---

## Deterministic Invariants: NOT MET

| Invariant | Status | Evidence |
|-----------|--------|----------|
| Finish NEVER blocks decrement | ❌ CANNOT VERIFY | Script didn't execute/failed |
| Every used slot maps to spool | ❌ FAIL | No mapping occurred |
| Mapping is deterministic | ❌ CANNOT TEST | No persistence layer |
| Slot binding persists | ❌ FAIL | Helpers don't exist |
| Restart doesn't remap | ❌ CANNOT TEST | Helpers don't persist |
| No "Start Snapshot Empty" regression | ⚠️ UNKNOWN | Test didn't complete |

---

## Root Cause Hypothesis

**Most Likely:**
HA configuration parsing issue specific to the new helpers. Possible causes:
1. **Max limit**: HA may have a limit on `input_text` entities in configuration.yaml
2. **Storage conflict**: Existing `.storage/input_text` file may be interfering with config-based helpers
3. **HA version incompatibility**: Current HA version may not support mixing config-based and storage-based `input_text` helpers
4. **Config merge issue**: HA may be ignoring new entries added to existing `input_text:` block

**Evidence:**
- Other `input_text` helpers work (9 existing P1S helpers functional)
- Storage file shows only 5 items, but HA reports 22 entities (config-based work)
- New helpers in same `input_text:` block as working helpers
- No YAML syntax errors

---

## Recommended Actions

### Immediate (Required for Production)

1. **Investigate HA Storage**
   ```bash
   # Check if storage is preventing config load
   ssh root@ha "cat /config/.storage/input_text"
   ```

2. **Alternative Storage**:
   - Use existing unused helpers (repurpose)
   - Move to `var/` file storage
   - Use AppDaemon persistent storage
   - Use SQL database helper

3. **Test Minimal Reproduction**:
   - Create single new `input_text` helper in fresh config
   - Verify if HA version supports config-based `input_text`

### Short-term (Workaround)

1. **Repurpose Existing Helpers**:
   - Use `input_text.p1s_init_seed_debug` for binding JSON (currently unused)
   - Use `input_text.p1s_print_job_key` for mapping log (low priority)
   
2. **Test with Repurposed Helpers**:
   - Modify script to use existing helpers
   - Re-run E2E validation

### Long-term (Architecture)

1. **External Persistence**:
   - Move to AppDaemon for state management
   - Use InfluxDB/SQL for historical mappings
   - Consider MQTT retain for slot bindings

2. **Simplify Dependencies**:
   - Reduce reliance on HA helpers for critical state
   - Use file-based JSON storage in `/config/custom_components/`

---

## Deployment Status

**Branch:** `bugfix/start-snapshot-empty-and-ams-spam`

**Commits:**
- `bd3da8f`: Add V1 helpers to configuration.yaml
- `e721c42`: Add p1s_choose_spool_for_slot_v1 script
- `35bae76`: Integrate script into finish automation
- `d0eb14e`: Add mapping details to notification
- `88e1124`: Documentation (V1_OPINIONATED_IMPLEMENTATION.md)
- `19ed0b9`: E2E test framework
- `d648016`: Test scenario scripts

**Current State:**
- ✓ All code deployed to HA
- ✓ Automations reloaded
- ✓ Scripts loaded
- ✗ Helpers not functional
- ✗ System not operational

---

## Final Assessment

### GO/NO-GO Criteria

| Criterion | Required | Actual | Status |
|-----------|----------|--------|--------|
| Infrastructure loaded | YES | Partial | ❌ NO-GO |
| Helpers functional | YES | No | ❌ NO-GO |
| Script executes | YES | Unknown | ❌ NO-GO |
| Mapping persists | YES | No | ❌ NO-GO |
| Tests pass | YES | Cannot run | ❌ NO-GO |

### Production Readiness: **NOT READY**

**Reasons:**
1. Core persistence infrastructure non-functional
2. Cannot validate deterministic behavior without working helpers
3. Silent failures prevent debugging
4. No confidence in system reliability

---

## Next Steps for User

**Option 1: Investigate & Fix (Recommended)**
1. Check HA version compatibility with config-based `input_text`
2. Examine `.storage/input_text` for conflicts
3. Test minimal reproduction (single helper)
4. Consult HA community forums

**Option 2: Workaround (Faster)**
1. Repurpose existing unused helpers
2. Update script to use repurposed helpers
3. Re-run E2E validation
4. Accept helper names won't be semantic

**Option 3: Architectural Change (Long-term)**
1. Move persistence to AppDaemon
2. Use external storage (file/DB)
3. Reduce HA helper dependencies

---

## Contact Points for Debugging

- HA Config: `/config/configuration.yaml` (lines 93-98)
- Script: `/config/scripts.yaml` (`p1s_choose_spool_for_slot_v1`)
- Automation: `/config/automations.yaml` (line 743, id: `p1s_remaining_snapshot_on_finish`)
- Storage: `/config/.storage/input_text`

---

**End of Report**

**Status: BLOCKED - Infrastructure failure prevents validation**
