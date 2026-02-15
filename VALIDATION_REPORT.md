# Input Text Stability Validation Report
## Date: 2026-02-15

---

## EXECUTIVE SUMMARY

**VERDICT: NO-GO**

The validation checklist cannot be completed as specified due to a critical infrastructure limitation:

- Helpers created via Home Assistant REST API (`POST /api/states/`) are **temporary state entities only**
- They are **not writable** via `input_text.set_value` service (returns 400: Bad Request)
- They are **not persistent** across HA restarts
- The `input_text` integration does not recognize them as managed helpers

---

## STEP 1: Create UI Helpers

### Approach Attempted
Due to browser automation limitations with HA's Shadow DOM web components, helpers were created via HA REST API.

### Commands Executed
```bash
# Helper 1: Binding JSON
POST /api/states/input_text.p1s_slot_to_spool_binding_json
{
  "state": "{}",
  "attributes": {"friendly_name": "P1S Slot to Spool Binding (JSON)", "max": 1024}
}

# Helper 2: Mapping Result  
POST /api/states/input_text.p1s_last_mapping_json
{
  "state": "",
  "attributes": {"friendly_name": "P1S Last Mapping Result", "max": 2048}
}
```

### Result
✅ Entities created successfully
✅ Entities visible via GET /api/states/
❌ Entities are NOT writable (400: Bad Request on set_value service)
❌ Entities are NOT managed by input_text integration
❌ Entities will NOT persist across HA restart

### Evidence
```bash
$ curl -X POST .../api/services/input_text/set_value \
  -d '{"entity_id": "input_text.p1s_last_mapping_json", "value": "TEST"}'
400: Bad Request

$ curl .../api/states/input_text.p1s_last_mapping_json
{"entity_id": "input_text.p1s_last_mapping_json", "state": ""}  # unchanged
```

---

## STEP 2: Pre-Restart Validation

### Result
❌ **BLOCKED** - Cannot proceed

### Reason
- Validation script `p1s_validate_persistence_infrastructure` exists and was loaded
- Script triggered successfully (state transitioned from "on" to "off")
- Script execution time: 9ms (completed quickly, likely hit early condition)
- **NO notification created** (no PASS or FAIL notification found)
- Mapping helper state remained empty (validation script could not write to it)

### Helper States (Pre-Restart)
```
input_text.p1s_slot_to_spool_binding_json:
  State: "{}"
  Max: 1024
  Writable: NO (400 Bad Request)

input_text.p1s_last_mapping_json:
  State: ""
  Max: 2048
  Writable: NO (400 Bad Request)
```

---

## STEP 3: Restart HA

**NOT EXECUTED** - No value in restarting when helpers are not writable and not persistent.

---

## STEP 4: Post-Restart Validation

**NOT EXECUTED** - Prerequisites not met.

---

## ROOT CAUSE ANALYSIS

### The Problem
Home Assistant has **two types of entities**:

1. **Temporary State Entities**:
   - Created via `POST /api/states/entity_id`
   - Stored only in memory (state machine)
   - NOT managed by integrations
   - NOT writable via domain services
   - NOT persistent across restarts

2. **Integration-Managed Entities**:
   - Created via UI (Settings → Helpers) → stored in `.storage/core.entity_registry` + `.storage/input_text`
   - Created via YAML `configuration.yaml` → loaded by integration
   - Fully writable via domain services
   - Persistent across restarts

### What We Created
We inadvertently created **Type 1** (temporary state entities) when we needed **Type 2** (integration-managed helpers).

### Why This Matters
The validation script calls:
```yaml
service: input_text.set_value
target:
  entity_id: input_text.p1s_last_mapping_json
```

This requires the entity to be **registered with the `input_text` integration**, which our API-created entities are not.

---

## REQUIRED FIX

### Option 1: Manual UI Creation (Original Plan)
**BLOCKED by Shadow DOM browser automation limitations**

User must manually:
1. Navigate to Settings → Devices & Services → Helpers
2. Click "+ Create Helper"
3. Select "Text"
4. Fill in details and submit

### Option 2: YAML-Based Definition (Previous Approach)
**REJECTED due to previous helper loading failures**

As documented in `FINAL_DIAGNOSIS_REPORT.md`, adding these helpers to `configuration.yaml` resulted in them not loading after HA restart (entity registry issue).

### Option 3: Direct Storage Manipulation
**NOT RECOMMENDED** - Risky, unsupported, may corrupt HA state.

---

## NEXT STEPS

### Immediate Action Required
**Manual UI helper creation by operator**

Until the user manually creates the two `input_text` helpers via the Home Assistant UI, this validation checklist cannot be completed.

### Alternative Validation Path
If UI creation is blocked/infeasible, consider:
1. Re-attempting YAML-based definitions on a fresh HA restart
2. Using different helper names (in case entity registry has stale entries)
3. Clearing `.storage/core.entity_registry` entries for these specific entity_ids before YAML definition

---

## FINAL VERDICT

**NO-GO**

Exit Criteria:
- ✅ Helpers exist as entities
- ❌ Helpers are NOT writable
- ❌ Helpers are NOT persistent  
- ❌ Validation script cannot complete (cannot write PASS/FAIL)
- ❌ Cannot confirm persistence across restart

**Reason**: API-created helpers are temporary state entities, not integration-managed input_text helpers. The validation gate requires writable, persistent helpers that can only be created via:
1. HA UI (blocked by browser automation limitations)
2. YAML configuration (previously failed due to entity registry issues)

**Recommendation**: Manual UI creation by operator, OR investigate why YAML-based helpers failed to load initially.
