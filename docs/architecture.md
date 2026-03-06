# Architecture

FilamentIQ is a set of AppDaemon apps that bridge Bambu Lab printers, Home Assistant, and Spoolman to provide automatic filament consumption tracking and spool identity management.

## System Overview

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
    │  3MF File   │  │  Tray      │  │  ha-bambulab    │
    │  (slicer    │  │  Sensors   │  │  integration    │
    │   weights)  │  │  (color,   │  │  (print status, │
    │             │  │   active)  │  │   task name)    │
    └──────┬──────┘  └─────┬──────┘  └──────┬──────────┘
           │               │                │
           └───────┬───────┘                │
                   │                        │
            ┌──────▼────────────────────────▼──┐
            │           AppDaemon               │
            │                                   │
            │  ams_print_usage_sync             │
            │  ams_rfid_reconcile               │
            │  ams_rfid_guard                   │
            │  threemf_parser                   │
            │  spoolman_dropdown_sync           │
            │  filament_weight_tracker          │
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

## AppDaemon Apps

All apps live in `apps/filament_iq/` and extend `hassapi.Hass`. They communicate with Spoolman via raw `urllib.request` (no `requests` library).

### ams_print_usage_sync.py (~930 lines)

The core consumption tracking app. Allocates filament usage to AMS slots after each print using a three-tier priority cascade:

**Tier 1 — 3MF file parsing:** FTPS into the printer's `/cache/` directory, downloads the .3mf file, and parses `Metadata/slice_info.config` for per-filament `used_g` values. Filaments are matched to physical slots by color + material with close-color fallback (Euclidean RGB distance < 30) and material-only fallback when exactly one slot matches. Yields slicer-exact weights (~1% accuracy).

**Tier 2 — RFID fuel gauge delta:** For RFID slots not covered by Tier 1. Uses `start_g - end_g` from tray fuel gauge snapshots. Resolution is coarse (~40g) but deterministic for single-RFID prints. RFID total is capped to print weight to prevent fuel gauge overshoot.

**Tier 3 — Time-weighted estimation:** For non-RFID slots or when Tier 1/2 data is insufficient. The remaining consumption pool (`print_weight - tier1_total - tier2_total`) is split proportionally by how long each slot was active during the print. Falls back to equal split if no time data. Naturally captures purge tower waste because active duration includes purge time.

**Trigger flow:**
1. Print status changes to `running`/`printing` → clears tracking state, seeds active tray, triggers 3MF background fetch
2. `active_tray` sensor changes → records tray start/end times
3. Print status leaves `running`/`printing` → closes time segments
4. HA automation fires `P1S_PRINT_USAGE_READY` event with start/end fuel gauge JSON, job key, and print weight
5. App allocates consumption across tiers, writes to Spoolman via `PUT /api/v1/spool/{id}/use`, sends persistent notification summary

**Deduplication:** Job keys are persisted to `data/seen_job_keys.json` (capped at 50 entries).

**Safety guards:** `min_consumption_g` (default 2g) and `max_consumption_g` (default 300g) reject outliers. Depleted spools (remaining_weight ≤ 0) are automatically moved to location `Empty`.

### ams_rfid_reconcile.py (~3700 lines)

Spool identity management — the largest and most complex app.

**Identity model (v4 — lot_nr):**
- RFID spools: `lot_nr` = `tray_uuid` (32-char hex spool factory serial from RFID chip)
- Non-RFID spools: `lot_nr` = `type|filament_id|color_hex` (pipe-delimited fingerprint, lowercase)
- `comment` field is reserved for human use — never written by the reconciler

**Fail-closed behavior:** Ambiguity (0 or >1 candidates) → slot stays UNBOUND. This is intentional — false positives are worse than manual resolution.

**Triggers:**
- Tray sensor state changes (with debounce, default 3s)
- Helper `input_text.ams_slot_{1-6}_spool_id` changes
- Manual reconcile button, `bambu_rfid_reconcile_now` event, `AMS_RECONCILE_ALL` event
- Startup delay (default 60s) with helper readiness probing (waits up to 420s for HA helpers)
- Periodic safety poll (default 600s, status_only)

**Reconcile flow per slot:**
1. Read tray sensor attributes (`tag_uid`, `tray_uuid`, `color`, `type`, etc.)
2. Build identity key: `tray_uuid` for RFID, `type|filament_id|color_hex` for non-RFID
3. Look up in Spoolman via `lot_nr` index (or legacy `extra.rfid_tag_uid` fallback)
4. For multiple candidates: deterministic tie-break ladder (prefer_used → next_man_up → full_pick)
5. Write spool ID to `input_text.ams_slot_{N}_spool_id`, status to `ams_slot_{N}_status`
6. Update Spoolman spool location to canonical slot name (e.g. `AMS1_Slot3`)
7. Log evidence transcript to `evidence_log_path`

