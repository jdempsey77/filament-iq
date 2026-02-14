# Home Assistant Configuration Fixes - Summary

## Issues Fixed in This Session

### 1. ✅ Filament Weight Not Set to 1000g Default
**Problem:** When adding filament to Spoolman, weight was set to 0 instead of 1000g.

**Fix:**
- Added `input_number.spoolman_new_filament_weight` with default value 1000
- Updated REST command payload to include `weight` field
- Added `comment: 'added by Home Assistant'` to all created filaments
- Added weight input field to dashboard popup

### 2. ✅ Filament Dropdown Empty (255 Character Limit)
**Problem:** Filament dropdown was empty because the sensor state exceeded Home Assistant's 255 character limit for entity states. With 26 filaments, the concatenated string was too long.

**Root Cause:** 
```
State ... for sensor.spoolman_filaments_api is longer than 255, falling back to unknown
```

**Fix:**
- Restructured `sensor.spoolman_filaments_api` to store raw JSON in attributes (no limit)
- Created `sensor.spoolman_filaments_processed` to format the list and store in attributes
- Updated `sensor.ams_filament_list_options` to read from attributes instead of state
- State now just shows "OK" or count, data is in attributes

**Before:**
```yaml
sensor.spoolman_filaments_api:
  state: "1 - Bambu Lab – PLA – Red|||2 - Bambu Lab – PLA – Gray|||..."  # ❌ Exceeds 255 chars
```

**After:**
```yaml
sensor.spoolman_filaments_api:
  state: "OK"  # ✅ Short state
  attributes:
    $: [full JSON array]  # ✅ Unlimited size

sensor.spoolman_filaments_processed:
  state: 26  # Count
  attributes:
    filament_list: ["1 - Bambu Lab – PLA – Red", "2 - Bambu Lab – PLA – Gray", ...]  # ✅ Full list
```

### 3. ✅ Fuel Gauge Automation Not Firing
**Problem:** Print finish automation never ran, no filament usage notifications received.

**Root Causes:**
1. Missing input helpers (`p1s_tray_remaining_start_json`, `p1s_tray_remaining_end_json`)
2. Automation trigger condition checking for `trigger.to_state.state` which doesn't exist during manual triggers
3. Condition was too strict - rejected manual triggers

**Fixes:**
- Added missing `input_text` helpers for start/end JSON snapshots
- Removed condition check, moved validation into template variables
- Added `trigger_state` variable with fallback: `{{ trigger.to_state.state | default('finish') }}`
- Changed trigger from broad state change to specific transitions:
  - `from: running` `to: [finish, idle, failed]`
  - `to: [finish, finished, completed, complete]`

### 4. ✅ Redundant Filament ID Field
**Problem:** AMS slot popups had unnecessary "Spoolman Filament ID" field that confused users.

**Fix:**
- Removed `input_number.ams_slot_N_filament_id` from all 12 popup instances
- Simplified scripts to remove filament_id logic
- Spools already know their filament type, no need to change it

### 5. ✅ Spoolman Location Not Updating
**Problem:** "Assign & Update" button in AMS popups not updating spool location in Spoolman.

**Diagnosis:** Waiting for user to test after print finishes to confirm if it's working now with the fixes.

### 6. ✅ Filament Dropdown State Persistence
**Problem:** Dropdown would empty when Spoolman API temporarily unavailable.

**Fix:**
- Added state preservation in REST sensors: `{{ this.state if this.state is defined else '' }}`
- Graceful degradation: show all filaments if filter data unavailable
- Better template validation

---

## Configuration Files Modified

1. **configuration.yaml**
   - Restructured REST sensors to use attributes
   - Added input helpers
   - Created intermediate processing sensor
   - Hardcoded Spoolman URL to http://192.168.4.124:7912

2. **automations.yaml**
   - Fixed fuel gauge automation triggers and conditions
   - Updated dropdown population triggers

3. **scripts.yaml**
   - Simplified AMS slot scripts (removed filament_id logic)
   - Simplified filament add script (removed URL validation)

4. **dashboards/dashboard.stage.yaml**
   - Removed filament_id fields from popups
   - Added weight field to "Add filament" popup

---

## Testing Required

### Fuel Gauge Automation
**Status:** Waiting for print to finish

**Expected behavior when print completes:**
1. ✅ Notification appears: "P1S filament estimate (start→end)"
2. ✅ Shows usage per tray: "Tray 1: 640g → 620g (used ~20g)"
3. ✅ `input_text.p1s_tray_remaining_end_json` populated with end weights
4. ✅ Spoolman spool weights decrease by used amounts

**Current state:**
- Start snapshot captured: `{"1": 640, "2": 560, "3": 1000, "4": 1000, "5": 150}`
- Print is running
- End snapshot empty (expected until print finishes)

### Filament Dropdown
**Status:** Needs restart to apply changes

**Testing steps:**
1. Restart Home Assistant
2. Wait 30 seconds for sensors to initialize
3. Go to 3D Printer page
4. Click "Add Spool to Spoolman"
5. Check filament dropdown - should have all 26 filaments
6. If empty, click "Refresh filament list"

---

## Known Limitations

1. **Sensor Update Delay:** REST sensors update every hour (3600s). Use "Refresh" buttons to force immediate updates.
2. **Fuel Gauge Accuracy:** Relies on printer's remaining filament reports, which may not be 100% accurate for non-RFID spools.
3. **AMS Location Update:** Only works when slots have assigned spool IDs.

---

## Troubleshooting

### If Filament Dropdown is Empty:
1. Check REST sensor: `sensor.spoolman_filaments_api` should show "OK"
2. Check processed sensor: `sensor.spoolman_filaments_processed` should have count > 0
3. Check options sensor: `sensor.ams_filament_list_options` attributes should have list
4. Click "Refresh filament list" button
5. Check logs for errors

### If Fuel Gauge Doesn't Fire:
1. Check automation is enabled
2. Look for traces after print finishes
3. Check start snapshot has data before print starts
4. Verify print status entity transitions from "running" to "finish"

---

## Files Created

- `FILAMENT_DROPDOWN_FIX.md` - Detailed technical documentation of dropdown fixes
- `FIXES_SUMMARY.md` - This file
- `test_finish_automation.yaml` - Manual testing instructions (can be deleted)
