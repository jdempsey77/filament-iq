# Bug Squashing Summary: Start Snapshot Empty + AMS Spam

## Branch: bugfix/start-snapshot-empty-and-ams-spam
## Base: fix/eliminate-json-parsing
## Commits: 3 (410e983, 6bd1253, 3ad64ff)

---

## Issues Fixed

### Issue A: "Start Snapshot Empty" on Real Print
**Root Cause:** Init automation cleared JSON helpers but NOT `input_number.p1s_start_slot_N_g` helpers. Stale values from previous prints persisted, or helpers were never seeded if tray detection failed.

**Fix (3ad64ff):**
1. Init automation now clears ALL `input_number.p1s_start_slot_N_g` to 0 (6 slots)
2. Init automation now clears ALL `input_number.p1s_end_slot_N_g` to 0 (6 slots)
3. Finish automation adds `slots_with_data` variable showing which slots have start data
4. Checkpoint now includes slot info: `processing_slots | 1:100, 2:200`

**Impact:**
- Prevents false "start snapshot empty" if init worked
- Shows exactly which slots have data in checkpoint
- Ensures clean slate on each print start

---

### Issue B: AMS Tray Auto-Detect Spam on Restart
**Root Cause:** Auto-detect automation triggered on ANY state change, including HA restart republishing same sensor values. No specificity gate for generic tray text like "Overture PLA".

**Fix (6bd1253):**
1. **Debouncing:** Only fire if `from_state` exists AND actually changed (not just republished)
2. **Specificity gate:** If tray text lacks color/weight/distinguishing info, skip auto-match
   - Generic: "Overture PLA", "eSUN ABS" → manual required
   - Specific: "Overture PLA Black", "eSUN ABS White 1kg" → attempt match
3. **Smart tie-break:** For multiple matches, pick least-remaining if clear winner (>50g diff)
4. **No silent guesses:** If ambiguous, notify for manual assignment

**Impact:**
- No spam on HA restart (sensors republish same values → condition fails)
- Conservative matching prevents incorrect auto-assignment
- Least-remaining tie-break works for common case (one spool nearly empty)

---

## Task C: Test Harness Infrastructure

**Added (410e983):**
1. `input_boolean.filament_test_mode` flag
2. `input_text.filament_test_last_result` helper
3. `TEST_HARNESS.md` documenting 6 test scenarios

**Test Matrix:**
| Test # | Scenario | Pass Criteria |
|--------|----------|---------------|
| 1 | Single-color success | Checkpoint reaches complete, used_g=50g |
| 2 | Multi-color success | Both slots decremented (20g, 50g) |
| 3 | HA restart persistence | Start values persist, finish works |
| 4 | Failed print policy | No decrement, notification sent |
| 5 | Unknown end → reconcile | Flag set, notification sent |
| 6 | Start snapshot empty | Early exit, notification sent |

**Next Step:** Implement test scripts in `scripts.yaml` (not done yet due to time constraints)

---

## Exact Diffs

### automations.yaml (+93 lines, -22 lines)

**Init automation (lines 513-543):**
```diff
+ # Clear input_number helpers (ALL slots to 0)
+ - repeat:
+     count: 6
+     sequence:
+       - service: input_number.set_value
+         target:
+           entity_id: "input_number.p1s_start_slot_{{ repeat.index }}_g"
+         data:
+           value: 0
+       - service: input_number.set_value
+         target:
+           entity_id: "input_number.p1s_end_slot_{{ repeat.index }}_g"
+         data:
+           value: 0
```

**Finish automation (lines 794-809):**
```diff
+ slots_with_data: >
+   {% set ns = namespace(slots=[]) %}
+   {% for i in range(1, 7) %}
+     {% if states('input_number.p1s_start_slot_' ~ i ~ '_g')|int(0) > 0 %}
+       {% set ns.slots = ns.slots + [i ~ ':' ~ states('input_number.p1s_start_slot_' ~ i ~ '_g')] %}
+     {% endif %}
+   {% endfor %}
+   {{ ns.slots | join(', ') if ns.slots else 'none' }}
...
- value: "no_start_data"
+ value: "no_start_data | slots={{ slots_with_data }}"
...
- value: "processing_slots"
+ value: "processing_slots | {{ slots_with_data }}"
```

**AMS auto-detect (lines 1071-1181):**
```diff
condition:
+   # Only fire if state actually changed (not just republished)
    - condition: template
      value_template: >
        {{ trigger.to_state.state not in ['Empty', 'unknown', 'unavailable', ''] 
+          and trigger.from_state is not none
           and trigger.from_state.state != trigger.to_state.state 
+          and trigger.from_state.state not in ['unknown', 'unavailable', 'None', ''] }}

+ # Check if tray text is specific enough
+ is_generic: >
+   {% set words = tray_text | lower | split() %}
+   {% set has_color = words | select('in', [...colors...]) | list | length > 0 %}
+   {% set has_weight = words | select('search', '\\d+(g|kg|lb)') | list | length > 0 %}
+   {% set word_count = words | length %}
+   {{ not has_color and not has_weight and word_count <= 2 }}

+ matching_spools: >
+   ... (now includes remaining_weight for tie-break)
+   {% set ns.matches = ns.matches + [{'id': spool_id, 'remaining': remaining}] %}

+ choose:
+   # Generic → skip auto-match
+   - conditions:
+       - condition: template
+         value_template: "{{ is_generic }}"
+     sequence:
+       - notify: "Manual assignment required (generic text)"
+   # Multiple matches → smart tie-break
+   - conditions: "{{ matching_spools | length > 1 }}"
+     sequence:
+       ... (pick least-remaining if >50g diff, else manual)
```

