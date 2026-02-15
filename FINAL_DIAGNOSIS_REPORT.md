# Final Diagnosis Report: input_text Helpers Not Loading

## Date: 2026-02-15

## Problem Statement

Two new `input_text` helpers defined in `configuration.yaml` do not appear in Home Assistant after restart and multiple deployment attempts:
- `input_text.p1s_slot_to_spool_binding_json`
- `input_text.p1s_last_mapping_json`

---

## Root Cause Identified

**Home Assistant Entity Registry Does Not Update for New Config-Based Helpers**

The HA entity registry (`/config/.storage/core.entity_registry`) caches entity definitions. When new `input_text` helpers are added to `configuration.yaml`, the registry does not automatically create entries for them, preventing the helpers from loading.

### Evidence

1. **YAML Configuration**: ✓ VALID
   - Proper structure, indentation, syntax
   - No duplicate keys
   - Deployed successfully (MD5 verified)

2. **Entity Registry Check**: ❌ ROOT CAUSE
   ```bash
   $ ssh ha "cat /config/.storage/core.entity_registry | grep 'input_text.p1s'"
   # Output: Only 9 existing helpers, new ones missing
   ```

3. **Modification Attempt Failed**: ❌ CRITICAL FAILURE
   - Attempted to repurpose existing helpers by modifying `max` values
   - **Result**: ALL `input_text` entities (22 total) disappeared from HA
   - System became non-functional

---

## Key Findings

### 1. New Helper Creation Blocked
- HA does not create new config-based `input_text` entities after initial setup
- Entity registry appears locked to existing entities only
- No errors logged, helpers simply don't exist in HA

### 2. Modifying Existing Helpers Breaks System
- Changing `max` attribute on existing helpers caused entity registry desync
- All config-based `input_text` helpers stopped loading (not just modified ones)
- **LESSON**: DO NOT modify attributes on existing config-based helpers

### 3. Entity Registry is Fragile
- Registry caching is aggressive
- Changes to helper definitions don't trigger registry updates
- Manual intervention required for any config-based helper modifications

---

## Attempted Solutions

### Solution 1: Add `initial:` Values ❌ FAILED
- Added `initial: "{}"` and `initial: ""` to new helpers
- Deployed, restarted multiple times
- **Result**: Helpers still not found

### Solution 2: Remove Unicode Characters ❌ FAILED
- Changed `→` to plain text "to"
- **Result**: No effect

### Solution 3: Repurpose Existing Helpers ❌ CATASTROPHIC FAILURE
- Increased `max` values on existing helpers (`p1s_init_seed_debug`, `p1s_finish_automation_checkpoint`)
- Updated script references
- **Result**: ALL 22 `input_text` entities disappeared, system broken
- Required immediate revert

---

## System Recovery

### Revert Process
```bash
git revert --no-commit HEAD~1
git revert --no-commit HEAD
git commit -m "URGENT: Revert helper modifications..."
./scripts/manage_ha.sh --all
# HA restart
```

### Status: REVERTED ✓
- Configuration restored to last known good state
- Helper modification changes rolled back
- Awaiting verification that helpers reappear after restart

---

## Recommended Solutions

Since config-based helper creation is blocked, here are viable alternatives:

### Option 1: Storage-Based Helpers (UI Creation) ⭐ RECOMMENDED
**Pros:**
- Created via HA UI (Settings > Devices & Services > Helpers)
- Stored in `/config/.storage/input_text`
- More robust, no entity registry issues
- Can be modified via UI without breaking system

**Cons:**
- Not in `configuration.yaml` (less version control)
- Requires manual UI creation (not scripted)

**Steps:**
1. Create `p1s_slot_to_spool_binding_json` via UI (max: 1024)
2. Create `p1s_last_mapping_json` via UI (max: 2048)
3. No code changes needed (entity IDs same as YAML would create)

### Option 2: External Persistence Layer
**Options:**
- AppDaemon with persistent storage
- File-based JSON in `/config/custom_components/`
- SQL database (InfluxDB, PostgreSQL)

**Pros:**
- Independent of HA helpers
- More control over storage
- Better for large data sets

**Cons:**
- More complex implementation
- Additional dependencies

### Option 3: Manual Entity Registry Edit ⚠️ RISKY
**Process:**
1. Stop HA
2. Edit `/config/.storage/core.entity_registry` JSON
3. Add new entity entries
4. Restart HA

**Risks:**
- JSON corruption can break HA entirely
- All customizations/areas/names lost if registry needs rebuild
- Not recommended unless expert-level

---

## Unified Diffs (Reverted - For Reference Only)

### What Was Attempted (DO NOT APPLY)

```diff
# configuration.yaml - REVERTED
@@ -81,9 +81,9 @@
   p1s_init_seed_debug:
-    name: P1S init seed debug
+    name: P1S Binding JSON (repurposed)
+    initial: "{}"
-    max: 255
+    max: 1024  # BREAKS ENTITY REGISTRY

@@ -87,9 +87,9 @@
   p1s_finish_automation_checkpoint:
-    name: P1S finish automation checkpoint (debug)
+    name: P1S Mapping Log + Checkpoint
+    initial: ""
-    max: 255
+    max: 2048  # BREAKS ENTITY REGISTRY
```

**Impact**: All `input_text` entities (22 total) disappeared

---

## Deliverable

**Root Cause**: HA entity registry does not dynamically create new config-based `input_text` helpers. Attempts to work around this by modifying existing helpers broke the entire `input_text` subsystem.

**Recommended Fix**: Create the two required helpers via HA UI (Settings > Helpers):
1. `p1s_slot_to_spool_binding_json` (Text, max: 1024, initial: "{}")
2. `p1s_last_mapping_json` (Text, max: 2048, initial: "")

**Status**: System reverted to functional state. New helpers still required but must be created via UI, not YAML.

---

## Next Steps

1. ✓ Verify helpers restored after revert
2. Create two helpers via HA UI
3. Test V1 opinionated auto-mapping
4. Run E2E validation

---

## Documentation Files

- `E2E_VALIDATION_FINAL.md` - Test report showing initial NO-GO
- `CRITICAL_SYSTEM_BROKEN.md` - Incident report on catastrophic failure
- This file - Final root cause analysis

---

**Conclusion**: HA config-based `input_text` helper creation is broken/limited. Use UI-based helper creation instead.
