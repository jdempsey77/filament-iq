# Filament Tracking Architecture Analysis

## 1) Core Architecture Questions

### Source of Truth for "Filament Remaining"

**Answer: Your system uses a HYBRID approach with multiple sources:**

1. **Spoolman `remaining_weight` field** (primary source of truth)
   - Entity: `sensor.spoolman_spool_<id>` with attribute `remaining_weight`
   - Updated by:
     - Manual weight updates via AMS Slot Manager UI
     - Automatic decrements after print completion

2. **Fuel Gauge Sensors** (calculated estimate for RFID-equipped Bambu filament)
   - Entities: `sensor.p1s_tray_N_fuel_gauge_remaining`
   - Formula: `tray_weight * remain / 100`
   - Source data: `sensor.p1s_01p00c5a3101668_ams_1_tray_N` attributes (`tray_weight`, `remain`)

3. **AMS Remaining Sensors** (display layer)
   - Entities: `sensor.ams_slot_N_remaining_g`
   - Reads from Spoolman: `state_attr('sensor.spoolman_spool_<id>', 'remaining_weight')`

**Precedence Logic (lines 548-554 in automations.yaml):**
```jinja2
{% set fg = states('sensor.p1s_tray_N_fuel_gauge_remaining') | float(-1) %}
{% set ams = states('sensor.ams_slot_N_remaining_g') | float(-1) %}
{% set effective = fg if fg > 0 else (ams if ams > 0 else -1) %}
```
Priority: Fuel Gauge → Spoolman/AMS → -1 (unknown)

### Entities Currently Used

**Print Status:**
- Entity: `sensor.p1s_01p00c5a3101668_print_status`
- States: `running`, `printing`, `idle`, `finish`, `finished`, `completed`, `complete`, `failed`, `pause`, `paused`, `prepare`, `init`, `slicing`
- Computed status: `sensor.p1s_operator_status` (lines 626-666 configuration.yaml)

**Print Progress:**
- Not explicitly tracked in your current config
- Likely available as `sensor.p1s_01p00c5a3101668_print_progress` (from Bambu integration)

**Active AMS Tray per Slot:**
- Attribute: `state_attr('sensor.p1s_01p00c5a3101668_ams_1_tray_N', 'active')` (boolean)
- State value: `sensor.p1s_01p00c5a3101668_active_tray` (display name string)
- Tray entities:
  - Slot 1: `sensor.p1s_01p00c5a3101668_ams_1_tray_1`
  - Slot 2: `sensor.p1s_01p00c5a3101668_ams_1_tray_2`
  - Slot 3: `sensor.p1s_01p00c5a3101668_ams_1_tray_3`
  - Slot 4: `sensor.p1s_01p00c5a3101668_ams_1_tray_4`
  - Slot 5: `sensor.p1s_01p00c5a3101668_ams_128_tray_1`
  - Slot 6: `sensor.p1s_01p00c5a3101668_ams_129_tray_1`

**Remaining Filament per Tray:**
- **Bambu AMS sensors** (raw from printer):
  - Fuel gauge: `sensor.p1s_tray_N_fuel_gauge_remaining` (lines 856-902 configuration.yaml)
- **Custom computed sensors** (from Spoolman):
  - Display: `sensor.ams_slot_N_remaining_g` (lines 671-709 configuration.yaml)
  - Source: Spoolman entity `sensor.spoolman_spool_<id>` attribute `remaining_weight`

**Trays Used This Print Memory:**
- Entity: `input_text.p1s_trays_used_this_print` (line 78 configuration.yaml)
- Format: Comma-separated slot numbers (e.g., "1,3,4")
- Cleared on print start (line 345-349 automations.yaml)
- Updated during print (lines 446-488 automations.yaml)

### Where Logic Runs

**Primary: HA Automations (YAML)** - All core logic in `automations.yaml`:
- Print start/finish detection
- Tray tracking during print
- Filament usage calculation
- Spoolman write-backs
- Low filament warnings
- Dropdown population

**Scripts** (`scripts.yaml`):
- Manual weight updates
- Spool assignment
- Filament/spool creation (REST API calls)
- Integration reload

