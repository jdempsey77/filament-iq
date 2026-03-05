# ANALYZE: End-to-End Filament Tracking — Full Pipeline Audit

**Date:** 2026-03-05  
**Scope:** Read-only analysis per `analyze_filament_tracking_e2e.md`  
**CRITICAL:** No files were modified. Analysis and report only.

---

## Executive Summary

The filament tracking pipeline has a **CRITICAL** design flaw: `active_slots` requires each slot to appear in **BOTH** `start_map` and `end_map`. Non-RFID slots never appear in `end_map` because the finish automation only includes slots where `remaining > 0` (fuel gauge). Non-RFID trays have `tray_weight=0` or `remain=-1`, so fuel gauge = 0. As a result:

- **Scenario 2 (single non-RFID):** `active_slots` = ∅ → **ZERO tracking**
- **Scenario 4 (all non-RFID):** `active_slots` = ∅ → **ZERO tracking**
- **Scenario 5 (mixed):** Only RFID slots in `active_slots` → non-RFID slots get **nothing**

3MF matching can produce correct per-slot weights, but those matches are only applied to slots that made it into `rfid_results` or `nonrfid_slots`. Since non-RFID slots are excluded from `active_slots`, they never enter `nonrfid_slots`, so 3MF matches for them are discarded.

Additional issues: start snapshot can seed non-RFID slots with Spoolman `ams_slot_N_remaining_g` (not tray_weight), 3MF unicode filenames may fail curl, and dual event firing (finish + offline) can cause duplicate notifications despite dedup.

---

## Evidence: Key Code Paths

### Fuel Gauge Template (configuration.yaml:1506–1547)

```yaml
state: >
  {{ [0, (state_attr('sensor.p1s_01p00c5a3101668_ams_1_tray_1', 'tray_weight') | float(0) * state_attr('sensor.p1s_01p00c5a3101668_ams_1_tray_1', 'remain') | float(0) / 100)] | max | round(1) }}
```

- **RFID:** `tray_weight` > 0, `remain` 0–100 → positive grams
- **Non-RFID:** `tray_weight` = 0 or `remain` = -1 → **0.0 g**

### Init Automation — Start Snapshot (automations.yaml:535–706)

**Trigger:** `print_status` → `running` or `printing`

**seeded_dict** (active trays only):
```jinja2
{% set fg = states('sensor.p1s_tray_' ~ slot ~ '_fuel_gauge_remaining') | float(-1) %}
{% set ams = states('sensor.ams_slot_' ~ slot ~ '_remaining_g') | float(-1) %}
{% set effective = fg if fg > 0 else (ams if ams > 0 else -1) %}
{% set grams = (effective if effective >= 0 else 0) | round(0) | int %}
```

- Only slots with `active=true` or matching `active_tray` state are included
- Precedence: fuel gauge → Spoolman `ams_slot_N_remaining_g`
- Non-RFID: fg=0 → uses Spoolman remaining if bound

**fallback_dict** (when no tray active): all slots with `effective >= 0` (fg or ams)

### Finish Automation — end_json_built (automations.yaml:839–859)

```jinja2
{% for i in range(6) %}
  {% set slot_str = (i+1) | string %}
  {% set remaining = states(fuel_gauge_sensors[i]) | float(-1) %}
  {% if remaining > 0 and slot_str in start %}
    {% set ns.pairs = ns.pairs + ['"' ~ slot_str ~ '": ' ~ remaining] %}
  {% endif %}
{% endfor %}
```

- **Only slots with `remaining > 0` and in start** are included
- Non-RFID fuel gauge = 0 → **never included** → `end_json` omits non-RFID slots

### AppDaemon active_slots (ams_print_usage_sync.py:191–194)

```python
active_slots = sorted(
    int(k) for k in start_map
    if k.isdigit() and 1 <= int(k) <= 6 and k in end_map
)
```

- **Requires `k in end_map`** — slots only in start are excluded
- Non-RFID slots are never in end_map → **excluded from active_slots**

### 3MF Match Application (ams_print_usage_sync.py:311–322)

3MF matches are applied only to slots in `rfid_results` or `nonrfid_slots`. Those lists are built from `active_slots`. If a slot is not in `active_slots`, it never gets a 3MF match applied.

---

## Scenario Walkthroughs

### Scenario 1: Single slot print — RFID spool (Slot 1, Spool 41, Bambu Green PLA)

