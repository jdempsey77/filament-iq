# P1S FINISH AUTOMATION - JSON-FREE SOLUTION

## Status: IMPLEMENTATION PAUSED - CONTEXT LIMIT

### ✅ Completed
1. **New branch:** `fix/eliminate-json-parsing` from `chore/repo-hygiene`
2. **Helpers added:** `input_number.p1s_start_slot_N_g` and `input_number.p1s_end_slot_N_g` (N=1-6)
3. **Commit:** `33f3478` - Configuration changes validated and committed

### 🔄 Required Next Steps

#### 1. Update Debug Script (`scripts.yaml`)

Replace `p1s_debug_force_finish_path` to set input_numbers instead of JSON:

```yaml
p1s_debug_force_finish_path:
  alias: P1S Debug – force finish path (no print)
  sequence:
    # Set start values
    - service: input_number.set_value
      target:
        entity_id: input_number.p1s_start_slot_1_g
      data:
        value: 100
    - service: input_number.set_value
      target:
        entity_id: input_number.p1s_start_slot_2_g
      data:
        value: 200
    # Set end values
    - service: input_number.set_value
      target:
        entity_id: input_number.p1s_end_slot_1_g
      data:
        value: 90
    - service: input_number.set_value
      target:
        entity_id: input_number.p1s_end_slot_2_g
      data:
        value: 150
    # Trigger finish automation
    - service: input_boolean.turn_off
      target:
        entity_id: input_boolean.p1s_debug_finish_trigger
    - delay: "00:00:01"
    - service: input_boolean.turn_on
      target:
        entity_id: input_boolean.p1s_debug_finish_trigger
```

#### 2. Update Finish Automation (`automations.yaml`)

Replace `p1s_remaining_snapshot_on_finish` to use input_numbers:

**Key changes:**
- Remove all `variables:` blocks with `from_json`
- Use `states('input_number.p1s_start_slot_N_g') | int` directly in templates
- Compute `used_g` inline: `(start | int) - (end | int) | max(0)`
- Loop over slots 1-6, skip if start_g == 0 (unused slot)
- Keep all checkpoints for validation

**Template example:**
```yaml
- repeat:
    count: 6
    sequence:
      - variables:
          slot_num: "{{ repeat.index }}"
          start_g: "{{ states('input_number.p1s_start_slot_' ~ repeat.index ~ '_g') | int }}"
          end_g: "{{ states('input_number.p1s_end_slot_' ~ repeat.index ~ '_g') | int }}"
          used_g: "{{ [0, start_g - end_g] | max }}"
          spool_id: "{{ states('input_text.ams_slot_' ~ repeat.index ~ '_spool_id') | int(0) }}"
      - condition: template
        value_template: "{{ start_g > 0 and used_g > 0 and spool_id > 0 }}"
      - service: spoolman.use_spool_filament
        data:
          id: "{{ spool_id }}"
          use_weight: "{{ used_g }}"
```

#### 3. Update Init Automation

The init automation must write to input_numbers instead of JSON:

```yaml
- service: input_number.set_value
  target:
    entity_id: input_number.p1s_start_slot_{{ slot }}_g
  data:
    value: "{{ grams }}"
```

### Rollback

```bash
cd /Users/jdempsey/code/home_assistant
git checkout chore/repo-hygiene
./scripts/manage_ha.sh --config
./scripts/manage_ha.sh --automations
./scripts/manage_ha.sh --scripts
```

### Testing Protocol

1. Deploy configuration (adds input_numbers)
2. Deploy scripts (new debug script)
3. Deploy automations (JSON-free finish)
4. Run: `script.p1s_debug_force_finish_path`
5. Verify:
   - `input_text.p1s_finish_automation_checkpoint` = `"complete"`
   - Spoolman weights decreased by 10g (slot 1) and 50g (slot 2)
   - Notification shows usage summary

### Success Criteria

- ✅ No `from_json` in any automation
- ✅ No JSON parsing in templates
- ✅ Checkpoint reaches `complete`
- ✅ Spoolman service called with correct values
- ✅ YAML validates with `yaml.safe_load`

### Branch Info

**Branch:** `fix/eliminate-json-parsing`  
**Base:** `chore/repo-hygiene` (45a2341)  
**Current commit:** `33f3478`

### Files Modified

- `configuration.yaml` - Added 12 input_number helpers ✅
- `scripts.yaml` - Needs update ⏳
- `automations.yaml` - Needs update ⏳

---

**Note:** Implementation paused due to conversation context limits. All design decisions documented. Solution is straightforward: replace JSON string parsing with native HA input_number entities.
