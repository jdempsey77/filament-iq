# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FilamentIQ is a Home Assistant + AppDaemon integration that tracks filament consumption for Bambu Lab 3D printers and writes usage data to Spoolman. It uses a three-tier allocation pipeline (3MF parsing → RFID fuel gauge → time-weighted estimation) and manages spool identity via RFID tags and color/material fingerprints.

## Commands

### Run tests
```bash
python3 -m pytest tests/ -q           # all tests
python3 -m pytest tests/test_ams_rfid_reconcile.py -v   # single file
python3 -m pytest tests/test_ams_rfid_reconcile.py::TestClassName::test_method -v  # single test
```

### Run audit
```bash
python3 scripts/audit_config_driven.py
```

### CI
CI runs on push/PR to main: installs `appdaemon pytest` on Python 3.11, runs pytest, then runs the audit script (continue-on-error).

### Deploy to Home Assistant
```bash
# Requires scripts/deploy.env (copy from deploy.env.example)
bash scripts/manage_filament_iq.sh
```

## Architecture

All AppDaemon apps live in `appdaemon/apps/filament_iq/`. Each extends `hassapi.Hass`.

### Core apps (by importance)

- **ams_print_usage_sync.py** (~930 lines) — Main print tracking app. Listens for `P1S_PRINT_USAGE_READY` event, runs the three-tier allocation pipeline, writes consumption to Spoolman via `PUT /api/v1/spool/{id}/use`. Deduplicates via persisted `seen_job_keys.json`. Uses `threemf_parser.py` for Tier 1 (3MF FTPS fetch + parse).
- **ams_rfid_reconcile.py** (~3700 lines) — Spool identity management. Binds AMS slots to Spoolman spools using RFID `tray_uuid` (stored in `lot_nr`) or non-RFID fingerprints (`type|filament_id|color_hex`). Fail-closed: ambiguity → UNBOUND. Listens to tray sensor changes with debounce + periodic safety poll.
- **ams_rfid_guard.py** (~360 lines) — Periodic auditor enforcing RFID policy invariants. Quarantines spools that violate identity rules.
- **threemf_parser.py** (~380 lines) — FTPS download of 3MF files from printer, parses `slice_info.config` for per-filament weights, matches filaments to physical slots by color + material.
- **spoolman_dropdown_sync.py** (~170 lines) — Syncs Spoolman spool list to HA input_select dropdowns.
- **filament_weight_tracker.py** (~220 lines) — Weight delta reporting.

### HA configuration

- `ha-config/packages/filament_iq.yaml` — HA package defining input_text/input_boolean/sensor helpers for slot state (spool IDs, statuses, binding JSON).
- `dashboard/filament_iq.yaml` — Lovelace dashboard YAML.

### Test structure

Tests in `tests/` mock `hassapi.Hass` with a fake class injected into `sys.modules` before importing app modules. No AppDaemon runtime needed. Tests use only pytest (no additional test deps).

## Key Conventions

- Spoolman API is called via `urllib.request` (no `requests` library) — all apps use raw urllib.
- Slot numbering: 1–4 = AMS1 trays, 5–6 = AMS Lite (HT) trays. Constant `PHYSICAL_AMS_SLOTS = (1, 2, 3, 4, 5, 6)`.
- Entity IDs contain printer serial (e.g., `sensor.p1s_YOUR_PRINTER_SERIAL_ams_1_tray_1`). The `TRAY_ENTITY_BY_SLOT` dict in each app maps slot numbers to entity IDs.
- Identity stored in Spoolman `lot_nr` field: RFID spools use `tray_uuid` (32-char hex), non-RFID use `type|filament_id|color_hex`.
- The `comment` field in Spoolman is reserved for human use — apps never write to it.
- Deploy script uses `scripts/deploy.env` (gitignored) for SSH/HA credentials.
