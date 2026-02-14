# Filament Tracking Deep Dive - Precise Implementation Details

This document answers the critical "how exactly" questions for implementing test mode and fixing fragile points.

---

## A) Spool ↔ Slot Mapping (CRITICAL FOR CORRECTNESS)

### How Each AMS Slot Maps to Spoolman spool_id

**Answer: Stored in HA helpers (input_text), NOT in Spoolman location field**

**Mapping Storage:**
```yaml
# configuration.yaml lines 54-71
input_text:
  ams_slot_1_spool_id:
    name: AMS Slot 1 (AMS1_Slot1) – Spoolman spool ID
    initial: "1"
  ams_slot_2_spool_id:
    name: AMS Slot 2 (AMS1_Slot2) – Spoolman spool ID
    initial: "2"
  # ... through slot 6
```

**Critical Point:** These are TEXT helpers, not numbers. Must cast to int when using:
```jinja2
# automations.yaml line 767
spool_id: "{{ states('input_text.ams_slot_' ~ repeat.item ~ '_spool_id') | int(0) }}"
```

**Spoolman Location Field Usage:**
- Location field ALSO updated to track physical position
- Values: `AMS1_Slot1`, `AMS1_Slot2`, `AMS1_Slot3`, `AMS1_Slot4`, `AMS2_HT_Slot1`, `AMS2_HT_Slot2`, `Shelf`
- Updated via `spoolman.patch_spool` when assigning (scripts.yaml lines 318-323)
- **Location is metadata, NOT the source of truth for slot→spool mapping**

### When `sensor.ams_slot_N_remaining_g` Reads from Spoolman

**Exact Template (configuration.yaml line 674):**
```jinja2
state: "{{ state_attr('sensor.spoolman_spool_' ~ states('input_text.ams_slot_1_spool_id'), 'remaining_weight') | default(states('sensor.spoolman_spool_' ~ states('input_text.ams_slot_1_spool_id'))) | float(0) | round(0) }}"
```

**Mapping Key:** `input_text.ams_slot_N_spool_id` → constructs entity_id `sensor.spoolman_spool_<id>`

**Example:**
- Slot 1: `input_text.ams_slot_1_spool_id` = "42"
- Reads: `sensor.spoolman_spool_42` attribute `remaining_weight`

**Fallback Chain:**
1. Try `state_attr('sensor.spoolman_spool_42', 'remaining_weight')`
2. If null/unavailable, try `states('sensor.spoolman_spool_42')` (state value)
3. If null/unavailable, return 0

---

## B) Snapshot + Delta Mechanics

### Where Start/End Snapshots are Stored

**Answer: `input_text` JSON blobs**

**Storage (configuration.yaml lines 84-89):**
```yaml
input_text:
  p1s_tray_remaining_start_json:
    name: P1S tray remaining start JSON
    max: 255
  p1s_tray_remaining_end_json:
    name: P1S tray remaining end JSON
    max: 255
```

**Format:**
```json
{"1": 850, "3": 420, "4": 1200}
```
Keys = slot numbers (strings), Values = grams (integers)

**⚠️ CRITICAL LIMIT:** 255 character max for input_text. Multi-tray prints with 6 slots could overflow:
```
{"1":1000,"2":1000,"3":1000,"4":1000,"5":1000,"6":1000}  # 60 chars, OK
```
Safe for up to ~10 slots at 4-digit weights.

### When Snapshots are Taken

**Start Snapshot Timing (automations.yaml lines 491-621):**

**Trigger (line 496-499):**
```yaml
- platform: state
  entity_id: sensor.p1s_01p00c5a3101668_print_status
  to:
    - running
    - printing
```

**Sequence:**
1. **Clear both JSONs** (lines 508-517): `{}` → ensures clean state
2. **Wait for active tray** (lines 519-528): Timeout 2 minutes, continue_on_timeout=true
3. **Seed start JSON** with active trays only (lines 538-556)
4. **Fallback** (lines 558-569): If no active tray detected, seed ALL slots with valid remaining

**Answer: BOTH strategies used:**
- Primary: First time tray becomes active during print (line 547: `if is_active_attr or is_active_by_name`)
- Fallback: All slots with valid remaining if no active detected (lines 564-566)

**Mid-Print Additional Tray Tracking (automations.yaml lines 623-687):**
- Automation: `p1s_remaining_snapshot_on_tray_first_active`
- Trigger: `attribute: active` changes to `true` (line 635-636)
- Condition: `print_status in ['running', 'printing']` (line 639)
- **WRITE-ONCE per slot** (line 660: `{{ eff >= 0 and not_present }}`)
- Merges into start JSON (line 664)

