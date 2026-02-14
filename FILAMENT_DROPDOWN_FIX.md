# Filament Dropdown Reliability Fixes

## Problem
The "Add spool to Spoolman" filament dropdown was intermittently empty or not loading.

## Root Causes Identified

### 1. **REST Sensor Failure Recovery**
When Spoolman API was temporarily unavailable or slow to respond:
- REST sensors would return `unavailable` or empty values
- The `value_template` had no fallback, so sensors would lose their previous state
- Downstream template sensors would receive empty data and stop working

### 2. **Template Parsing Issues**
The filtering logic in `sensor.ams_filament_list_options` had several fragile assumptions:
- Didn't validate that `' - '` separator existed before splitting
- Created empty lists in wrong order (before checking validity)
- No handling for edge cases (single item, empty string, etc.)

### 3. **Cascade Failure**
When one sensor failed:
1. `sensor.spoolman_filaments_api` becomes unavailable
2. `sensor.ams_filament_list_options` receives empty data
3. `input_select.spoolman_new_spool_filament` gets cleared
4. Dropdown appears empty to user
5. Even after API recovers, dropdown stays empty until manual refresh

## Fixes Applied

### Fix 1: State Preservation in REST Sensors
Added fallback to retain previous state when API fails:

```yaml
value_template: >
  {% if value_json is defined and value_json is iterable ... %}
    # Process API data
  {% else %}
    {{ this.state if this.state is defined else '' }}
  {% endif %}
```

**Impact:** Sensors now keep their last good value instead of becoming empty/unavailable.

### Fix 2: Robust Template Parsing
Improved `sensor.ams_filament_list_options`:

```yaml
{% if ' - ' in part %}  # Validate separator exists first
  {% set fid = part.split(' - ')[0] | trim %}
  {% if fid not in used_list %}
    # Add to filtered list
  {% endif %}
{% endif %}
```

**Impact:** Template handles malformed data gracefully instead of failing.

### Fix 3: Better Empty State Handling
Moved variable declarations inside validity checks:

```yaml
{% if used_fids_raw not in ['unknown', 'unavailable', ''] and used_fids_raw != '' %}
  {% set used_list = ... %}  # Only create list if data is valid
  # Filter logic here
{% else %}
  {{ all_parts | sort }}  # Show all filaments if filter data unavailable
{% endif %}
```

**Impact:** Filtering is optional - if spool data unavailable, show all filaments instead of nothing.

## Testing Performed

1. ✅ Manual trigger of automation - dropdown loads
2. ✅ Restart Home Assistant - dropdown persists
3. ✅ Spoolman service restart - dropdown keeps previous values
4. ✅ Network interruption simulation - sensors retain state
5. ✅ Empty database case - shows all filaments (no filter)

## Prevention Strategy

### Monitoring
Watch these sensors for issues:
- `sensor.spoolman_filaments_api` - Should have `|||` separated list
- `sensor.spoolman_spools_api` - Should have comma-separated IDs
- `sensor.ams_filament_list_options` - Should have non-zero `options` attribute

### Recovery Actions
If dropdown is empty:
1. Check REST sensor states (Developer Tools → States)
2. Click "Refresh filament list" button (forces update)
3. If still empty, check Spoolman API is accessible: `curl http://192.168.4.124:7912/api/v1/filament`

### Future Improvements
Consider adding:
- Health check automation that notifies if sensors stay unavailable > 5min
- Automatic retry logic in refresh script
- Cached fallback data stored in input_text helpers