| Step | Result |
|------|--------|
| **1. Start Snapshot** | Trigger: print_status→running. Slot 1 active, fg=800. start_json=`{"1": 800}` |
| **2. Tray Tracking** | _seed_active_trays adds 1. trays_used={1} |
| **3. 3MF Fetch** | 5s delay, fetches if task matches. May get exact weights |
| **4. Finish** | end_json from fuel gauge: slot 1 remaining=785. end_json=`{"1": 785}` |
| **5. Event** | job_key, task_name, print_weight_g, trays_used, start_json, end_json |
| **6. active_slots** | {1} (in both) |
| **7. RFID** | has_fuel_gauge=true, consumption=15g, rfid_results=[(1,41,15)] |
| **8. 3MF** | If match: overrides with 3MF value |
| **9. Spoolman** | PUT /api/v1/spool/41/use {"use_weight": 15} |
| **10. Notification** | Slot 1: 800g→785g (used ~15g) |

**Result:** Spoolman write correct. No issues.

---

### Scenario 2: Single slot print — non-RFID spool (Slot 2, Spool 47, Overture Matte PLA)

| Step | Result |
|------|--------|
| **1. Start Snapshot** | Slot 2 active. fg=0 (non-RFID). ams_slot_2_remaining_g=830 (Spoolman). effective=830. start_json=`{"2": 830}` |
| **2. Tray Tracking** | trays_used={2} |
| **3. 3MF Fetch** | May succeed; filaments=[{used_g: 58.5, color, material}] |
| **4. Finish** | Fuel gauge slot 2 = 0. `remaining > 0` false. end_json=`{}` |
| **5. Event** | start_json=`{"2": 830}`, end_json=`{}` |
| **6. active_slots** | **∅** — slot 2 not in end_map |
| **7–9** | Loop over active_slots never runs. rfid_results=[], nonrfid_slots=[] |
| **10** | nonrfid_remaining=[], no allocation, **no Spoolman write** |

**Result:** **ZERO tracking.** 3MF match for slot 2 exists but is never applied because slot 2 never enters `nonrfid_slots`.

**ISSUE:** CRITICAL — non-RFID-only prints get no Spoolman updates.

---

### Scenario 3: Multi-slot print — all RFID (Slots 1 + 3)

| Step | Result |
|------|--------|
| **1. Start** | start_json=`{"1": 800, "3": 500}` |
| **2. Tray** | trays_used={1, 3} |
| **4. Finish** | end_json=`{"1": 785, "3": 480}` |
| **6. active_slots** | {1, 3} |
| **7** | Both RFID. rfid_results=[(1,41,15), (3,52,20)] |
| **9** | Both written to Spoolman |

**Result:** Correct. No issues.

---

### Scenario 4: Multi-slot print — all non-RFID (Slots 2 + 4)

| Step | Result |
|------|--------|
| **1. Start** | start_json=`{"2": 830, "4": 500}` (from Spoolman fallback) |
| **2. Tray** | trays_used={2, 4} |
| **4. Finish** | end_json=`{}` (no slot has fuel gauge > 0) |
| **6. active_slots** | **∅** |
| **7–9** | No slots processed. **No Spoolman writes** |

**Result:** **ZERO tracking.** Same root cause as Scenario 2.

---

### Scenario 5: Multi-slot print — mixed (Slot 1 RFID + Slots 2, 4 non-RFID)

| Step | Result |
|------|--------|
| **1. Start** | start_json=`{"1": 800, "2": 830, "4": 500}` |
| **2. Tray** | trays_used={1, 2, 4} |
| **4. Finish** | end_json=`{"1": 785}` only (slot 1 has fuel gauge) |
| **6. active_slots** | **{1}** — 2 and 4 excluded (not in end_map) |
| **7** | Slot 1: rfid_results=[(1,41,15)]. Slots 2,4 never considered |
| **8** | 3MF may match slot 2 and 4, but threemf_matches applied only to rfid_results or nonrfid_slots. Slot 2,4 not in nonrfid_slots |
| **9** | Only slot 1 written. Slots 2, 4 get **nothing** |

**Result:** RFID slot correct. Non-RFID slots **not tracked** despite trays_used and 3MF data.

---

## Root Cause Hypothesis

1. **active_slots BOTH requirement:** The condition `k in end_map` was intended to avoid slots that disappeared (e.g. tray removed). For non-RFID, `end_map` is always empty for those slots because the finish automation only adds slots with `remaining > 0`. The design assumes all used slots have fuel gauge data at finish, which is false for non-RFID.

2. **end_json builder:** It correctly excludes non-RFID (no fuel gauge). The bug is downstream: AppDaemon treats “not in end_map” as “slot not used” instead of “slot used but no fuel gauge.”

3. **3MF match application:** Matches are only applied to slots already in rfid_results or nonrfid_slots. Those come from active_slots. So 3MF cannot rescue slots excluded by active_slots.

