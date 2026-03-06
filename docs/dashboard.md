# FilamentIQ Dashboard

The FilamentIQ Lovelace dashboard provides AMS slot status, Spoolman integration, print tracking, and operator status. This guide explains how to install and use it.

## Prerequisites

- FilamentIQ AppDaemon apps installed and running
- Home Assistant helpers and template sensors configured (see [Configuration](configuration.md))
- Spoolman integration providing `sensor.spoolman_spool_*` entities

## Dashboard Files

| File | Purpose |
|------|---------|
| `dashboards/dashboard.stage.yaml` | Stage dashboard — deploy to `/lovelace-stage` for testing |
| `dashboards/dashboard.prod.yaml` | Production dashboard — copy to main dashboard when ready |
| `dashboards/dashboard.test.storage.yaml` | Test dashboard — FilamentIQ/Spool management view only, for copy/paste into a storage-mode dashboard |

## Installation

### Option 1: Deploy stage dashboard (recommended for testing)

If you use the `manage_ha.sh` script:

```bash
./scripts/manage_ha.sh --stage
```

This deploys `dashboard.stage.yaml` to `/lovelace-stage`. Open `https://your-ha-url/lovelace-stage` to view. Use `--stage-no-restart` to deploy without restarting Home Assistant.

### Option 2: Manual copy to storage dashboard

1. **Settings** → **Dashboards** → **Add dashboard**
2. Create a new dashboard (e.g. "FilamentIQ" or "3D Printer")
3. Open the dashboard → **⋮** → **Edit dashboard** → **⋮** → **Raw configuration**
4. Copy the contents of `dashboards/dashboard.prod.yaml` (or the FilamentIQ-specific sections from `dashboard.test.storage.yaml`) and paste
5. **Save** (✓)

### Option 3: Add FilamentIQ cards to existing dashboard

Add individual cards to any dashboard:

- **Operator status** — `sensor.filament_iq_operator_status` (or your configured entity)
- **AMS slot cards** — One per slot: `sensor.ams_slot_N_name`, `sensor.ams_slot_N_remaining_g`, `sensor.ams_slot_N_status`, `input_text.ams_slot_N_unbound_reason`
- **Reconcile button** — `input_button.filament_iq_reconcile_now`
- **Print times** — `input_datetime.filament_iq_print_start_time`, `input_datetime.filament_iq_print_end_time`

## Dashboard Features

- **AMS Filament Slots** — Per-slot view: spool name, material, vendor, remaining (g), status, unbound reason
- **Operator status** — Printer state (idle, printing, finished, failed, etc.)
- **Print tracking** — Start/end times, active tray during print
- **Manual reconcile** — Button to trigger slot reconciliation
- **Slot assignment** — Assign Spoolman spools to slots when `NEEDS_MANUAL_BIND`

## Entity ID Customization

If your printer uses a different prefix (e.g. `x1_01p00c5a3101668`), update:

1. **Template sensors** in `configuration.yaml` — ensure `sensor.ams_slot_N_*` and `sensor.filament_iq_operator_status` exist
2. **Dashboard YAML** — replace entity IDs to match your setup
3. **apps.yaml** — `printer_model` and `printer_serial` control AppDaemon entity prefixes

## Custom Cards

The dashboard may use custom Lovelace cards (e.g. `mushroom-entity-card`, `digital-clock`). Install them via HACS or the frontend if cards fail to load.
