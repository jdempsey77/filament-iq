# FilamentIQ Configuration Reference

Complete reference for every key in `apps.yaml.example`. Replace placeholders (`YOUR_SPOOLMAN_IP`, `YOUR_PRINTER_IP`, printer serial) with your values.

---

## ams_rfid_reconcile

Slot identity management: RFID tag matching, non-RFID fingerprinting.

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `module` | string | Yes | — | Must be `filament_iq.ams_rfid_reconcile` |
| `class` | string | Yes | — | Must be `AmsRfidReconcile` |
| `enabled` | boolean | No | `true` | Enable or disable the app |
| `spoolman_url` | string | Yes | — | Spoolman API base URL (e.g. `http://192.168.4.124:7912`) |
| `printer_serial` | string | Yes | — | Printer serial (e.g. `01P00C5A3101668`) |
| `printer_model` | string | No | `p1s` | Printer model; used for entity prefix (e.g. `p1s_01p00c5a3101668`) |
| `ams_units` | list | No | AMS Pro + HT | List of `{type, ams_index, slots}`. Default: slots 1–4 (AMS Pro), 5–6 (HT) |
| `startup_delay_seconds` | int | No | `60` | Seconds to wait before first reconciliation after AppDaemon start |
| `startup_wait_helpers_seconds` | int | No | `420` | Max seconds to wait for helpers to become available |
| `startup_probe_helper_entity` | string | No | `input_text.ams_slot_1_spool_id` | Entity to probe for helper readiness |
| `debounce_seconds` | int | No | `3` | Debounce window for tray state changes before running reconcile |
| `safety_poll_seconds` | int | No | `600` | Interval for periodic reconciliation (seconds) |
| `strict_mode_reregister` | boolean | No | `false` | When true, refuse auto-pick when multiple metadata matches |
| `evidence_log_path` | string | No | `/config/ams_rfid_reconcile_evidence.log` | Path for evidence/debug log |
| `debug_logs` | boolean | No | `false` | Enable verbose debug logging |
| `color_distance_threshold` | int | No | `90` | Color matching threshold for non-RFID (0–255) |
| `reconcile_button_entity` | string | No | `input_button.filament_iq_reconcile_now` | Button to trigger manual reconcile |
| `nonrfid_enabled_entity` | string | No | `input_boolean.filament_iq_nonrfid_enabled` | Boolean to enable/disable non-RFID matching |
| `last_mapping_json_entity` | string | No | `input_text.filament_iq_last_mapping_json` | Entity to store last mapping JSON |
| `startup_suppress_swap_entity` | string | No | `input_boolean.filament_iq_startup_suppress_swap` | Suppress swap alerts during startup |

---

## ams_rfid_guard

Auditor: quarantines Spoolman spools with RFID policy violations.

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `module` | string | Yes | — | Must be `filament_iq.ams_rfid_guard` |
| `class` | string | Yes | — | Must be `AmsRfidGuard` |
| `enabled` | boolean | No | `true` | Enable or disable the app |
| `spoolman_url` | string | Yes | — | Spoolman API base URL |
| `scan_interval_seconds` | int | No | `300` | How often to run the guard scan |
| `dry_run` | boolean | No | `false` | When true, log violations but do not quarantine |
| `notify_cooldown_minutes` | int | No | `360` | Minutes between duplicate notifications |
| `cache_sensor_entity` | string | No | `sensor.spoolman_spools_cache` | Optional cache sensor for trigger |
| `use_cache_trigger` | boolean | No | `false` | When true, also run on cache sensor change |
| `rfid_managed_patterns` | list | No | `["bambu", "bambu lab"]` | Vendor name patterns for RFID-managed filament |
| `missing_ha_spool_uuid_mode` | string | No | `warn_only` | `warn_only` or `quarantine` when ha_spool_uuid missing |

---

## spoolman_dropdown_sync

Populates filament dropdown from Spoolman `/api/v1/filament`.

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `module` | string | Yes | — | Must be `filament_iq.spoolman_dropdown_sync` |
| `class` | string | Yes | — | Must be `SpoolmanDropdownSync` |
| `enabled` | boolean | No | `true` | Enable or disable the app |
| `spoolman_url` | string | Yes | — | Spoolman API base URL |
| `dropdown_entity` | string | No | `input_select.spoolman_new_spool_filament` | input_select entity to populate |