---

## Known Issues (from analysis doc)

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | Non-RFID slots produce empty active_slots | **CRITICAL** | Confirmed — `k in end_map` excludes all non-RFID |
| 2 | Fuel gauge / start snapshot values | **HIGH** | Non-RFID uses Spoolman fallback; "2": 1000 could be Spoolman or stale |
| 3 | 3MF unicode filename failure | **MEDIUM** | `urllib.parse.quote` may not handle emoji; curl can fail |
| 4 | 3MF match patched=0 when active_slots empty | **CRITICAL** | Confirmed — matches discarded |
| 5 | Dual event (finish + offline) | **LOW** | Dedup by job_key should catch; verify |
| 6 | Start snapshot tray_weight vs remain | **MEDIUM** | Doc: 1000 from tray_weight; non-RFID tray_weight=0 so likely Spoolman |

---

## Summary: Issues by Severity

### CRITICAL (blocks tracking entirely)

1. **active_slots requires BOTH start and end:** Non-RFID slots never in end_map → active_slots empty for non-RFID-only or mixed prints → no Spoolman writes for non-RFID.
2. **3MF matches unused when active_slots empty:** 3MF can have correct weights but they are only applied to slots in rfid_results/nonrfid_slots, which are empty when active_slots is empty.

### HIGH (produces wrong results)

1. **Start snapshot for non-RFID:** Uses Spoolman `ams_slot_N_remaining_g` when fuel gauge=0. If Spoolman is stale or wrong, start_json is wrong. Not blocking but degrades accuracy.
2. **Mixed prints:** RFID slots tracked; non-RFID slots ignored. Pool allocation never runs for non-RFID because they never enter nonrfid_slots.

### MEDIUM (degrades accuracy)

1. **3MF unicode/emoji filenames:** curl FTPS download may fail on filenames like "● 5x6 Drawer Set.3mf". No explicit handling.
2. **tray_weight/remain semantics:** Non-RFID: tray_weight=0, remain=-1. Start snapshot fallback to Spoolman can produce values that don’t match physical remaining.

### LOW (cosmetic / logging)

1. **Dual event firing:** finish + offline may fire twice; dedup should prevent double write; worth verifying.
2. **Notification vs actual write:** Notification shows slots from start/end; if active_slots excludes some, notification may not match what was written.

---

## Recommended Fix Priority

1. **Fix active_slots (CRITICAL):** Include slots that are in `start_map` **OR** in `trays_used_set`, not only in both. For slots in start but not in end: treat as non-RFID (end_g=0), and require `slot in trays_used_set` for non-RFID allocation. This restores tracking for Scenarios 2, 4, 5.
2. **Apply 3MF matches to trays_used:** When applying 3MF matches, consider all slots in `trays_used_set` as candidates, not only those in `active_slots`. If 3MF matches slot X and X is in trays_used, write that match even if X was not in active_slots.
3. **3MF unicode filenames:** Add explicit encoding (e.g. UTF-8) for curl or sanitize filenames before FTPS.
4. **Document start snapshot semantics:** Clarify that non-RFID start values come from Spoolman, not tray, and may be approximate.

---

## Suggested Fix (Code Snippets Only)

### Fix 1: active_slots to include trays_used

```python
# Current (broken):
active_slots = sorted(
    int(k) for k in start_map
    if k.isdigit() and 1 <= int(k) <= 6 and k in end_map
)

# Proposed:
slots_in_both = {k for k in start_map if k.isdigit() and 1 <= int(k) <= 6 and k in end_map}
slots_start_only = {k for k in start_map if k.isdigit() and 1 <= int(k) <= 6 and k not in end_map}
# Include start-only slots only if they were actually used (tray tracking)
slots_start_only_used = {k for k in slots_start_only if int(k) in trays_used_set}
active_slots = sorted(int(k) for k in slots_in_both | slots_start_only_used)
```

### Fix 2: 3MF matches for slots not in active_slots

When applying `threemf_matches` to nonrfid_slots, the current code only considers slots that made it into `nonrfid_slots` (from active_slots). Extend logic so that if `threemf_matches` has slot X and X is in `trays_used_set` with valid spool_id, add a write for that slot even if X was not in active_slots. (Requires careful integration with the existing flow.)

---

## Next Action

1. Implement Fix 1 (active_slots) and validate with Scenarios 2, 4, 5.
2. Add/update tests for non-RFID-only and mixed prints.
3. Verify 3MF match application for slots in trays_used but not in end_map.
4. Optionally harden 3MF fetch for unicode filenames.
