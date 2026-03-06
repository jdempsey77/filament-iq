# Configuration

## Prerequisites

- Home Assistant OS or Supervised
- [ha-bambulab](https://github.com/alandtse/ha-bambulab) installed via HACS
- AppDaemon addon installed and running
- Spoolman running and accessible from HA (default port 7912)
- Bambu Lab printer with at least one AMS unit
- `curl` available on the HA host (for 3MF FTPS fetch)

## Installation

### 1. Install via HACS

HACS → three-dot menu → Custom repositories → URL: `https://github.com/jdempsey77/filament-iq`, Category: **AppDaemon**

### 2. HA Package

Add to `configuration.yaml`:

```yaml
homeassistant:
  packages: !include_dir_named packages/
```

Copy `ha-config/packages/filament_iq.yaml` to your HA config `packages/` directory.

Replace placeholders:
- `YOUR_SPOOLMAN_IP` → your Spoolman server IP (e.g. `192.168.1.250`)
- `YOUR_PRINTER_SERIAL` → your Bambu printer device serial (e.g. `01p00a1b2c3d4e5f`)

Restart Home Assistant.

### 3. Configure apps.yaml

Copy `appdaemon/apps/filament_iq/apps.yaml.example` to your AppDaemon `apps.yaml` and adjust values. See the example file for all available keys with defaults.

### 4. Printer Serial in Source

The `TRAY_ENTITY_BY_SLOT` and `ACTIVE_TRAY_ENTITY` constants in `ams_print_usage_sync.py` and `ams_rfid_reconcile.py` contain `YOUR_PRINTER_SERIAL` placeholders. Replace these with your printer's serial.

Entity ID pattern:
- AMS1 trays: `sensor.p1s_{SERIAL}_ams_1_tray_{1-4}`
- AMS HT slots: `sensor.p1s_{SERIAL}_ams_128_tray_1`, `sensor.p1s_{SERIAL}_ams_129_tray_1`
- Active tray: `sensor.p1s_{SERIAL}_active_tray`
- Print status: `sensor.p1s_{SERIAL}_print_status`

## Configuration Reference

### ams_print_usage_sync

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `enabled` | No | `true` | Master enable/disable |
| `spoolman_base_url` | Yes | — | Spoolman API base URL (e.g. `http://192.168.1.250:7912`) |
| `dry_run` | No | `false` | Log writes without calling Spoolman |
| `min_consumption_g` | No | `2` | Skip Spoolman writes below this threshold (grams) |
| `max_consumption_g` | No | `300` | Reject writes above this threshold (grams) |
| `printer_ip` | Yes | — | Bambu printer LAN IP address |
| `printer_ftps_port` | No | `990` | FTPS port for 3MF fetch |
| `access_code_entity` | No | `input_text.bambu_printer_access_code` | HA entity holding printer access code |
| `printer_access_code` | No | — | Hard-coded access code (overrides entity if set) |
| `threemf_enabled` | No | `true` | Enable 3MF parsing (Tier 1 allocation) |

### ams_rfid_reconcile

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `enabled` | No | `true` | Master enable/disable |
| `spoolman_url` | Yes | — | Spoolman API base URL |
| `startup_delay_seconds` | No | `60` | Delay before first reconcile after AppDaemon start |
| `startup_wait_helpers_seconds` | No | `420` | Max seconds to wait for HA helpers to be ready |
| `startup_wait_retry_initial_seconds` | No | `2` | Initial retry interval for startup readiness probe |
| `startup_wait_retry_max_seconds` | No | `30` | Max retry interval (exponential backoff cap) |
| `startup_probe_helper_entity` | No | `input_text.ams_slot_1_spool_id` | Entity probed to determine HA readiness |
| `debounce_seconds` | No | `3` | Debounce interval for tray/helper changes |
| `safety_poll_seconds` | No | `600` | Periodic reconcile interval (status_only) |
| `debug_logs` | No | `false` | Enable extra debug logging |
| `strict_mode_reregister` | No | `false` | Require explicit spool ID when multiple candidates match |
| `color_distance_threshold` | No | `90` | Euclidean RGB distance threshold for "close" color match |
| `evidence_log_path` | No | `/config/ams_rfid_reconcile_evidence.log` | Path for evidence/audit log |

### ams_rfid_guard

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `enabled` | No | `true` | Master enable/disable |
| `spoolman_base_url` | Yes | — | Spoolman API base URL |
| `scan_interval_seconds` | No | `300` | Interval between scans (seconds) |
| `dry_run` | No | `false` | Log violations without quarantining |
| `notify_cooldown_minutes` | No | `360` | Min minutes between duplicate notifications |
| `cache_sensor_entity` | No | `sensor.spoolman_spools_cache` | Optional cache trigger entity |
| `use_cache_trigger` | No | `false` | Trigger scan on cache entity change |
| `rfid_managed_patterns` | No | `["bambu", "bambu lab"]` | Regex patterns matching RFID-managed filament names |
| `missing_ha_spool_uuid_mode` | No | `warn_only` | `warn_only` or `quarantine` for missing identity |

### filament_weight_tracker

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `spoolman_url` | Yes | — | Spoolman API base URL |
| `report_path` | No | `/config/filament_weight_reports.log` | Path for weight delta reports |

### spoolman_dropdown_sync

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `enabled` | No | `true` | Master enable/disable |
| `spoolman_base_url` | Yes | — | Spoolman API base URL |

## Slot Numbering

| Slot | AMS Unit | Entity Suffix | Canonical Location |
|------|----------|---------------|--------------------|
| 1 | AMS1 Tray 1 | `ams_1_tray_1` | `AMS1_Slot1` |
| 2 | AMS1 Tray 2 | `ams_1_tray_2` | `AMS1_Slot2` |
| 3 | AMS1 Tray 3 | `ams_1_tray_3` | `AMS1_Slot3` |
| 4 | AMS1 Tray 4 | `ams_1_tray_4` | `AMS1_Slot4` |
| 5 | AMS Lite / HT 1 | `ams_128_tray_1` | `AMS128_Slot1` |
| 6 | AMS Lite / HT 2 | `ams_129_tray_1` | `AMS129_Slot1` |

## Spoolman Location Values

| Location | Meaning |
|----------|---------|
| `AMS1_Slot1` ... `AMS129_Slot1` | Spool currently in that AMS slot |
| `Shelf` | Not in AMS (stored elsewhere) |
| `New` | Newly created, not yet placed |
| `Empty` | Depleted (remaining_weight ≤ 0), excluded from matching |
| `QUARANTINE` | Identity policy violation (set by RFID Guard) |