**Template Sensors** (`configuration.yaml`):
- Fuel gauge calculations
- Display formatting
- Dropdown options generation

**REST Commands** (configuration.yaml lines 1132-1165):
- Spoolman API calls for create/update operations

**No evidence of:**
- AppDaemon apps
- Python scripts
- Node-RED
- Shell commands (beyond the test scripts you created)

### Write-Back Points to Spoolman

**1. HA Spoolman Integration Services:**
- `spoolman.use_spool_filament` (line 774 automations.yaml)
  - Decrements remaining_weight after print
  - Parameters: `id`, `use_weight`
- `spoolman.patch_spool` (lines 166-169, 304-323, etc. scripts.yaml)
  - Updates remaining_weight from manual scale input
  - Updates location field when assigning/unassigning trays
  - Parameters: `id`, `remaining_weight`, `location`

**2. REST API Calls** (configuration.yaml rest_command):
- `ams_spoolman_create_filament` (line 1149)
- `ams_spoolman_create_spool` (line 1161)
- Not used for weight updates (integration services preferred)

**Pattern: Both, but integration services are primary for weight updates.**

---

## 2) Spoolman Data Model Questions

### Relationship Between Filament/Spool/Tray

**Current Model:**
```
Filament (material definition)
  └─> Spool (physical roll with remaining weight)
       └─> Tray/Slot (AMS physical location, stored as "location" field)
```

**Mapping Strategy:**
- Each AMS slot maps to ONE Spoolman spool via `input_text.ams_slot_N_spool_id`
- Spool location field stores AMS position: `AMS1_Slot1`, `AMS1_Slot2`, etc.
- Spools not in AMS have location `Shelf`

### Critical Spoolman Fields

**Spool `remaining_weight` field:**
- Field name: `remaining_weight` (attribute on `sensor.spoolman_spool_<id>`)
- Read: `state_attr('sensor.spoolman_spool_<id>', 'remaining_weight')`
- Written by: `spoolman.patch_spool` or `spoolman.use_spool_filament`

**Spool "empty" threshold:**
- Warning: < 100g (line 327 automations.yaml)
- Critical: < 50g (line 319 automations.yaml)
- No automatic archival

**Spool ↔ Filament link:**
- Field: `filament_id` (required on spool creation)
- Attributes available: `filament_name`, `filament_material`, `filament_vendor_name`

**Spool ↔ Tray/Slot link:**
- Field: `location` (string, not enforced as enum)
- Your conventions:
  - `AMS1_Slot1` through `AMS1_Slot4`
  - `AMS2_HT_Slot1`, `AMS2_HT_Slot2`
  - `Shelf` (warehouse/inactive)
- HA mapping: `input_text.ams_slot_N_spool_id` stores the spool ID

### Multi-Spool/Multi-Tray Support

**Multiple spools per filament:** ✅ YES, SUPPORTED
- Normal Spoolman behavior
- Your filament dropdown filters out filaments that already have spools (lines 787-799 configuration.yaml)
- This filter should be removed or made optional

**Multiple trays per spool:** ❌ NO, NOT SUPPORTED
- Each tray has exactly one `ams_slot_N_spool_id`
- Scripts enforce one-to-one: when assigning, old spool is moved to `Shelf` (lines 302-307 scripts.yaml)
- **If manually broken, first tray to finish print would decrement the shared spool**

---

## 3) Print Accounting Questions

### Grams Used Calculation

**Current Method: AMS Delta (remaining_before − remaining_after)**

Implementation in `p1s_remaining_snapshot_on_finish` automation (lines 689-806 automations.yaml):

1. **Start snapshot** (`p1s_remaining_snapshot_init`, line 492):
   - On `print_status → running/printing`
   - Clear JSON helpers
   - Wait for tray `active=true` or `active_tray` state
   - Record: `{"slot": grams}` in `input_text.p1s_tray_remaining_start_json`
   - Source precedence: fuel gauge → AMS slot remaining

