# Bambu P1S — Fallback to Cloud Mode

## Rationale
LAN mode with firmware 01.09.01.00 + MQTT encryption did not enable pause/resume/speed controls. Cloud mode restores Bambu app access and may simplify connectivity. Telemetry and monitoring remain the primary HA use case.

## What stays the same (no YAML changes)
- **configuration.yaml** — `sensor.p1s_operator_status` template uses print_status, online, print_error, hms_errors. Same entities regardless of connection mode.
- **automations.yaml** — `printer_air_purifier_lan` and `p1s_operator_alert` use the same entities. Both work with cloud.
- **Entity IDs** — Integration uses device serial; entity IDs (`sensor.p1s_01p00c5a3101668_*`, etc.) should persist when re-adding via cloud. If they change after re-add, update references.

## Manual steps (printer + HA)

### 1. On the printer
- Disable **Developer LAN Mode** (Settings → Network). Printer will reconnect to Bambu Cloud.

### 2. In Home Assistant
- **Settings → Devices & Services** → Bambu Lab
- Remove the existing printer integration (or delete the device)
- Add Bambu Lab integration again → choose **Cloud** setup (log in with Bambu account)
- Confirm the printer appears and entities populate

### 3. Verify entity IDs
- **Developer Tools → States** — filter by `p1s` or `01p00c5a3101668`
- If entity IDs changed (e.g. different serial slug), update:
  - `configuration.yaml` (template sensor)
  - `automations.yaml`
  - `dashboards/dashboard.stage.yaml`

## Optional dashboard adjustments

### Option A: Leave controls as-is
- Pause, Resume, Cancel, Speed cards remain. With cloud + 01.09, they may show unavailable. No code changes.

### Option B: Hide controls when using cloud
- Add conditionals so Pause/Resume/Cancel and Speed only render when `sensor.p1s_01p00c5a3101668_mqtt_connection_mode` is `local`. Cleaner UI when controls are known to be non-functional.

## Expected behavior (cloud mode, 01.09.x)
| Feature                | Expected                         |
|------------------------|----------------------------------|
| Telemetry              | ✅ Progress, temps, status, time |
| Chamber light          | ✅ (often the only working control) |
| Pause / Resume / Stop  | ❌ Blocked by firmware           |
| Speed select           | ❌ Blocked by firmware           |
| Bambu Studio / App     | ✅ Full control via Bambu tools  |
| HA remote (Nabu Casa)  | ✅ Monitoring only               |

## Rollback
- Re-enable LAN mode on printer, remove integration, re-add via LAN/IP. Entity IDs should match; no YAML rollback needed if IDs stayed the same.