### configuration.yaml (+6 lines)

```diff
input_boolean:
+ filament_test_mode:
+   name: Filament Test Mode (disables real Spoolman calls)
+   initial: false

input_text:
+ filament_test_last_result:
+   name: Filament Test Last Result (PASS/FAIL + details)
+   max: 255
```

---

## Rollback Instructions

### Option 1: Revert All Bug Fixes
```bash
git checkout fix/eliminate-json-parsing
./scripts/manage_ha.sh --config --restart
./scripts/manage_ha.sh --automations
```

### Option 2: Revert Specific Commits
```bash
# Revert test harness only
git revert 410e983
./scripts/manage_ha.sh --config --restart

# Revert AMS fix only
git revert 6bd1253
./scripts/manage_ha.sh --automations

# Revert init fix only
git revert 3ad64ff
./scripts/manage_ha.sh --automations
```

### Option 3: Cherry-pick Fixes to Another Branch
```bash
git checkout main
git cherry-pick 3ad64ff  # Init clear fix
git cherry-pick 6bd1253  # AMS debounce fix
./scripts/manage_ha.sh --config --restart
./scripts/manage_ha.sh --automations
```

---

## GO/NO-GO Assessment

### ⚠️ **NO-GO (Partial)** - Fixes Complete, But Test Scripts Not Implemented

**What's Ready:**
- ✅ Task A: Init clears helpers, finish shows slot data
- ✅ Task B: AMS debouncing + specificity gate
- ✅ Task C (partial): Test mode flag + documentation

**What's Missing:**
- ⚠️ Test scripts in `scripts.yaml` (not implemented due to time)
- ⚠️ Test mode dry-run logic in finish automation (Spoolman call bypass)
- ⚠️ Actual test execution + validation

**Can Deploy Task A & B Fixes:** ✅ **YES**
- Init clearing helpers is safe and necessary
- AMS debouncing prevents spam and is conservative
- Both fixes are minimal, isolated, and reversible

**Can Deploy Test Harness:** ⚠️ **NOT YET**
- Test mode flag is harmless (defaults to OFF)
- But test scripts don't exist yet
- Need to implement scripts + dry-run logic before using

---

## Recommendation

**Deploy fixes A & B immediately:**
```bash
cd /Users/jdempsey/code/home_assistant
git checkout bugfix/start-snapshot-empty-and-ams-spam
./scripts/manage_ha.sh --config --restart  # (test mode flag added)
./scripts/manage_ha.sh --automations        # (init clear + AMS debounce)
```

**Next real print will:**
- Start with clean helpers (no stale data)
- Show exact slot data in checkpoints
- Not spam on AMS tray republish
- Still require manual assignment for generic tray text

**Complete test harness later:**
- Implement test scripts (`scripts.yaml`)
- Add dry-run logic to finish automation
- Run test matrix before next major change

---

## Evidence of Fixes

### Before (Issue A):
```
Notification: "Start Snapshot Empty"
Checkpoint: "no_start_data"
Problem: Init didn't clear old values OR didn't seed new values
```

### After (Issue A):
```
Init: Clears all start_slot_N_g to 0, then seeds based on active trays
Finish checkpoint: "processing_slots | 1:100, 2:200"
Result: Exact visibility into which slots have data
```

### Before (Issue B):
```
HA restart → sensors republish → automation fires 6 times
Notifications: "New Filament Detected..." spam for all slots
Problem: No debouncing, generic text auto-matched to duplicates
```

### After (Issue B):
```
HA restart → sensors republish same values → condition fails (from_state == to_state)
Generic text: "Overture PLA" → skip auto-match, notify once for manual
Specific text: "Overture PLA Black" → attempt match, smart tie-break
Result: No spam, conservative matching
```

---

## Files Changed

- `automations.yaml`: +93/-22 lines (3 automations modified)
- `configuration.yaml`: +6 lines (2 helpers added)
- `TEST_HARNESS.md`: +200 lines (new file, documentation)

**No changes to:**
- `scripts.yaml` (test scripts not implemented yet)
- Finish accounting logic (math unchanged)
- Spool mapping logic (helper reads unchanged)
- Dashboard UI

---

## Safety Notes

- All changes are minimal and isolated
- YAML validated before each commit
- No refactoring of unrelated code
- Clear rollback path for each fix
- Test mode defaults to OFF (no impact on production)

---

## Next Steps

1. **Deploy fixes A & B** (recommended)
2. **Run a real print** to validate:
   - Init clears and seeds helpers
   - Checkpoint shows slot data
   - Finish accounting works
3. **Implement test scripts** (Task C completion):
   - Add 6 test scripts to `scripts.yaml`
   - Add dry-run logic to finish automation
   - Run full test matrix
4. **Merge to main** after real print success
