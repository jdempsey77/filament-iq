# FilamentIQ

> Slicer-exact filament tracking and visibility for Bambu Lab + Home Assistant + Spoolman

FilamentIQ integrates Bambu Lab P1S printers with [Spoolman](https://github.com/Donkie/Spoolman) via Home Assistant and AppDaemon. It automatically tracks filament consumption per print using a three-tier allocation pipeline, manages spool identity (RFID and non-RFID), and writes usage to Spoolman.

---

## What It Does

### Three-Tier Allocation Pipeline

FilamentIQ allocates consumption to AMS slots using a priority cascade:

**Tier 1: 3MF file parsing** — After each print, the app FTPS into the printer's `/cache/` directory, downloads the 3MF file, and parses `Metadata/slice_info.config` for per-filament `used_g` values. This yields slicer-exact weights (~1% accuracy). No fuel gauge needed. Filaments are matched to physical slots by color + material; close color matches (Euclidean distance < 30) and material-only fallbacks are supported when exactly one slot matches.

**Tier 2: RFID fuel gauge delta** — Fallback for Bambu RFID spools. Uses `start_g - end_g` from tray fuel gauge snapshots. Resolution is coarse (~40g) but deterministic for single-RFID prints.

**Tier 3: Time-weighted active slot estimation** — For non-RFID slots or when 3MF/RFID data is insufficient, consumption is split proportionally by how long each slot was active during the print. Typical error ~10–15%. Naturally captures purge tower waste because active duration includes purge time.

### Spool Identity Management

- **RFID spools** — Matched by `tag_uid` (or `tray_uuid`) from ha-bambulab tray sensors. Identity is stored in Spoolman `lot_nr` as a 32-char hex. Automatic enrollment on first detection.
- **Non-RFID spools** — Matched by color + material fingerprint (`type|filament_id|color_hex`). No manual assignment needed when the fingerprint is unique. Automatic enrollment on first detection.
- **Fail-closed behavior** — Ambiguity (0 or >1 candidates) → slot stays UNBOUND. RFID Guard quarantines spools that violate identity invariants.

---

## Architecture

```
                    ┌──────────────┐
                    │  Bambu P1S   │
                    │  (printer)   │
                    └──┬───┬───┬──┘
                       │   │   │
              FTPS/990 │   │   │ MQTT
           ┌───────────┘   │   └────────────┐
           │               │                │
    ┌──────▼──────┐  ┌─────▼──────┐  ┌──────▼──────────┐
    │  3MF File   │  │  Tray      │  │  Bambu HA       │
    │  (slicer    │  │  Sensors   │  │  Integration    │
    │   weights)  │  │  (color,   │  │  (print status, │
    │             │  │   active)  │  │   task name)    │
    └──────┬──────┘  └─────┬──────┘  └──────┬──────────┘
           │               │                │
           └───────┬───────┘                │
                   │                        │
            ┌──────▼────────────────────────▼──┐
            │           AppDaemon               │
            │  ┌─────────────────────────────┐  │
            │  │  ams_print_usage_sync.py    │  │
            │  │  - Tray tracking            │  │
            │  │  - 3MF fetch + parse        │  │
            │  │  - Color matching           │  │
            │  │  - 3-tier allocation        │  │
            │  │  - Spoolman REST writes     │  │
            │  │  - Notifications            │  │
            │  └─────────────────────────────┘  │
            │  ┌─────────────────────────────┐  │
            │  │  ams_rfid_reconcile.py      │  │
            │  │  - Slot identity mgmt       │  │
            │  │  - RFID tag matching        │  │
            │  │  - Non-RFID fingerprinting  │  │
            │  └─────────────────────────────┘  │
            └──────────────┬────────────────────┘
                           │
                    ┌──────▼──────┐
                    │  Spoolman   │
                    │  (spool DB) │
                    │  :7912      │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ HA Dashboard│
                    └─────────────┘
```

---

## Prerequisites

- Home Assistant OS or Supervised
- [ha-bambulab](https://github.com/alandtse/ha-bambulab) installed via HACS
- AppDaemon addon installed and running
- Spoolman running and accessible from HA (default port 7912)
- Bambu Lab printer with at least one AMS unit (any type or combination)
- `curl` available on the HA host (for 3MF FTPS fetch)

---

## Installation

### 1. Install FilamentIQ via HACS

- HACS → three-dot menu → Custom repositories
- URL: `https://github.com/YOUR_USERNAME/filament-iq`, Category: **AppDaemon**

### 2. HA Configuration (packages drop-in)

Add to `configuration.yaml`:

```yaml
homeassistant:
  packages: !include_dir_named packages/
```

Copy `ha-config/packages/filament_iq.yaml` to your HA config `packages/` directory.

Replace placeholders in the package file:
- `YOUR_SPOOLMAN_IP` → your Spoolman server IP (e.g. `192.168.1.250`)
- `YOUR_PRINTER_SERIAL` → your Bambu P1S device serial (e.g. `01p00a1b2c3d4e5f`)

Restart Home Assistant.

### 3. Configure apps.yaml

Add the FilamentIQ apps to your AppDaemon `apps.yaml`. Example with every config key:

```yaml
# apps.yaml — FilamentIQ configuration
# Replace YOUR_PRINTER_IP, YOUR_PRINTER_SERIAL, YOUR_SPOOLMAN_IP with your values.

ams_print_usage_sync:
  module: ams_print_usage_sync
  class: AmsPrintUsageSync
  enabled: true
  spoolman_base_url: "http://YOUR_SPOOLMAN_IP:7912"
  dry_run: false
  min_consumption_g: 2
  max_consumption_g: 300
  printer_ip: "YOUR_PRINTER_IP"
  printer_ftps_port: 990
  access_code_entity: "input_text.bambu_printer_access_code"
  threemf_enabled: true
  # printer_access_code: ""  # optional; overrides entity if set

ams_rfid_reconcile:
  module: ams_rfid_reconcile
  class: AmsRfidReconcile
  enabled: true
  spoolman_url: "http://YOUR_SPOOLMAN_IP:7912"
  startup_delay_seconds: 60
  startup_wait_helpers_seconds: 420
  startup_wait_retry_initial_seconds: 2
  startup_wait_retry_max_seconds: 30
  startup_probe_helper_entity: "input_text.ams_slot_1_spool_id"
  debounce_seconds: 3
  safety_poll_seconds: 600
  debug_logs: false
  strict_mode_reregister: false
  color_distance_threshold: 90
  evidence_log_path: "/config/ams_rfid_reconcile_evidence.log"

ams_rfid_guard:
  module: ams_rfid_guard
  class: AmsRfidGuard
  enabled: true
  spoolman_base_url: "http://YOUR_SPOOLMAN_IP:7912"
  scan_interval_seconds: 300
  dry_run: false
  notify_cooldown_minutes: 360
  cache_sensor_entity: "sensor.spoolman_spools_cache"
  use_cache_trigger: false
  rfid_managed_patterns: ["bambu", "bambu lab"]
  missing_ha_spool_uuid_mode: "warn_only"

filament_weight_tracker:
  module: filament_weight_tracker
  class: FilamentWeightTracker
  spoolman_url: "http://YOUR_SPOOLMAN_IP:7912"
  report_path: "/config/filament_weight_reports.log"

spoolman_dropdown_sync:
  module: spoolman_dropdown_sync
  class: SpoolmanDropdownSync
  enabled: true
  spoolman_base_url: "http://YOUR_SPOOLMAN_IP:7912"
```

**Important:** Edit `TRAY_ENTITY_BY_SLOT`, `ACTIVE_TRAY_ENTITY`, and `print_status` entity IDs in the source files to use your printer serial, or ensure your AppDaemon apps path uses a configurable entity prefix.

### 4. Dashboard

The dashboard is provided as YAML in `dashboard/filament_iq.yaml`. Because HA dashboards in storage mode cannot be deployed via script:

- **Import via HA UI:** Settings → Dashboards → Add Dashboard → Import from YAML
- Or paste into Settings → Dashboards → Raw configuration editor

Replace `YOUR_PRINTER_SERIAL` in the dashboard YAML with your Bambu device serial.

---

## Configuration Reference

| Key | App | Required | Default | Description |
|-----|-----|----------|---------|-------------|
| `enabled` | ams_print_usage_sync, ams_rfid_reconcile, ams_rfid_guard, spoolman_dropdown_sync | No | `true` | Master enable/disable |
| `spoolman_base_url` | ams_print_usage_sync, ams_rfid_guard, spoolman_dropdown_sync | Yes | — | Spoolman API base URL (e.g. `http://192.168.1.250:7912`) |
| `spoolman_url` | ams_rfid_reconcile, filament_weight_tracker | Yes | — | Same as above; some apps use this key |
| `dry_run` | ams_print_usage_sync, ams_rfid_guard | No | `false` | Log writes without calling Spoolman |
| `min_consumption_g` | ams_print_usage_sync | No | `2` | Skip writes below this (g) |
| `max_consumption_g` | ams_print_usage_sync | No | `300` | Reject writes above this (g) |
| `printer_ip` | ams_print_usage_sync | Yes | — | Bambu printer LAN IP |
| `printer_ftps_port` | ams_print_usage_sync | No | `990` | FTPS port for 3MF fetch |
| `access_code_entity` | ams_print_usage_sync | No | `input_text.bambu_printer_access_code` | HA entity for printer access code |
| `printer_access_code` | ams_print_usage_sync | No | — | Override; if set, ignores entity |
| `threemf_enabled` | ams_print_usage_sync | No | `true` | Enable 3MF parsing (Tier 1) |
| `startup_delay_seconds` | ams_rfid_reconcile | No | `60` | Delay before first reconcile |
| `startup_wait_helpers_seconds` | ams_rfid_reconcile | No | `420` | Max wait for HA helpers to be ready |
| `startup_wait_retry_initial_seconds` | ams_rfid_reconcile | No | `2` | Initial retry interval for startup probe |
| `startup_wait_retry_max_seconds` | ams_rfid_reconcile | No | `30` | Max retry interval |
| `startup_probe_helper_entity` | ams_rfid_reconcile | No | `input_text.ams_slot_1_spool_id` | Entity probed for readiness |
| `debounce_seconds` | ams_rfid_reconcile | No | `3` | Debounce tray/helper changes |
| `safety_poll_seconds` | ams_rfid_reconcile | No | `600` | Periodic reconcile interval |
| `debug_logs` | ams_rfid_reconcile | No | `false` | Extra logging |
| `strict_mode_reregister` | ams_rfid_reconcile | No | `false` | Strict mode for re-registration |
| `color_distance_threshold` | ams_rfid_reconcile | No | `90` | RGB distance for "close" color match |
| `evidence_log_path` | ams_rfid_reconcile | No | `/config/ams_rfid_reconcile_evidence.log` | Path for evidence log |
| `scan_interval_seconds` | ams_rfid_guard | No | `300` | Scan interval for policy checks |
| `notify_cooldown_minutes` | ams_rfid_guard | No | `360` | Min minutes between duplicate notifications |
| `cache_sensor_entity` | ams_rfid_guard | No | `sensor.spoolman_spools_cache` | Optional cache trigger entity |
| `use_cache_trigger` | ams_rfid_guard | No | `false` | Run scan on cache change |
| `rfid_managed_patterns` | ams_rfid_guard | No | `["bambu", "bambu lab"]` | Regex patterns for RFID-managed filament |
| `missing_ha_spool_uuid_mode` | ams_rfid_guard | No | `warn_only` | `warn_only` or `quarantine` |
| `report_path` | filament_weight_tracker | No | `/config/filament_weight_reports.log` | Path for weight delta reports |

---

## Troubleshooting

### 1. Helpers resetting on HA restart

**Cause:** `initial:` in configuration for a FilamentIQ-managed helper overwrites runtime state.

**Fix:** Remove all `initial:` from helpers in `filament_iq.yaml` package for reconciler-owned fields (e.g. `ams_slot_*_spool_id`, `ams_slot_*_status`, `ams_slot_*_expected_spool_id`). Keep `initial` only where a default is intended (e.g. `p1s_slot_to_spool_binding_json: "{}"`).

### 2. RFID spool recognized inconsistently

**Cause:** Some Bambu spools have dual NFC chips reporting different UIDs by orientation.

**Fix:** FilamentIQ uses `tray_uuid` as primary identity. Ensure the spool is seated consistently. If recognition flips, try rotating the spool 180° and re-seating.

### 3. Non-RFID slots not tracked

**Cause:** Legacy HA automation still enabled (e.g. `p1s_record_trays_used_during_print`).

**Fix:** Disable any pre-existing tray tracking automations; AppDaemon handles tray tracking and avoids mode:restart race conditions.

### 4. Dashboard not updating after file deploy

**Cause:** Dashboard is in HA storage mode.

**Fix:** Import via HA UI (Settings → Dashboards → Add Dashboard → Import from YAML) or edit via Settings → Dashboards → Raw configuration editor.

### 5. AppDaemon logs truncated

**Cause:** Supervisor keeps ~100 lines only.

**Fix:** Enable file-based logging in AppDaemon configuration or `apps.yaml` so logs persist to disk.

### 6. 3MF fetch fails (no Tier 1 allocation)

**Cause:** `curl` not found, wrong printer IP, or access code invalid.

**Fix:** Ensure `curl` is available on the HA host. Verify `printer_ip` and `access_code_entity` (or `printer_access_code`). Check AppDaemon logs for FTPS errors.

---

## License

MIT
