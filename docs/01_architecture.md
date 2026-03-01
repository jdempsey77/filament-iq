# System Architecture

## Layered Model

+----------------------+
|   Bambu P1S Printer  |
+----------------------+
           |
           v
+----------------------+
| AMS Units            |
| - ams_1 (slots 1-4)  |
| - ams_128 (slot 5)   |
| - ams_129 (slot 6)   |
+----------------------+
           |
           v
+----------------------+
| Home Assistant       |
| - Sensors            |
| - Helpers            |
| - Automations        |
+----------------------+
           |
           v
+----------------------+
| AppDaemon            |
| - RFID reconciliation|
| - Sticky tray logic  |
| - State machine      |
+----------------------+
           |
           v
+----------------------+
| Spoolman             |
| REST API             |
| /api/v1/openapi.json |
+----------------------+

---

## Separation of Responsibility

Home Assistant:
- Entity storage
- Helpers
- UI
- Service calls

AppDaemon:
- Deterministic reconciliation logic
- Identity decisions
- State transitions

Spoolman:
- Canonical spool inventory
- Filament metadata
- Weight tracking
- RFID UID association

Scripts:
- Deploy gates
- Preflights
- Evidence capture

---

## AppDaemon data artifacts
- **seen_job_keys.json** — persisted under `appdaemon/apps/data/` by `ams_print_usage_sync`. Used for job_key dedup so the same print is not applied twice to Spoolman. Path is relative to the app file so it works under `/config/appdaemon/apps` or addon config paths.

---

## Dashboard
The main dashboard is **storage-type** (UI-managed in Home Assistant). It is **not** deployed by script; updates are done by **manual copy/paste** of YAML from repo (e.g. `dashboards/dashboard.test.storage.yaml`) into HA dashboard raw configuration. Stage dashboard (`dashboards/dashboard.stage.yaml`) is deployed to `/lovelace-stage` via `./scripts/manage_ha.sh --stage` when that file changes.
