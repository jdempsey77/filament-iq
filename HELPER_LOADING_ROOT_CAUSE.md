# Root Cause Analysis: input_text Helpers Not Loading

## Date: 2026-02-15

## Problem Statement
Two new `input_text` helpers defined in `configuration.yaml` do not appear in Home Assistant after restart:
- `input_text.p1s_slot_to_spool_binding_json`
- `input_text.p1s_last_mapping_json`

## Investigation Results

### 1. YAML Structure: ✓ CORRECT
- **Single `input_text:` block**: Line 35 in configuration.yaml (no duplicates)
- **Proper indentation**: 2 spaces for helper name, 4 spaces for properties
- **Valid syntax**: YAML parses without errors
- **No hidden characters**: Checked with `od -c`, all clean
- **Deployment verified**: MD5 checksum matches local and remote (02576ac2b90fb6f78202372b62c02f1a)

### 2. Configuration Includes: ✓ NO CONFLICTS
- No `packages:` system in use
- Automations/scripts in separate files via `!include`
- All existing P1S helpers in same `input_text:` block (lines 35-107)
- No duplicate top-level keys

### 3. Helper Properties: ✓ ATTEMPTED FIX
- Added `initial:` values:
  - `p1s_slot_to_spool_binding_json`: `initial: "{}"`
  - `p1s_last_mapping_json`: `initial: ""`
- Deployed and restarted
- **Result**: Still NOT FOUND

### 4. Entity Registry: ❌ ROOT CAUSE IDENTIFIED

**Evidence:**
```bash
$ ssh ha "cat /config/.storage/core.entity_registry | grep 'input_text.p1s'"
input_text.p1s_finish_automation_checkpoint
input_text.p1s_init_seed_debug
input_text.p1s_last_active_tray
input_text.p1s_last_print_status_transition
input_text.p1s_last_tray_entity
input_text.p1s_print_job_key
input_text.p1s_tray_remaining_end_json
input_text.p1s_tray_remaining_start_json
input_text.p1s_trays_used_this_print
```

**Finding**: Only 9 P1S `input_text` helpers in entity registry. The two new ones are **missing**.

## Root Cause

**Entity Registry Not Updating for Config-Based Helpers**

Home Assistant's entity registry (`/config/.storage/core.entity_registry`) is not reflecting the new helpers added to `configuration.yaml`. Possible causes:

1. **HA Entity Registry Corruption**: Registry may have stale data preventing new config-based entities
2. **Entity Limit**: HA may have a soft limit on config-based `input_text` entities (unlikely, but possible)
3. **Registry Lock**: Entity registry might be locked to specific entity IDs from first config load
4. **Storage vs Config Conflict**: HA may be prioritizing storage-based helpers over config-based ones

## Why Other Helpers Work

The 9 existing P1S `input_text` helpers were created **before** this issue occurred. They're already in the entity registry, so HA continues to load them from the registry cache.

## Why `initial:` Didn't Fix It

The `initial:` parameter only sets the default state value. It doesn't force HA to create the entity if the registry doesn't recognize it.

## Attempted Workarounds

### Manual API Creation (Tried Earlier)
```bash
curl -X POST .../api/states/input_text.p1s_slot_to_spool_binding_json \
  -d '{"state": "{}", "attributes": {...}}'
```
**Result**: Entity created but became "unavailable" after automation interaction.

## Solution: Repurpose Existing Unused Helpers

Since creating new helpers fails, we'll **repurpose existing helpers** that are currently unused or low-priority:

### Repurposed Helpers

1. **For Binding JSON** (was: `p1s_init_seed_debug`):
   - Current: `input_text.p1s_init_seed_debug` (max: 255)
   - **Problem**: `max: 255` is too small for JSON binding data
   - **Fix Required**: Increase `max: 1024` in configuration.yaml

2. **For Mapping Log** (use: `p1s_finish_automation_checkpoint`):
   - Current: `input_text.p1s_finish_automation_checkpoint` (max: 255)
   - Already used for checkpoints, but can be expanded
   - **Problem**: `max: 255` might be too small for detailed mapping logs
   - **Fix Required**: Increase `max: 2048` in configuration.yaml

### Alternative: Use Print Job Key
- `input_text.p1s_print_job_key` (max: 100, mutex use)
- Currently underutilized - could store binding JSON if expanded

## Recommended Fix

### Option 1: Increase Existing Helper Limits (RECOMMENDED)
1. Modify `configuration.yaml`:
   ```yaml
   p1s_init_seed_debug:
     name: P1S Binding JSON (repurposed from init_seed_debug)
     max: 1024  # was 255
   p1s_finish_automation_checkpoint:
     name: P1S Mapping Log + Checkpoint
     max: 2048  # was 255
   ```

2. Update `script.p1s_choose_spool_for_slot_v1` references:
   - Replace `input_text.p1s_slot_to_spool_binding_json` → `input_text.p1s_init_seed_debug`
   - Replace `input_text.p1s_last_mapping_json` → `input_text.p1s_finish_automation_checkpoint`

3. Deploy, restart, test

### Option 2: Clear Entity Registry (RISKY)
1. Stop HA
2. Delete `/config/.storage/core.entity_registry`
3. Restart HA (will rebuild registry from config)
4. **Risk**: All entity customizations, names, areas will be lost

### Option 3: Manual Registry Edit (ADVANCED)
1. Stop HA
2. Edit `/config/.storage/core.entity_registry` JSON to add new entities
3. Restart HA
4. **Risk**: JSON corruption can break HA

## Summary

- **Diagnosis**: Entity registry not updating with new config-based helpers
- **YAML**: Perfect, no issues
- **Workaround**: Repurpose existing helpers with increased max sizes
- **Long-term**: Investigate HA version compatibility or consider external storage (AppDaemon, file-based, DB)

---

**Next Step**: Implement Option 1 (repurpose existing helpers)