**Slot numbering:** 1–4 = AMS1 trays (ams_index 0), 5 = AMS_128 HT slot, 6 = AMS_129 HT slot.

**Location constants:**
- `AMS1_Slot1` through `AMS1_Slot4`, `AMS128_Slot1`, `AMS129_Slot1` (canonical)
- `Shelf` — not in AMS
- `Empty` — depleted, excluded from matching
- `QUARANTINE` — policy violation (set by Guard)

### ams_rfid_guard.py (~360 lines)

Periodic auditor enforcing RFID identity invariants. Runs independently of the reconciler.

**Invariants enforced (for spools in AMS locations only):**
- A) Spool with `extra.rfid_tag_uid` must have identity (`lot_nr` or `ha_spool_uuid`)
- B) Spool with RFID-managed filament (Bambu vendor) must have identity
- C) Already-quarantined spools are skipped (idempotent)

**Actions:** Quarantines violating spools by setting `location=QUARANTINE`. Supports `warn_only` mode and `dry_run`. Sends persistent notifications with cooldown.

### threemf_parser.py (~380 lines)

Pure utility module — no AppDaemon or HA dependencies. Used by `ams_print_usage_sync` for Tier 1 allocation.

- `ftps_list_cache()` — lists `.3mf` files on printer via `curl --ssl-reqd`
- `ftps_download_3mf()` — downloads a specific file using CWD+RETR (handles unicode/emoji filenames)
- `find_best_3mf()` — matches task name to filename (exact → contains → newest)
- `parse_3mf_filaments()` — extracts `<filament>` elements from `Metadata/slice_info.config` inside the ZIP
- `match_filaments_to_slots()` — matches filaments to physical slots by color+material with three-tier fallback (exact → close color → material-only single)

### spoolman_dropdown_sync.py (~170 lines)

Populates `input_select.spoolman_new_spool_filament` from Spoolman's `/api/v1/filament` endpoint on startup and on `SPOOLMAN_REFRESH_FILAMENT_DROPDOWN` event. Used by the dashboard "Add Spool" form.

### filament_weight_tracker.py (~220 lines)

Before/after weight delta reporter. Takes a snapshot of all Spoolman spool weights at print start, another at print end, and writes a JSON report of deltas to `report_path`. Useful for validating that consumption tracking is working correctly. Supports manual snapshots via `input_button.filament_iq_weight_snapshot_now`.

## HA Configuration Layer

### Package: `ha-config/packages/filament_iq.yaml` (~4000 lines)

Defines all HA entities used by the AppDaemon apps:

- **input_text**: Slot state (`ams_slot_{1-6}_spool_id`, `_status`, `_unbound_reason`, `_expected_spool_id`, `_tray_signature`, `_expected_color_hex`, `_rfid_pending_until`), print tracking (`trays_used_this_print`, `slot_to_spool_binding_json`), forms (add filament/spool)
- **input_boolean**: Mutex flags (`filament_iq_print_active`), feature toggles (`filament_iq_nonrfid_enabled`, `filament_debug_mode`, `filament_test_mode`), startup suppression
- **input_button**: Manual triggers (`p1s_rfid_reconcile_now`, `p1s_weight_snapshot_now`)
- **input_number**: Tare weights, spool form fields, start/end fuel gauge per-slot values
- **input_select**: Filament dropdown (populated by `spoolman_dropdown_sync`)
- **template sensors**: Operator status, unbound slot count, derived states
- **automations**: Print lifecycle (start/finish snapshots, `P1S_PRINT_USAGE_READY` event firing)
- **rest_command**: Spoolman API calls for spool/filament creation from dashboard forms

### Dashboard: `dashboard/filament_iq.yaml` (~1850 lines)

Lovelace dashboard with sections for:
- Printer status, controls (pause/resume/cancel), progress, time remaining
- AMS slot cards showing bound spool, color, material, remaining weight
- Camera feed (WebRTC)
- Spool management (add filament, add spool, edit spool)
- Requires custom cards: `mushroom`, `config-template-card`, `timer-bar-card`, `webrtc-camera`, `card-mod`

## Data Flow: Print Lifecycle

1. **Print starts** → HA automation snapshots tray fuel gauges to `input_number.p1s_start_slot_{1-6}_g` → `ams_print_usage_sync` begins tray activity tracking and fetches 3MF
2. **During print** → `active_tray` sensor changes drive time-segment tracking in `ams_print_usage_sync`
3. **Print ends** → HA automation snapshots end fuel gauges → fires `P1S_PRINT_USAGE_READY` custom event with start/end JSON, job key, and print weight
4. **Usage sync processes** → Three-tier allocation → Spoolman `PUT /api/v1/spool/{id}/use` → persistent notification
5. **Reconciler maintains** slot-to-spool bindings throughout, updating `input_text.ams_slot_{N}_spool_id` as trays change
6. **Guard audits** Spoolman periodically, quarantining spools with broken identity