2. **During print tracking** (`p1s_remaining_snapshot_on_tray_first_active`, line 623):
   - When additional tray becomes `active=true`
   - Merge into start JSON (write-once per slot)

3. **Finish snapshot** (line 689):
   - On `print_status → finish/idle/failed`
   - Read end values for all slots in start JSON
   - Calculate: `used_g = max(0, start_g - end_g)` per slot
   - Call `spoolman.use_spool_filament` for each slot with `used_g > 0`

**NOT using:**
- Bambu print report / job stats
- Slicer estimate
- Equal split across trays (you do per-tray delta)

### Multi-Color Print Behavior

**Current: Best Case Implementation** ✅
- Each tray that goes `active=true` is tracked separately
- Each spool decremented by its actual delta
- Stored in `input_text.p1s_trays_used_this_print` as comma-separated slots (lines 446-488)

**Fallback Scenario:**
- If start snapshot is empty (line 724), automation stops with "start_dict empty, skipping end snapshot"
- No decrement happens (safest failure mode)

### Failure Rules

**Print Fails:**
- Status: `failed`, `error`
- Behavior: **Partial decrement still happens** (line 699 includes `failed` as trigger)
- Rationale: Filament was consumed even if print failed

**Sensors Unknown/Unavailable/0:**
- Start snapshot: `-1` for unavailable, stored as `0` in JSON (line 551)
  - Comment: "Include active tray even when effective is -1"
  - Reason: "printer often reports tray_weight=0/remain=-1 for active tray"
- Finish: If `used_g ≤ 0`, `spoolman.use_spool_filament` not called (line 773 condition)
- **No "needs manual reconcile" flag, just skips decrement**

**Unknown States in `print_status`:**
- Logged to logbook (line 206 automations.yaml)
- No action taken (safe default)

### Mid-Print Spool Swap

**Current Behavior:**
- Tray active tracking: Should add new tray to `p1s_trays_used_this_print`
- Start snapshot: Write-once per slot (lines 659-661) - does NOT update if slot already in start JSON
- **Problem: If user physically swaps spool mid-print, HA will decrement from ORIGINAL spool ID**
- `ams_slot_N_spool_id` is not re-read during print, only at start

---

## 4) Bambu Lab Brand vs Custom Sensors

### RFID (Bambu Lab) Filament

**Most Accurate Source:**
- Fuel gauge: `sensor.p1s_tray_N_fuel_gauge_remaining`
- Based on: `tray_weight * remain / 100`
- **Problem: Printer reports tray_weight=0 or remain=-1 when tray is active** (see line 552 comment)

### Non-Bambu Filament

**Custom Method:**
- Manual weigh-in via AMS Slot Manager UI
- Fields:
  - `input_number.ams_slot_N_gross_weight` (scale reading)
  - `input_select.ams_slot_N_spool_type` (Bambu Lab / Overture / Custom)
  - `input_number.ams_slot_N_tare_override` (for Custom type)
  - `input_number.ams_slot_N_extras_weight` (silica/adapters for Custom)
- Calculation: `remaining = max(0, gross - tare - extras)`
- Written to Spoolman via `ams_slot_N_assign_and_update` script

### Precedence Logic

**Current: Fuel gauge preferred if available**
```jinja2
{% set fg = states('sensor.p1s_tray_N_fuel_gauge_remaining') | float(-1) %}
{% set ams = states('sensor.ams_slot_N_remaining_g') | float(-1) %}
{% set effective = fg if fg > 0 else (ams if ams > 0 else -1) %}
```
(line 548 automations.yaml)

**Interpretation:**
- IF fuel gauge > 0: use fuel gauge
- ELSE IF AMS slot remaining > 0: use Spoolman remaining_weight
- ELSE: -1 (unknown)

**Not implemented:**
- "AMS always wins for Bambu SKU" (no SKU/RFID detection)
- Max/min/last_updated strategies

---

## 5) Management Flows (CRUD + Tray Assignment)

### A. Create New Filament

**UI Flow:** Dashboard → "Add Filament to Spoolman" card

