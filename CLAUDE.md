# Home Assistant Configuration Repo

## Project Structure

This repo contains Home Assistant configuration (automations, scripts, dashboards, helpers) and the **Filament IQ** AppDaemon package for Bambu Lab printer + Spoolman integration.

## Source of Truth

**`appdaemon/apps/filament_iq/`** is the single source of truth for all AppDaemon code. Never edit root-level or deployed `.py` files directly. All changes go into `filament_iq/` first, then get deployed.

### Key Package Files

| File | Purpose |
|------|---------|
| `appdaemon/apps/filament_iq/base.py` | Base class, slot/tray mappings |
| `appdaemon/apps/filament_iq/ams_rfid_reconcile.py` | RFID + non-RFID spool reconciliation |
| `appdaemon/apps/filament_iq/ams_print_usage_sync.py` | Print usage tracking, 3MF fetch orchestration |
| `appdaemon/apps/filament_iq/threemf_parser.py` | FTPS listing/download, 3MF parsing, filename matching |
| `appdaemon/apps/filament_iq/ams_rfid_guard.py` | RFID guard automation |
| `appdaemon/apps/filament_iq/filament_weight_tracker.py` | Filament weight tracking |
| `appdaemon/apps/filament_iq/spoolman_dropdown_sync.py` | Spoolman dropdown sync for dashboard |

### HA Configuration Files

| File | Purpose |
|------|---------|
| `automations.yaml` | All HA automations (print finish, startup, air purifier, etc.) |
| `scripts.yaml` | HA scripts (slot assign, reconcile, Spoolman reload) |
| `configuration.yaml` | Core config, input_text/input_boolean helpers |
| `helpers_manifest.yaml` | Required helpers registry for validation |
| `secrets.yaml` | Secrets (printer access code, camera URLs) |

## Deployment

- **HA config**: `./scripts/manage_ha.sh --all` (deploys config + automations, restarts HA)
- **AppDaemon**: `./scripts/manage_ha.sh --appdaemon` (deploys `filament_iq/` to HA, restarts addon)
- **Full deploy with tests**: `./scripts/skill_deploy.sh`
- **Deploy target**: `root@192.168.4.124:/addon_configs/a0d7b954_appdaemon/apps/`

## Separate Release Repo

The `filament_iq/` package is also published as a standalone repo at `~/code/filament-iq` (`github.com/jdempsey77/filament-iq`). When making changes here, sync to that repo and tag a release.

## Testing

- Tests live in `tests/` (scoped via `pyproject.toml`)
- Run: `python3 -m pytest -q`
- The `filament_iq/` directory is gitignored — use `git add -f` when committing AppDaemon files
- Diagnostic: `tools/test_3mf_pipeline.py` validates the 3MF fetch pipeline end-to-end

## Printer Entity Prefix

All Bambu Lab P1S entities use prefix: `p1s_01p00c5a3101668`

## Slot-to-AMS Mapping

- Slots 1-4: AMS Pro (`ams_1_tray_1` through `ams_1_tray_4`)
- Slot 5: AMS HT (`ams_128_tray_1`)
- Slot 6: AMS HT (`ams_129_tray_1`)
