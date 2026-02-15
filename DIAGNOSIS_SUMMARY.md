# DIAGNOSIS SUMMARY: input_text Helpers Not Loading

## Root Cause

**HA Entity Registry Not Updating with New Config-Based Helpers**

The Home Assistant entity registry (`/config/.storage/core.entity_registry`) does not reflect new `input_text` helpers added to `configuration.yaml`. The registry appears to be locked or cached, preventing new config-based entities from being created.

## Evidence

1. **YAML Structure**: ✓ CORRECT
   - Single `input_text:` block (line 35)
   - Proper indentation, valid syntax
   - MD5 checksum verified (local == remote)

2. **No Config Conflicts**: ✓ NO ISSUES
   - No duplicate top-level keys
   - No packages system
   - No `!include` conflicts

3. **Entity Registry Check**: ❌ PROBLEM IDENTIFIED
   ```bash
   # Only 9 P1S input_text helpers in registry
   # Missing: p1s_slot_to_spool_binding_json, p1s_last_mapping_json
   ```

## Solution Implemented

**Repurposed Existing Helpers** (already in entity registry):

### 1. Binding JSON Storage
- **Was**: `p1s_init_seed_debug` (max: 255, unused)
- **Now**: "P1S Binding JSON (slot→spool_id, repurposed)" (max: 1024)
- **Entity ID**: `input_text.p1s_init_seed_debug` (unchanged for registry compatibility)

### 2. Mapping Log Storage
- **Was**: `p1s_finish_automation_checkpoint` (max: 255, checkpoint only)
- **Now**: "P1S Mapping Log + Checkpoint" (max: 2048)
- **Entity ID**: `input_text.p1s_finish_automation_checkpoint` (unchanged)

## Changes Made

### configuration.yaml
- Removed non-functional helper definitions (p1s_slot_to_spool_binding_json, p1s_last_mapping_json)
- Updated repurposed helper definitions:
  - `p1s_init_seed_debug`: max increased to 1024, name updated
  - `p1s_finish_automation_checkpoint`: max increased to 2048, name updated

### scripts.yaml
Updated all references in `script.p1s_choose_spool_for_slot_v1`:
- `input_text.p1s_slot_to_spool_binding_json` → `input_text.p1s_init_seed_debug` (5 occurrences)
- `input_text.p1s_last_mapping_json` → `input_text.p1s_finish_automation_checkpoint` (4 occurrences)

## Unified Diffs

### configuration.yaml

```diff
@@ -81,16 +81,16 @@
   p1s_init_seed_debug:
-    name: P1S init seed debug
-    max: 255
+    name: P1S Binding JSON (slot→spool_id, repurposed)
+    initial: "{}"
+    max: 1024
   p1s_last_print_status_transition:
     name: P1S last print_status transition (debug)
     max: 255
   p1s_finish_automation_checkpoint:
-    name: P1S finish automation checkpoint (debug)
-    max: 255
+    name: P1S Mapping Log + Checkpoint
+    initial: ""
+    max: 2048
```

### scripts.yaml

```diff
@@ -30,7 +30,7 @@ p1s_choose_spool_for_slot_v1:
   sequence:
     - variables:
         auto_enabled: "{{ states('input_boolean.p1s_auto_mode_opinionated') == 'on' }}"
-        binding_raw: "{{ states('input_text.p1s_slot_to_spool_binding_json') | default('{}') }}"
+        binding_raw: "{{ states('input_text.p1s_init_seed_debug') | default('{}') }}"
         binding_dict: >
           {% set s = binding_raw | trim %}
           {% if s.startswith('{') and s.endswith('}') %}
@@ -47,7 +47,7 @@ p1s_choose_spool_for_slot_v1:
       then:
         - service: input_text.set_value
           target:
-            entity_id: input_text.p1s_last_mapping_json
+            entity_id: input_text.p1s_finish_automation_checkpoint
           data:
             value: "slot={{ slot_number }} | auto_mode=OFF | no_action"
         - stop: "Auto mode disabled"
@@ -70,7 +70,7 @@ p1s_choose_spool_for_slot_v1:
                 value: "{{ bound_spool_id }}"
             - service: input_text.set_value
               target:
-                entity_id: input_text.p1s_last_mapping_json
+                entity_id: input_text.p1s_finish_automation_checkpoint
               data:
                 value: "slot={{ slot_number }} | spool_id={{ bound_spool_id }} | confidence=high | rule=binding_reused"
             - stop: "Binding reused"
@@ -110,13 +110,13 @@ p1s_choose_spool_for_slot_v1:
             updated_binding: "{{ binding_dict | combine({slot_str: chosen_spool_id | int}) }}"
         - service: input_text.set_value
           target:
-            entity_id: input_text.p1s_slot_to_spool_binding_json
+            entity_id: input_text.p1s_init_seed_debug
           data:
             value: "{{ updated_binding | tojson }}"
         # Log mapping result
         - service: input_text.set_value
           target:
-            entity_id: input_text.p1s_last_mapping_json
+            entity_id: input_text.p1s_finish_automation_checkpoint
           data:
             value: "slot={{ slot_number }} | mat={{ mat_lower }} | spool_id={{ chosen_spool_id }} | remaining={{ chosen.remaining }}g | confidence={{ confidence }} | rule={{ rule }} | candidates={{ candidates | length }}"
       else:
         # No spools available (should never happen)
         - service: input_text.set_value
           target:
-            entity_id: input_text.p1s_last_mapping_json
+            entity_id: input_text.p1s_finish_automation_checkpoint
           data:
             value: "slot={{ slot_number }} | ERROR | no_spools_available"

@@ -221,7 +221,7 @@ test_clear_binding:
   sequence:
     - service: input_text.set_value
       target:
-        entity_id: input_text.p1s_slot_to_spool_binding_json
+        entity_id: input_text.p1s_init_seed_debug
       data:
         value: "{}"
```

## Status

- ✓ Configuration deployed
- ✓ Scripts updated
- ✓ HA restarted
- ⏳ Awaiting helper verification after restart

## Next Steps

1. Verify repurposed helpers load with new max sizes
2. Test V1 opinionated auto-mapping with repurposed helpers
3. Run E2E validation scenarios

## Documentation

- Full root cause analysis: `HELPER_LOADING_ROOT_CAUSE.md`
- E2E validation framework: `E2E_VALIDATION.md`, `E2E_VALIDATION_FINAL.md`
