# P1S Finish Automation: Debugging Guide

## Problem History

The finish automation (`p1s_remaining_snapshot_on_finish`) was not running when prints completed. Root cause: **templates in top-level `variables:` block were silently crashing before any actions could execute**.

## Solution

**Move all trigger-dependent templates and JSON parsing OUT of top-level variables and INTO action-level variables blocks (after the first checkpoint).**

### Critical Rules

1. **First action MUST be an unconditional checkpoint write**
   - Uses `service: input_text.set_value` (not `action:`)
   - Writes a fixed value (no templates)
   - Proves the automation reached the action block

2. **Top-level variables restrictions**
   - ❌ NO `trigger.from_state` or `trigger.to_state` (can be `None` and crash)
   - ❌ NO `from_json` (can fail on malformed input and crash)
   - ❌ NO complex templates that reference entities that might not exist
   - ✅ OK: Simple `states()` calls, basic trigger checks, static lists

3. **Safe JSON parsing pattern**
   ```yaml
   - variables:
       start_dict: >
         {% set s = raw_start | trim %}
         {% if s.startswith('{') and s.endswith('}') %}
           {{ s | from_json }}
         {% else %}
           {{ {} }}
         {% endif %}
   ```

4. **Debug without printing**
   - Use `script.p1s_debug_force_finish_path` to seed start/end JSON and trigger the automation
   - Check `input_text.p1s_finish_automation_checkpoint` to see how far the automation progressed
   - Check automation trace in HA UI for step-by-step execution

## Debugging Workflow

1. **Ensure debug mode is on**
   ```yaml
   input_boolean.filament_debug_mode: on
   ```

2. **Reset trigger to off** (if stuck)
   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" \
     http://192.168.4.124:8123/api/services/input_boolean/turn_off \
     -d '{"entity_id": "input_boolean.p1s_debug_finish_trigger"}'
   ```

3. **Run debug script**
   ```yaml
   action: script.p1s_debug_force_finish_path
   ```

4. **Check checkpoint**
   ```yaml
   # Developer Tools → States
   input_text.p1s_finish_automation_checkpoint
   ```

   Expected progression:
   - `ENTERED_FIRST__BUILD_999` → automation started
   - `after_start_dict | keys=1,2` → JSON parsed successfully
   - `after_end_dict | has_unknown=False` → end values computed
   - `before_spoolman | slot=1 | spool_id=X | used_g=Y` → about to decrement
   - `reload_done` → Spoolman reloaded
   - `complete` → finished successfully

5. **Check automation trace**
   ```
   Settings → Automations → P1S – snapshot remaining on print finish → Trace
   ```
   - Recent timestamp confirms it ran
   - Step-by-step view shows which conditions/actions executed
   - Red/yellow icons indicate failures

## Common Issues

### Checkpoint stays "unknown"
- Automation never reached actions → check top-level variables for crashing templates

### Checkpoint shows old value
- Automation didn't trigger → check trigger boolean state, ensure it transitioned off→on

### Checkpoint stops at "after_start_dict"
- JSON parsing failed or subsequent variables crashed → check `raw_start` value

### No Spoolman decrement
- Check `ams_slot_X_spool_id` helpers have valid IDs > 0
- Check `used_g > 0` (if start and end are same, nothing to decrement)
- Check automation reached `before_spoolman` checkpoint

## File Locations

- Automation: `automations.yaml` (id: `p1s_remaining_snapshot_on_finish`)
- Debug script: `scripts.yaml` (`script.p1s_debug_force_finish_path`)
- Helpers: `configuration.yaml` (input_text, input_boolean)
- Deploy: `./scripts/manage_ha.sh --automations`

## Testing Without Prints

The debug script allows full end-to-end testing without starting a print:

1. Seeds `input_text.p1s_tray_remaining_start_json` with `{"1": 100, "2": 200}`
2. Seeds `input_text.p1s_tray_remaining_end_json` with `{"1": 90, "2": 150}`
3. Toggles `input_boolean.p1s_debug_finish_trigger` off → delay → on
4. Automation runs with seeded values instead of reading sensors
5. Spoolman decrement only runs if `ams_slot_1_spool_id` and `ams_slot_2_spool_id` are set
