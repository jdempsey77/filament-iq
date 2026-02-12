# Spoolman Integration Setup Diagnosis

## Problem

HA logs show: `"Waiting for integrations to complete setup: {('spoolman','01KGX5YPH9CXR2Y1KZRVSP48V7'): ...}"`

**Impact:** While Spoolman integration setup is pending/slow, entities (e.g. `sensor.spoolman_spool_9`) may be missing or unavailable, causing template sensors and the dropdown to fail.

---

## Diagnostic Steps

### 1. Enable debug logging (done)

`configuration.yaml` now has:

```yaml
logger:
  default: info
  logs:
    custom_components.spoolman: debug
    homeassistant.components.template: debug
    homeassistant.helpers.template: debug
```

**Deploy and restart HA**, then collect logs for Spoolman setup issues.

---

### 2. Verify Spoolman base URL

**Settings → Devices & Services → Spoolman** → check the configured URL.

- If it's `http://localhost:8080` but Spoolman is NOT running on the HA host (e.g., it's in a different container or on a different machine), change it to the **real IP/hostname**.
- Example: `http://192.168.1.100:8080` or `http://spoolman.local:8080`

**Test connectivity:**
- From the HA host, run: `curl http://<spoolman-url>/api/v1/info`
- Should return JSON with `{"version": "...", ...}`
- If connection fails or times out → Spoolman is not reachable from HA, or Spoolman isn't running

---

### 3. Avoid config entry reloads during normal usage

The **Reload Spoolman** button (`reload_spoolman_integration` script) calls `homeassistant.reload_config_entry`. This re-triggers integration setup, which can:

- Cause long waits
- Show spinners in UI
- Make entities temporarily unavailable

**For now:**
- Keep the button (useful for manual debugging)
- **Do NOT use it** for dropdown refresh
- Once Spoolman setup is stable, dropdown should populate automatically via automation (no manual reload needed)

---

### 4. Check logs after restart

After deploying the logger config and restarting HA, check **Settings → System → Logs** or `home-assistant.log` for:

**Spoolman setup messages:**
- `custom_components.spoolman` entries
- Connection timeouts, HTTP errors, API failures
- "Setup complete" or "Setup failed" messages

**Common issues:**
- **Timeout:** Spoolman URL is wrong or Spoolman is slow/unreachable
- **HTTP 404/500:** Spoolman API version mismatch or Spoolman is broken
- **Long delay then success:** Spoolman is reachable but slow (large database, network latency)

---

## Expected Outcome

After fixing the URL/connectivity and restarting:

1. **Spoolman integration setup completes quickly** (within a few seconds, no "waiting for integrations" message persists)
2. **Entities appear:** `sensor.spoolman_spool_9`, etc., are available in States
3. **Template sensors become available:**
   - `sensor.ams_spool_list_options` with populated `attributes.options`
   - `sensor.ams_spool_list_options_debug` with `spool_entities_seen`
4. **Dropdown populates automatically** via the existing automation

---

## Next Steps (after Spoolman is healthy)

If entities appear but `attributes.options` is still empty, check:
- **Debug sensor:** `sensor.ams_spool_list_options_debug.spool_entities_seen` — are spool entities listed?
- **Options sensor:** `sensor.ams_spool_list_options.options` — is it a non-empty list?

If `spool_entities_seen` is empty but entities exist in States (search `spoolman`), the template filter might need adjustment (e.g., entity_id pattern or string slicing).

---

## Files Changed

- **`configuration.yaml`** – Added `logger:` section for debug logging
- **`SPOOLMAN_INTEGRATION_DIAGNOSIS.md`** – This diagnostic doc
