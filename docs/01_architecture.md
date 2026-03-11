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

AppDaemon (all three lifecycle phases):
- **Identity** — deterministic reconciliation, RFID + non-RFID matching, auto-enrollment
- **Usage tracking** — print consumption via RFID delta + 3MF match, job dedup. Consumption uses a 3-tier gcode_state model (see below).
- **Weight sync** — Spoolman remaining weight updates, smart empty guard
- Color sync on bind, filament weight tracking, Spoolman dropdown sync
- Replaced 7 HA automations — all lifecycle logic now runs in AppDaemon

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

## Print Consumption — 3-Tier State Model

The print usage sync (`ams_print_usage_sync`) gates consumption logic on `gcode_state`, using an allowlist rather than a blocklist. The Bambu `gcode_state` is a closed set of 10 values defined in pybambu (`const.py`): `failed`, `finish`, `idle`, `init`, `offline`, `pause`, `prepare`, `running`, `slicing`, `unknown`.

| Tier | States | Consumption behavior |
|------|--------|----------------------|
| **Tier 1 — Failed/Error** | `failed`, `error` | Skip entirely — no consumption recorded |
| **Tier 2 — Non-success terminal** | All states except `finish`, `failed`, `error` | 3MF suppressed; RFID delta only (Path A) |
| **Tier 3 — Success** | `finish` | Full Path A (RFID delta) + Path B (3MF plate-to-slot match) |

Key constants:
- `_SUCCESS_STATES = frozenset({"finish"})` — only state that enables 3MF fetch
- `_FAILED_STATES = frozenset({"failed", "error"})` — skip consumption entirely
- `_TERMINAL_STATES` removed (was dead code)

This design prevents overcounting from cancelled, paused, or otherwise non-success prints. Previous versions used a blocklist (`_FAILED_STATES` only), which allowed 3MF consumption on any state not explicitly blocked — including phantom values that never appear from the ha-bambulab integration.

---

## AppDaemon data artifacts
- **seen_job_keys.json** — persisted under `appdaemon/apps/data/` by `ams_print_usage_sync`. Used for job_key dedup so the same print is not applied twice to Spoolman. Path is relative to the app file so it works under `/config/appdaemon/apps` or addon config paths.

---

## Dashboard
The main dashboard is **storage-type** (UI-managed in Home Assistant). It is **not** deployed by script; updates are done by **manual copy/paste** of YAML from repo (e.g. `dashboards/dashboard.prod.yaml`) into HA dashboard raw configuration. Stage dashboard (`dashboards/dashboard.stage.yaml`) is deployed to `/lovelace-stage` via `./scripts/manage_ha.sh --stage` (restarts HA) or `./scripts/manage_ha.sh --stage-no-restart` (no restart; refresh browser). LIGHT_DEPLOY supports dashboard-only changes without restart.
