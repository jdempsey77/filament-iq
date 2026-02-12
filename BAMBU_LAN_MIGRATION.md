# Bambu P1S → LAN Mode migration (Home Assistant runbook)

## Goals
- Move printer connectivity from Cloud to LAN mode
- Preserve existing HA dashboard/automations behavior
- Validate control paths (pause/resume/cancel/light) and telemetry paths (status/progress/time/temps)
- Keep remote access via Home Assistant (Nabu Casa / VPN) as the primary mobile experience
- Avoid firmware rollback unless a specific required control is blocked

## Non-goals
- Rebuilding the dashboard from scratch
- Adding new cameras
- Changing unrelated HA automations

## Preconditions
- HA remote access already works (Nabu Casa or VPN)
- Access to printer front panel to enable LAN Mode
- Bambu Studio available on a machine on the same LAN

## Current known entities
- Print status: sensor.p1s_01p00c5a3101668_print_status
- Air purifier: fan.office_air_purifier
- Printer smart plug: (entity id in HA, to be filled)

## Phase 0 — Baseline
### Snapshot
- Export screenshots of HA dashboard page(s) for printer
- Capture list of printer entities and their current states
- Record current print_status values observed during: idle, printing, paused, finished (if available)

### Verification (baseline)
- Telemetry updates: progress, remaining time, temps update during an active print
- Control: chamber light toggle works
- Remote: HA mobile can load the dashboard and cameras

## Phase 1 — Harden automations (LAN-proof)
### Change
- Replace separate air-purifier on/off automations with a single choose-based automation
- Trigger on any print_status state change and map multiple state vocabularies

### Verification
- Simulate by temporarily setting an input_text (if used) OR verify via real print transitions
- Ensure logbook logging occurs for unknown states

## Phase 2 — Enable LAN Mode
### Steps (manual on printer)
- On printer: enable LAN Mode (document the exact menu path when you do it)
- Do NOT change firmware in the same session

### HA adjustments
- **Re-add Bambu integration for LAN mode**: After enabling LAN on printer, remove the existing Bambu integration (Settings → Devices & Services), then add it again and select "LAN mode" / local connection. Entity IDs will remain the same.
- **Start print via LAN**: Not supported. Bambu does not expose a LAN API for starting new prints. Start prints from Bambu Studio; use HA for monitoring and control (pause/resume/cancel/light) once a job is running.
- **Operator status sensor**: `sensor.p1s_operator_status` synthesizes print_status, errors, and stage into one clean signal (printing_normally, failed_requires_intervention, etc.) for automations and UI.

## Fallback to Cloud
See **BAMBU_CLOUD_FALLBACK.md** for steps to switch back to Bambu Cloud. With cloud mode, dashboard hides Pause/Resume/Speed controls (only shown when mqtt_connection_mode is local).