**End Snapshot Timing (automations.yaml lines 689-806):**

**Trigger (lines 693-706):**
```yaml
- platform: state
  entity_id: sensor.p1s_01p00c5a3101668_print_status
  from: running
  to:
    - finish
    - idle
    - failed
# PLUS second trigger (no "from" constraint)
- platform: state
  entity_id: sensor.p1s_01p00c5a3101668_print_status
  to:
    - finish
    - finished
    - completed
    - complete
```

**Sequence:**
1. Load start JSON keys (line 713)
2. **For ONLY those slots in start JSON**, read current remaining (lines 738-740)
3. Store as end JSON (line 750)
4. Calculate deltas and decrement (lines 762-777)

**Answer: End snapshot uses SAME sources as start** (fuel gauge → AMS slot remaining)

### End Snapshot Source Details

**Exact Template (lines 738-741):**
```jinja2
{% set fg = states('sensor.p1s_tray_' ~ slot_int ~ '_fuel_gauge_remaining') | float(-1) %}
{% set ams = states('sensor.ams_slot_' ~ slot_int ~ '_remaining_g') | float(-1) %}
{% set eff = fg if fg > 0 else (ams if ams > 0 else -1) %}
{% set grams = eff | round(0) | int %}
```

**Sources:**
1. `sensor.p1s_tray_N_fuel_gauge_remaining` (fuel gauge from Bambu `tray_weight * remain / 100`)
2. `sensor.ams_slot_N_remaining_g` (Spoolman `remaining_weight` for mapped spool)
3. Precedence: Fuel gauge > Spoolman > -1 (stored as negative int)

**NOT using:**
- Direct Bambu `remain%` (only via fuel gauge calculation)
- AMS tray remaining_filament attribute (same as fuel gauge, just different path)

---

## C) "Fuel Gauge = -1 When Active" Root Cause Handling

### Fallback When Fuel Gauge Returns -1 While Active

**Answer: Fall back to Spoolman remaining (with special zero-storage for active trays)**

**Start Snapshot Logic (lines 548-553):**
```jinja2
{% set fg = states('sensor.p1s_tray_' ~ slot ~ '_fuel_gauge_remaining') | float(-1) %}
{% set ams = states('sensor.ams_slot_' ~ slot ~ '_remaining_g') | float(-1) %}
{% set effective = fg if fg > 0 else (ams if ams > 0 else -1) %}
{% set grams = (effective if effective >= 0 else 0) | round(0) | int %}
{# Include active tray even when effective is -1; store as 0 so finish can still call use_spool_filament #}
{% set ns.result = dict(ns.result, **{slot: grams}) %}
```

**Behavior:**
- Fuel gauge -1 → try Spoolman remaining
- Spoolman also -1 → store as `0` (not skip)
- Rationale (line 552 comment): "printer often reports tray_weight=0/remain=-1 for active tray"
- **Active tray is ALWAYS included in start JSON, even with 0 grams**

**End Snapshot Logic (same precedence, lines 738-741):**
- Fuel gauge > 0 → use it
- Else Spoolman > 0 → use it
- Else → store as `-1` (negative int in end JSON)

**Delta Calculation (line 770):**
```jinja2
used_g: "{{ [0, (start_dict[repeat.item] | int(0)) - (end_dict[repeat.item] | int(0))] | max }}"
```

**Result:**
- Start=0, End=-1 → `0 - (-1) = 1` → clamped to max(0, 1) = 1g used ⚠️
- Start=0, End=0 → `0 - 0 = 0` → skipped (line 773 condition)
- Start=850, End=-1 → `850 - (-1) = 851` ⚠️

**PROBLEM IDENTIFIED:** Negative end values cause overestimation. Should clamp end to 0:
```jinja2
# SHOULD BE:
end_g: "{{ max(0, end_dict[repeat.item] | int(0)) }}"
used_g: "{{ [0, start_g - end_g] | max }}"
```

**NOT IMPLEMENTED:**
- Cache last-known-good fuel gauge per slot
- Time-decay fallback
- Sensor availability tracking

---

## D) Print Status Semantics

### Real Values for `sensor.p1s_01p00c5a3101668_print_status`

**From Code Analysis:**

**Start Triggers (lines 498-499, 342-343, 639):**
- `running`
- `printing`
- (Note: both are accepted, likely due to LAN vs Cloud mode differences)

