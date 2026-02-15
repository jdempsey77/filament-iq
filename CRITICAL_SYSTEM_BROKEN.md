# CRITICAL: Helper Configuration Corrupted HA System

## Status: SYSTEM BROKEN

After modifying existing helper definitions (`max` values), **ALL config-based `input_text` helpers are now missing** from Home Assistant, including ones that previously worked.

## What Happened

1. **Initial Problem**: New helpers wouldn't load (entity registry issue)
2. **Workaround Attempted**: Repurposed existing helpers, increased `max` values
3. **Result**: **BREAKING CHANGE** - Modifying `max` values caused HA to stop loading ALL config-based `input_text` helpers

## Evidence

```bash
$ curl .../api/states/input_text.p1s_init_seed_debug
{"message": "Entity not found."}

$ curl .../api/states | grep input_text.p1s
# Returns: NOT FOUND
```

Previously working helpers that are now missing:
- `input_text.p1s_init_seed_debug`
- `input_text.p1s_finish_automation_checkpoint`
- `input_text.p1s_last_active_tray`
- `input_text.p1s_tray_remaining_start_json`
- And all others

## Root Cause (Updated)

**Changing `max` attribute breaks entity registry sync**

Home Assistant's entity registry caches entity attributes. When config-based entities have their attributes changed (e.g., `max` value), HA may:
1. Fail to update the registry
2. Mark entities as invalid
3. Stop loading them entirely

This is a **known HA limitation**: Config-based helpers with modified attributes require:
- Manual entity registry cleanup, OR
- Recreation via UI (storage-based), OR
- Complete HA restart + registry rebuild

## System Impact

**PRODUCTION SYSTEM NOW NON-FUNCTIONAL**

- ✗ All P1S filament tracking helpers missing
- ✗ Automations referencing these helpers will fail
- ✗ Init automation cannot store start weights
- ✗ Finish automation cannot store end weights
- ✗ V1 auto-mapping completely broken
- ✗ Manual spool assignment broken

## Immediate Recovery Required

### Option 1: Revert Changes (FASTEST)
```bash
git revert HEAD~2  # Revert helper modifications
./scripts/manage_ha.sh --all
# Restart HA
```

### Option 2: Manual Entity Registry Cleanup (RISKY)
```bash
# SSH to HA
systemctl stop home-assistant
# Edit /config/.storage/core.entity_registry
# Remove all input_text.p1s_* entries
# OR delete entire registry file (loses all customizations)
systemctl start home-assistant
```

### Option 3: Recreate Helpers via UI (TEDIOUS)
1. Delete helper definitions from configuration.yaml
2. Recreate each helper manually in HA UI (Settings > Helpers)
3. Update all automation/script references

## Recommendation

**REVERT IMMEDIATELY**

The workaround caused more harm than the original issue. System must be rolled back to last known good state.

## Lessons Learned

1. **DO NOT modify `max` values** on existing config-based `input_text` helpers
2. **Entity registry is fragile** - changes to helper definitions can break registration
3. **Test in isolation** before deploying config changes that modify helper attributes
4. **Storage-based helpers** (created via UI) are more robust for runtime modifications

## Status

**BLOCKED - System requires immediate rollback**

---

**Awaiting user decision on recovery approach**
