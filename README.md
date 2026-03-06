# FilamentIQ

**Filament lifecycle tracking and Spoolman integration for Bambu Lab printers with AMS.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![Last commit](https://img.shields.io/github/last-commit/jdempsey77/home-assistant-config.svg)](https://github.com/jdempsey77/home-assistant-config)

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
6. Create the required Home Assistant helpers — see `ha-config/packages/filament_iq.yaml` and the [Configuration guide](docs/configuration.md).

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | How FilamentIQ works: reconciliation loop, Spoolman integration, AMS slot management, RFID vs non-RFID, operator status |
| [Configuration](docs/configuration.md) | Complete reference for every key in `apps.yaml.example` |
| [Dashboard](docs/dashboard.md) | How to install and use the Lovelace dashboard |
| [Troubleshooting](docs/troubleshooting.md) | Common issues and solutions |

## Quick links

- **Configuration reference:** [docs/configuration.md](docs/configuration.md)
- **Dashboard setup:** [docs/dashboard.md](docs/dashboard.md)
- **Troubleshooting:** [docs/troubleshooting.md](docs/troubleshooting.md)

## Contributing

Contributions are welcome. Please open an issue first to discuss changes, then submit a pull request.

## License

MIT License — see [LICENSE](LICENSE) for details.