**Finish Triggers (lines 697-706):**
- `finish`
- `finished`
- `completed`
- `complete`
- `idle` (from running, line 695)
- `failed` (from running, line 699)

**Paused States (mentioned in configuration.yaml line 644):**
- `pause`
- `paused`

**Other States (from configuration.yaml lines 631-651 operator_status template):**
- `offline`
- `prepare`
- `init`
- `slicing`
- `standby`
- `error`

**Full State Machine (inferred):**
```
idle → prepare → [init/slicing] → printing/running → finish/completed
                                       ↓
                                    pause/paused → (resume) → printing
                                       ↓
                                    failed/error
```

**Used in Automations:**
| Automation | Trigger States | Condition States |
|------------|----------------|------------------|
| Air purifier | Any state change | `running/printing/pause/paused` (on), `idle/standby/finish/finished/completed/failed/error` (off) |
| Start snapshot | `running`, `printing` | N/A |
| End snapshot | `finish`, `finished`, `completed`, `complete`, `idle` (from running), `failed` (from running) | N/A |
| Tray tracking | N/A | `running`, `printing` |

### Print Job ID / Start Timestamp Entity

**Available (line 710):**
```yaml
task_name: "{{ states('sensor.p1s_01p00c5a3101668_task_name') }}"
```

**Entity:** `sensor.p1s_01p00c5a3101668_task_name` (display only, used in notification line 783)

**Also Available (configuration.yaml lines 19-26):**
```yaml
input_datetime:
  p1s_print_start_time:
    name: P1S Print Start Time
    has_date: true
    has_time: true
  p1s_print_end_time:
    name: P1S Print End Time
    has_date: true
    has_time: true
```
Updated by automation `p1s_persist_print_times` (lines 227-282)

**NOT AVAILABLE:**
- Unique print job ID
- Print job counter
- Bambu Cloud job reference

**Recommendation for Mutex:**
Use `input_datetime.p1s_print_start_time` as print session identifier:
1. On print start: Store timestamp
2. On decrement: Verify timestamp hasn't changed (if changed, another print started → skip decrement)
3. On manual update: Block if `print_status in ['running', 'printing', 'pause', 'paused']`

---

## E) Spoolman Update Contract

### `spoolman.use_spool_filament` Payload

**Exact Call (lines 774-777):**
```yaml
- service: spoolman.use_spool_filament
  data:
    id: "{{ spool_id }}"
    use_weight: "{{ used_g }}"
```

**Parameters:**
- `id`: Spool ID (integer, from `input_text.ams_slot_N_spool_id | int`)
- `use_weight`: Grams consumed (integer, calculated delta)

**Service Action:** Decrements `spool.remaining_weight` by `use_weight`

**NOT using:**
- `filament_id` (service operates on spool_id, not filament_id)
- `set_weight` (absolute value)
- `add_weight` (different service)

### `spoolman.patch_spool` Payload

**Exact Call (scripts.yaml lines 319-323):**
```yaml
- service: spoolman.patch_spool
  data:
    id: "{{ final_spool_id }}"
    remaining_weight: "{{ remaining | float | round(2) }}"
    location: "AMS1_Slot1"
```

**Parameters:**
- `id`: Spool ID (integer)
- `remaining_weight`: Absolute grams remaining (float, rounded to 2 decimals)
- `location`: Physical location string (optional)

**Service Action:** Sets spool fields to exact values (PATCH operation, not delta)

### After Successful Write-Back, Sensor Refresh

**Answer: Forced refresh via integration reload**

**Call (line 778, line 324, line 972):**
```yaml
- service: script.reload_spoolman_integration
```

**Script Implementation (scripts.yaml lines 12-17):**
```yaml
reload_spoolman_integration:
  alias: Reload Spoolman integration
  sequence:
    - action: homeassistant.reload_config_entry
      data:
        entry_id: "01KGX5YPH9CXR2Y1KZRVSP48V7"
```

**Effect:** Forces immediate re-fetch of all Spoolman entities (should update within 1-2 seconds)

**NOT using:**
- Polling (scan_interval is 3600s for REST sensors)
- `homeassistant.update_entity` (only updates template sensors)
- Manual REST calls to verify

---

## F) Policy Decisions (USER MUST DECIDE)

### Failed Print Policy

**Current Behavior:**
- `failed` state triggers end snapshot (line 699)
- Decrements happen normally
- Rationale: Filament was consumed even if print failed