---

## ams_print_usage_sync

Writes filament consumption to Spoolman after each print (triggered by `P1S_PRINT_USAGE_READY` event).

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `module` | string | Yes | — | Must be `filament_iq.ams_print_usage_sync` |
| `class` | string | Yes | — | Must be `AmsPrintUsageSync` |
| `enabled` | boolean | No | `true` | Enable or disable the app |
| `spoolman_url` | string | Yes | — | Spoolman API base URL |
| `printer_serial` | string | Yes | — | Printer serial |
| `printer_model` | string | No | `p1s` | Printer model for entity prefix |
| `printer_ip` | string | No | — | Printer IP for 3MF FTP download (required if 3MF enabled) |
| `printer_ftps_port` | int | No | `990` | FTPS port for 3MF download |
| `access_code_entity` | string | No | `input_text.bambu_printer_access_code` | Entity holding printer access code |
| `spoolman_sensor_prefix` | string | No | `sensor.spoolman_spool_` | Prefix for Spoolman spool sensors (3MF fallback) |
| `dry_run` | boolean | No | `false` | When true, log but do not write to Spoolman |
| `min_consumption_g` | float | No | `2` | Minimum consumption (g) to record |
| `max_consumption_g` | float | No | `300` | Maximum consumption (g) per slot; higher values ignored |
| `threemf_enabled` | boolean | No | `true` | Enable 3MF parsing for per-slot color/material fallback |
| `trays_used_entity` | string | No | `input_text.filament_iq_trays_used_this_print` | Entity storing trays used during print |
| `notify_target` | list | No | `[]` | Optional list of notify targets (e.g. `["notify.mobile_app"]`) |
| `ams_units` | list | No | AMS Pro + HT | Same format as ams_rfid_reconcile; omit for default |

---

## filament_weight_tracker

Snapshots spool weights before/after prints for validation.

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `module` | string | Yes | — | Must be `filament_iq.filament_weight_tracker` |
| `class` | string | Yes | — | Must be `FilamentWeightTracker` |
| `spoolman_url` | string | Yes | — | Spoolman API base URL |
| `printer_serial` | string | Yes | — | Printer serial |
| `printer_model` | string | No | `p1s` | Printer model for entity prefix |
| `report_path` | string | No | `/config/filament_weight_reports.log` | Path for weight report log |
| `operator_status_entity` | string | No | `sensor.filament_iq_operator_status` | Entity for print state (triggers snapshots) |
| `weight_snapshot_button_entity` | string | No | `input_button.filament_iq_weight_snapshot_now` | Button for manual snapshot |
| `print_name_entities` | list | No | `[sensor.{prefix}_current_stage, sensor.{prefix}_print_status]` | Entities for print name in report |

---

## Home Assistant Helpers

FilamentIQ requires these Home Assistant helpers. Create them via UI or include `ha-config/packages/filament_iq.yaml` (and any additional helpers from your `configuration.yaml`):

**Required for reconciliation:**
- `input_text.ams_slot_1_spool_id` … `input_text.ams_slot_6_spool_id` — Spool ID per slot (reconciler writes)
- `input_text.ams_slot_N_unbound_reason` — Unbound reason (reconciler writes)
- `input_button.filament_iq_reconcile_now` — Manual reconcile trigger
- `input_boolean.filament_iq_nonrfid_enabled` — Enable non-RFID matching
- `input_boolean.filament_iq_startup_suppress_swap` — Suppress startup swap alerts

**Required for print usage sync:**
- `input_text.filament_iq_trays_used_this_print` — Trays used during print
- `input_text.filament_iq_printer_access_code` or `input_text.bambu_printer_access_code` — Printer access code (for 3MF)
- `input_number.filament_iq_start_slot_N_g` / `input_number.filament_iq_end_slot_N_g` — Start/end snapshots (from HA automation)

**Optional:**
- `input_text.filament_iq_last_mapping_json` — Last mapping JSON
- `sensor.filament_iq_operator_status` — Template sensor for print state (see configuration.yaml)
- `input_button.filament_iq_weight_snapshot_now` — Manual weight snapshot

See `helpers_manifest.yaml` and your `configuration.yaml` for the full list.