**Required Fields:**
- Name: `input_text.spoolman_new_filament_name`
- Material: `input_text.spoolman_new_filament_material` (PLA, PETG, etc.)
- Diameter: `input_number.spoolman_new_filament_diameter` (default 1.75mm)
- Density: `input_number.spoolman_new_filament_density` (default 1.24 g/cm³)
- Weight: `input_number.spoolman_new_filament_weight` (default 1000g)

**Optional Fields:**
- Color hex: `input_text.spoolman_new_filament_color_hex`
- Vendor: `input_select.spoolman_new_filament_vendor` (dropdown from Spoolman API)

**Script:** `spoolman_add_filament` (lines 19-29 scripts.yaml)
- Calls: `rest_command.ams_spoolman_create_filament`
- Then: `script.reload_spoolman_integration`

**No custom profile mapping to Bambu Studio in HA.**

### B. Create New Spool

**UI Flow:** Dashboard → "Add Spool to Spoolman" card

**Required Fields:**
- Filament: `input_select.spoolman_new_spool_filament` (dropdown, format: "ID - Vendor Material Name")
- Remaining weight: `input_number.spoolman_new_spool_remaining_weight`

**Optional Fields:**
- Location: `input_text.spoolman_new_spool_location` (default "Shelf")

**Initial Weight Source:**
- User enters remaining weight (net filament weight)
- Not gross weight (you enter what's left, not spool+filament)

**Spool Type/Tare:**
- Not stored in Spoolman
- Stored per-slot in HA: `input_select.ams_slot_N_spool_type`

**Script:** `spoolman_add_spool` (lines 31-42 scripts.yaml)

### C. Assign Spool to Tray

**Two Methods:**

**1. Manual Assignment (Assign from Warehouse):**
- Select spool: `input_select.ams_assign_source_spool`
- Automation sets: `input_number.ams_assign_source_spool_id` (line 862 automations.yaml)
- Click button: Runs `ams_assign_to_slot_N` script
- Sets: `input_text.ams_slot_N_spool_id`

**2. Auto-Detection** (`ams_tray_auto_detect_and_assign`, line 907):
- Triggers: When AMS tray sensor state changes
- Fuzzy match: Vendor + Material + Name against Spoolman spools on `Shelf`
- Exactly 1 match: Auto-assign + set location to `AMS1_SlotN`
- 0 or 2+ matches: Notify for manual assignment

**Service Calls:**
- Updates spool ID: `input_text.set_value` on `ams_slot_N_spool_id`
- Updates location: `spoolman.patch_spool` with `location: "AMS1_SlotN"`

**NOT setting "active spool" in Spoolman** (no such field; location is the link).

### D. Update Weights

**Manual Override:**
- UI: "Assign & Update" button in slot popup
- Script: `ams_slot_N_assign_and_update` (lines 281-549 scripts.yaml)
- Reads: Gross weight, spool type, tare, extras
- Calculates: `remaining = max(0, gross - tare - extras)`
- Writes: `spoolman.patch_spool` with `remaining_weight` + `location`
- Then: `script.reload_spoolman_integration`

**Print-Finished Automation:**
- Automation: `p1s_remaining_snapshot_on_finish` (line 689)
- Calculates: `used_g = start_g - end_g` per slot
- Writes: `spoolman.use_spool_filament` with `use_weight: used_g`
- Then: `script.reload_spoolman_integration`

**Double-Apply Prevention:**
- Manual updates write `remaining_weight` (absolute)
- Print decrements use `use_spool_filament` (relative)
- **Conflict risk: Manual update DURING print will break delta calculation**
- No mutex/lock mechanism

### Most Common Breaks

Based on code analysis, likely issues:

1. **Wrong tray decremented:**
   - Mid-print spool swap not detected (start snapshot is write-once)

2. **Doubled decrement:**
   - Manual update + automatic decrement within same print cycle
   - No state flag to prevent this

3. **Decrement on failed prints:**
   - Current: Decrements happen on `failed` status (line 699)
   - May or may not be desired (filament was consumed)

4. **`trays_used_this_print` not reset:**
   - Reset happens on `running`/`printing` (line 345)
   - If print goes `prepare → failed` without reaching `running`, not cleared
   - Next print could inherit old list

5. **Spoolman update succeeds but HA UI stale:**
   - `script.reload_spoolman_integration` called after each update
   - Should refresh within 5-10 seconds
   - If integration is slow/broken, UI won't update

---

## 6) Testing + Simulation Questions

### Test Mode Toggle

**Recommendation: YES, add `input_boolean.filament_test_mode`**

Benefits:
- Test logic without real prints
- Validate multi-tray scenarios
- Debug snapshot math

### Mock Entities

**Approach Options:**

**Option 1: Template Sensors (Recommended)**
```yaml
template:
  - sensor:
      - name: "Mock Print Status"
        unique_id: mock_print_status
        state: "{{ states('input_select.test_print_status') }}"
      - name: "Mock Tray 1 Remaining"
        state: "{{ states('input_number.test_tray_1_remaining') }}"
```

**Option 2: Input Helpers (Simpler)**
- `input_select.test_print_status` (idle/running/finish/failed)
- `input_number.test_tray_N_remaining`
- `input_boolean.test_tray_N_active`

**Integration:**
- Modify automations to check `input_boolean.filament_test_mode`
- If true, read from mock entities instead of real Bambu sensors

### Simulator Scope

**Recommendation: Full simulation (start → tray events → finish)**

Test cases:
1. Single-tray print (simple)
2. Multi-tray print (all trays active from start)
3. Sequential tray activation (tray 1 runs out, switches to tray 2)
4. Failed print with partial filament use
5. Start snapshot failure (no active tray detected)

### Where to Run Tests

**Option A: Pure HA (Easiest)**
- Add test mode toggle + mock entities
- Create test automation: `test_simulate_print`
- Use `script.turn_on` to trigger test sequences
- Pros: No external dependencies
- Cons: Hard to assert/verify results programmatically

**Option B: AppDaemon Python Test Runner**
- Would need to install AppDaemon (not currently in your stack)
- Pros: Proper test framework, assertions, CI/CD
- Cons: New dependency, learning curve

**Option C: External Python Script (Recommended for CI/CD)**
- Hit HA REST API to:
  - Set test mode on
  - Trigger state changes
  - Read results
  - Assert correctness
- Hit Spoolman API to verify write-backs
- Pros: Proper testing framework (pytest), CI/CD friendly
- Cons: Requires Python environment, more complex setup

**Recommendation: Start with Option A (HA-native), move to Option C if you want automated regression testing.**

---

## Summary of Key Findings

### Strengths
- ✅ Hybrid remaining_weight tracking (Bambu + manual)
- ✅ Multi-tray print support with per-spool deltas
- ✅ Auto-detection and assignment
- ✅ Fuel gauge for RFID filament
- ✅ Low filament warnings

### Fragile Points
- ⚠️ Mid-print spool swap not detected
- ⚠️ Manual weight update during print breaks delta
- ⚠️ Start snapshot can be empty (tray_weight=0 issue)
- ⚠️ Failed prints decrement filament (may be undesired)
- ⚠️ No mutex between manual/automatic updates

### Missing Features
- ❌ Print progress tracking
- ❌ Bambu print report integration
- ❌ Slicer estimate comparison
- ❌ "Needs reconcile" flag for unknown states
- ❌ Test mode for validation

---

## Recommendations for Improvement

1. **Add test mode** (`input_boolean.filament_test_mode`)
2. **Add print-in-progress flag** to prevent manual updates during prints
3. **Add "last print reconciled" flag** to detect missed decrements
4. **Log start/end snapshots to persistent storage** for debugging
5. **Consider removing "failed" from decrement triggers** (make it user choice)
6. **Add validation: Alert if start_g < end_g** (impossible scenario)
7. **Document the "tray_weight=0 during print" Bambu quirk** in comments
8. **Add CI/CD test suite** (Option C above) for regression testing

---

*Generated: 2025-02-13*
*Based on: automations.yaml, configuration.yaml, scripts.yaml, dashboards/dashboard.stage.yaml*