**Options:**
1. **No decrement on failed/canceled** (safest, requires manual reconciliation)
2. **Decrement partial** (estimate % completion from print progress sensor)
3. **Configurable** (input_boolean to enable/disable decrement on fail)
4. **Manual button** ("Apply Partial Usage" popup on fail)

**Recommendation:** Option 3 (configurable) with Option 1 as default:
```yaml
input_boolean:
  p1s_decrement_on_failed_print:
    name: Decrement filament on failed prints
    initial: false
```

Then add condition to end snapshot automation (line 707):
```yaml
condition:
  - condition: or
    conditions:
      - condition: template
        value_template: "{{ trigger_state not in ['failed', 'error'] }}"
      - condition: state
        entity_id: input_boolean.p1s_decrement_on_failed_print
        state: 'on'
```

### Manual Update During Print Policy

**Current Behavior:**
- No prevention
- User can run `ams_slot_N_assign_and_update` anytime
- Overwrites Spoolman `remaining_weight` with absolute value
- End snapshot will read this new value → incorrect delta

**Options:**
1. **Block** (error message, don't run script)
2. **Queue** (run after print finishes)
3. **Allow but mark "needs reconcile"** (run, set flag, skip automatic decrement)

**Recommendation:** Option 1 (block) with notification:

Add to each `ams_slot_N_assign_and_update` script (after line 283):
```yaml
- condition: template
  value_template: "{{ states('sensor.p1s_01p00c5a3101668_print_status') not in ['running', 'printing', 'pause', 'paused'] }}"
- choose:
    - conditions:
        - condition: template
          value_template: "{{ states('sensor.p1s_01p00c5a3101668_print_status') in ['running', 'printing', 'pause', 'paused'] }}"
      sequence:
        - service: notify.persistent_notification
          data:
            title: "Cannot Update During Print"
            message: "Slot {{ slot }} weight update blocked. Wait for print to finish or cancel to preserve accurate tracking."
        - stop: "Print in progress, manual update blocked"
```

### Mid-Print Spool Swap Policy

**Current Behavior:**
- Start snapshot is write-once per slot (line 660)
- `input_text.ams_slot_N_spool_id` is not re-read during print
- If user physically swaps spool mid-print, decrement goes to original spool

**Options:**
1. **Freeze mapping at print start** (simplest, document behavior)
2. **Detect swaps and mark "needs reconcile"** (watch `ams_slot_N_spool_id` changes during print)
3. **Support swaps with split accounting** (complex, track per-spool-per-slot timeslices)

**Recommendation:** Option 2 (detect and reconcile):

Add automation:
```yaml
- id: p1s_detect_spool_swap_during_print
  alias: P1S – detect spool swap during print
  trigger:
    - platform: state
      entity_id:
        - input_text.ams_slot_1_spool_id
        - input_text.ams_slot_2_spool_id
        - input_text.ams_slot_3_spool_id
        - input_text.ams_slot_4_spool_id
        - input_text.ams_slot_5_spool_id
        - input_text.ams_slot_6_spool_id
  condition:
    - condition: template
      value_template: "{{ states('sensor.p1s_01p00c5a3101668_print_status') in ['running', 'printing', 'pause', 'paused'] }}"
  action:
    - service: input_boolean.turn_on
      target:
        entity_id: input_boolean.p1s_needs_reconcile
    - service: notify.persistent_notification
      data:
        title: "Spool Swap During Print Detected"
        message: "Slot mapping changed during print. Automatic filament tracking disabled for this print. Manual reconciliation required."
```

Then modify end snapshot to skip decrement if `input_boolean.p1s_needs_reconcile` is on.

---

## G) "Needs Reconcile" UX

### Where "Needs Reconcile" Should Surface

**Recommendations (implement multiple):**

**1. Input Boolean per Print (recommended):**
```yaml
input_boolean:
  p1s_needs_reconcile:
    name: P1S Print Needs Reconcile
    initial: off
```

**Usage:**
- Set ON: When swap detected, manual update during print, end snapshot fails, etc.
- Set OFF: After user manually reconciles or new print starts
- Check in end snapshot condition (line 707)

**2. Dashboard Badge per Slot (visual):**

Template sensor (configuration.yaml):
```yaml
- sensor:
    - name: "AMS Slot 1 Status"
      unique_id: ams_slot_1_status
      state: >
        {% if states('input_boolean.p1s_needs_reconcile') == 'on' %}
          Needs Reconcile
        {% elif states('sensor.ams_slot_1_remaining_g') | float < 50 %}
          Critical Low
        {% elif states('sensor.ams_slot_1_remaining_g') | float < 100 %}
          Low
        {% else %}
          OK
        {% endif %}
      icon: >
        {% if states('input_boolean.p1s_needs_reconcile') == 'on' %}
          mdi:alert-circle
        {% else %}
          mdi:check-circle
        {% endif %}
```

Dashboard card (dashboard.stage.yaml):
```yaml
- type: entity
  entity: sensor.ams_slot_1_status
  name: Slot 1 Status
```

**3. Persistent Notification (immediate):**
- Already implemented in recommendations above
- Use for critical events (swap during print, failed snapshot)

**4. Spoolman "note" Field Update (audit trail):**

Add to end snapshot on failure (after line 731):
```yaml
- service: spoolman.patch_spool
  data:
    id: "{{ states('input_text.ams_slot_' ~ slot ~ '_spool_id') | int }}"
    comment: "HA: Tracking failed at {{ now().isoformat() }} - needs reconcile"
```

**NOT RECOMMENDED:**
- HA helper per slot (6 helpers just for reconcile flag is excessive)
- Spoolman tag field (not well-supported by integration)

---

## Summary: Critical Implementation Details

### Exact Entities and Storage

| Purpose | Type | Entity ID | Format | Line Ref |
|---------|------|-----------|--------|----------|
| Slot→Spool mapping | input_text | `input_text.ams_slot_N_spool_id` | String, e.g. "42" | config.yaml:54-71 |
| Start snapshot | input_text | `input_text.p1s_tray_remaining_start_json` | JSON, e.g. `{"1":850,"3":420}` | config.yaml:84-86 |
| End snapshot | input_text | `input_text.p1s_tray_remaining_end_json` | JSON | config.yaml:87-89 |
| Fuel gauge | template sensor | `sensor.p1s_tray_N_fuel_gauge_remaining` | Float (grams) | config.yaml:856-902 |
| Spoolman remaining | template sensor | `sensor.ams_slot_N_remaining_g` | Float (grams) | config.yaml:671-709 |
| Print status | Bambu sensor | `sensor.p1s_01p00c5a3101668_print_status` | String | N/A |
| Task name | Bambu sensor | `sensor.p1s_01p00c5a3101668_task_name` | String | auto.yaml:710 |
| Print start time | input_datetime | `input_datetime.p1s_print_start_time` | ISO timestamp | config.yaml:19-23 |

### Snapshot Timing (Exact)

| Event | Automation | Trigger | Storage | Line Ref |
|-------|------------|---------|---------|----------|
| Print start | `p1s_remaining_snapshot_init` | `print_status → running/printing` | Clear both JSONs, seed start | auto.yaml:491-621 |
| Tray first active | `p1s_remaining_snapshot_on_tray_first_active` | `tray.active → true` during print | Merge into start (write-once) | auto.yaml:623-687 |
| Print finish | `p1s_remaining_snapshot_on_finish` | `print_status → finish/finished/completed/complete/idle/failed` | Write end JSON, calculate deltas | auto.yaml:689-806 |

### Data Flow (Exact)

```
START:
  sensor.p1s_tray_N_fuel_gauge_remaining → float(-1)
  sensor.ams_slot_N_remaining_g → float(-1)
  effective = (fuel_gauge if >0 else (ams if >0 else -1))
  grams = max(0, effective) if effective >= 0 else 0
  → input_text.p1s_tray_remaining_start_json[slot] = grams

END:
  (same sources)
  → input_text.p1s_tray_remaining_end_json[slot] = grams

DECREMENT:
  spool_id = input_text.ams_slot_N_spool_id | int
  start_g = start_json[slot] | int
  end_g = end_json[slot] | int
  used_g = max(0, start_g - end_g)
  if spool_id > 0 and used_g > 0:
    spoolman.use_spool_filament(id=spool_id, use_weight=used_g)
```

### Known Bugs to Fix

1. **Negative end values not clamped** (line 741, 769-770)
   - Should: `end_g = max(0, end_dict[slot] | int(0))`

2. **Failed prints always decrement** (line 699)
   - Should: Add configurable boolean

3. **Manual update during print not blocked** (scripts.yaml:281-324)
   - Should: Add condition checking print_status

4. **Mid-print spool swap not detected**
   - Should: Add automation watching `ams_slot_N_spool_id` changes

5. **No reconcile flag** (multiple locations)
   - Should: Add `input_boolean.p1s_needs_reconcile`

---

*Generated: 2025-02-13*
*For simulator implementation and test mode design*
