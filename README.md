# FilamentIQ

**Filament lifecycle tracking and Spoolman integration for Bambu Lab printers with AMS.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![Last commit](https://img.shields.io/github/last-commit/jdempsey/filament-iq.svg)](https://github.com/jdempsey/filament-iq)

## What it does

FilamentIQ is an AppDaemon-based integration that connects your Bambu Lab printer's AMS (Automatic Material System) to [Spoolman](https://github.com/Donkie/Spoolman), an open-source filament inventory manager. It tracks filament consumption, reconciles RFID tags and non-RFID trays to Spoolman spools, and writes usage data after each print.

The integration provides AMS slot management: each physical slot (1–6 for AMS Pro + HT) is mapped to a Spoolman spool via RFID tag matching or metadata fingerprinting for non-RFID filaments. A reconciliation engine keeps slot bindings in sync, and an RFID guard auditor enforces policy. Filament consumption is recorded to Spoolman after each print, with optional 3MF parsing for per-slot usage.

## Requirements

- **Home Assistant** (2024.1 or later)
- **AppDaemon** add-on
- **Spoolman** (running and reachable)
- **Bambu Lab printer** with AMS (e.g. P1S, X1)
- **ha-bambulab** integration for printer sensors

## Installation

### Manual install

1. Clone or download this repository.
2. Copy the `appdaemon/apps/filament_iq/` directory to your AppDaemon apps folder:
   - Standard: `config/appdaemon/apps/filament_iq/`
   - Add-on: `/addon_configs/a0d7b954_appdaemon/apps/filament_iq/`
3. Copy `appdaemon/apps/filament_iq/apps.yaml.example` to your AppDaemon `apps.yaml` (or merge its contents).
4. Edit `apps.yaml` and replace placeholders (`YOUR_SPOOLMAN_IP`, `YOUR_PRINTER_IP`, printer serial, etc.).
5. Restart the AppDaemon add-on.
6. Create the required Home Assistant helpers (input_boolean, input_text, input_button, etc.) — see `ha-config/packages/filament_iq.yaml` or your configuration for the full list.

## Configuration

Reference `appdaemon/apps/filament_iq/apps.yaml.example` for the complete configuration. Required keys per app:

| App | Required keys |
|-----|---------------|
| `ams_rfid_reconcile` | `spoolman_url`, `printer_serial` |
| `ams_rfid_guard` | `spoolman_url` |
| `spoolman_dropdown_sync` | `spoolman_url` |
| `ams_print_usage_sync` | `spoolman_url`, `printer_serial` |
| `filament_weight_tracker` | `spoolman_url`, `printer_serial` |

Optional keys include `printer_model`, `ams_units`, `access_code_entity`, `dropdown_entity`, and entity overrides for buttons, booleans, and sensors.

## Dashboard

A Lovelace dashboard is included in `dashboards/` for AMS slot status, Spoolman integration, and print tracking. Deploy via `manage_ha.sh --stage` or copy the YAML to your dashboard configuration.

## Contributing

Contributions are welcome. Please open an issue first to discuss changes, then submit a pull request.

## License

MIT License — see [LICENSE](LICENSE) for details.
