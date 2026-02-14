# Mutex Rehydration Implementation

## Implementation Date: 2026-02-14
## Commit: 9e225ba
## Branch: fix/eliminate-json-parsing

---

## Change Summary

**Added automation:** `p1s_rehydrate_mutex_on_restart`
- **ID:** `p1s_rehydrate_mutex_on_restart`
- **Alias:** P1S – rehydrate print active mutex on HA restart
- **Location:** `automations.yaml` (before line 1014, after tray auto-detect)

---

## Automation Definition

```yaml
- id: p1s_rehydrate_mutex_on_restart
  alias: P1S – rehydrate print active mutex on HA restart
  description: If HA restarts while print is active, restore mutex to ON to prevent manual slot mapping changes
  trigger:
    - platform: homeassistant
      event: start
  condition:
    - condition: state
      entity_id: sensor.p1s_01p00c5a3101668_print_status
      state:
        - running
        - printing
        - pause
        - paused
  action:
    - service: input_boolean.turn_on
      target:
        entity_id: input_boolean.p1s_print_active
  mode: single
```

---

## Purpose

Restores `input_boolean.p1s_print_active` to ON when Home Assistant restarts while a print is active.

**Protects Against:**
1. Manual slot mapping changes via dashboard dropdowns after restart
2. Missed spool swap detection after restart

**Does NOT Affect:**
- Finish accounting (already protected by trigger design)
- Double-decrement protection (already protected by trigger design)

---

## Deployment Evidence (Read-Only Verification)

### Automation Exists and Enabled
```json
{
  "entity_id": "automation.p1s_rehydrate_print_active_mutex_on_ha_restart",
  "state": "on",
  "last_triggered": "2026-02-14T19:46:12.050600+00:00"
}
```

### Manual Trigger Test (Simulating Restart)
**Before trigger:**
```
print_status: running
p1s_print_active: off
```

**After trigger:**
```
print_status: running
p1s_print_active: on
last_changed: 2026-02-14T19:46:12.053107+00:00
```

**Result:** ✅ Automation successfully restored mutex to ON when print_status was running

---

## Behavior on Next HA Restart

**Scenario: Print is Active**
1. HA restarts
2. `input_boolean.p1s_print_active` resets to OFF (initial: false)
3. HA loads, fires `homeassistant start` event
4. Rehydration automation triggers
5. Condition checks: `print_status` in [running, printing, pause, paused]
6. If TRUE: `input_boolean.turn_on` → mutex restored to ON
7. User cannot manually change slot mappings via dashboard
8. Spool swap detection re-enabled

**Scenario: No Print Active**
1. HA restarts
2. Rehydration automation triggers
3. Condition checks: `print_status` NOT in active states
4. Condition FAILS → automation stops
5. Mutex remains OFF (correct)

---

## Rollback Instructions

### Option 1: Revert Commit
```bash
cd /Users/jdempsey/code/home_assistant
git revert HEAD
./scripts/manage_ha.sh --automations
```

### Option 2: Manual Deletion
```bash
# Edit automations.yaml, remove lines with id: p1s_rehydrate_mutex_on_restart
./scripts/manage_ha.sh --automations
```

### Option 3: Disable Automation (No File Changes)
Via HA UI: Settings → Automations → "P1S – rehydrate print active mutex on HA restart" → Disable

---

## Testing Notes

**Tested via manual trigger:**
- Automation successfully turned mutex ON when print_status = running
- Last triggered timestamp updated correctly
- No errors in HA logs

**Real restart testing:**
- Will occur naturally on next HA restart during active print
- Expected behavior: mutex will turn ON within seconds of HA start
- Can verify by checking `input_boolean.p1s_print_active` state after restart

---

## Files Changed

**automations.yaml:** +21 lines
- Added new automation before auto-detect tray changes section

**No other files modified**

---

## Safety Notes

- Automation has `mode: single` (cannot run multiple times concurrently)
- Only triggers once per HA start
- Condition prevents false positives (only runs if print actually active)
- Does NOT interfere with finish accounting
- Does NOT change init or finish automation logic
- Minimal, isolated change

---

## Known Limitations

- Does NOT prevent manual slot changes via HA API (bypasses dashboard scripts)
- Does NOT prevent manual changes via `input_text.set_value` service calls
- Only protects against dashboard dropdown scripts (`ams_slot_N_set_spool`)

These limitations are acceptable as:
1. API/service calls are advanced user actions (expected behavior)
2. Dashboard dropdowns are the primary user interaction path
3. Spool swap detection still fires for any slot change when mutex ON
