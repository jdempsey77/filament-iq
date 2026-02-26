# Troubleshooting: UUID helper not set after gen_uuid

## Symptom

- `python_script` domain is loaded and service calls return HTTP 200.
- After calling `python_script.gen_uuid` (e.g. with `target: input_text.spoolman_new_spool_uuid`), the helper stays empty.
- Gates/preflight may report success while the helper remains empty.

## Root cause

In Home Assistant's python_script sandbox, `hass.services.call()` **requires the 4th argument (blocking)**. If it is omitted, the call errors at runtime; the HTTP 200 is still returned, so callers see success while the helper is never updated.

## Fix

In `python_scripts/gen_uuid.py`, ensure the call uses the full signature:

```python
hass.services.call(
    "input_text",
    "set_value",
    {"entity_id": target, "value": new_uuid},
    False   # blocking
)
```

## Operational note

After deploying `python_scripts/` to HA, the **Python Scripts integration must be reloaded** for changes to take effect:

- **Developer Tools → YAML → Python Scripts → Reload**

If "Python Scripts" reload is not available in your HA version, restart Home Assistant.

## Validation commands

1. Call the service directly:
   ```bash
   curl -X POST -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"target":"input_text.spoolman_new_spool_uuid"}' \
     "$HOME_ASSISTANT_URL/api/services/python_script/gen_uuid"
   ```

2. Read the helper state (e.g. poll or GET):
   ```bash
   curl -s -H "Authorization: Bearer $HOME_ASSISTANT_TOKEN" \
     "$HOME_ASSISTANT_URL/api/states/input_text.spoolman_new_spool_uuid" | jq .
   ```

3. Run preflight and gate:
   ```bash
   ./scripts/preflight_spoolman_uuid_present.sh
   ./scripts/gate_spoolman_uuid_e2e.sh
   ```
