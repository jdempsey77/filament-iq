# FilamentIQ Architecture

This document describes how FilamentIQ works: the AppDaemon reconciliation loop, Spoolman integration, AMS slot management, RFID vs non-RFID handling, and operator status states.

## Overview

FilamentIQ runs as several AppDaemon apps that coordinate with Home Assistant and Spoolman:

```
┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│  Bambu Lab Printer  │────▶│  Home Assistant     │────▶│  AppDaemon           │
│  (AMS + trays)      │     │  (sensors, helpers) │     │  (FilamentIQ apps)   │
└─────────────────────┘     └─────────────────────┘     └──────────┬──────────┘
                                                                  │
                                                                  ▼
                                                         ┌─────────────────────┐
                                                         │  Spoolman           │
                                                         │  (REST API)         │
                                                         └─────────────────────┘
```

## AppDaemon Reconciliation Loop

The **ams_rfid_reconcile** app is the core reconciliation engine. It:

1. **Listens** to tray sensor state changes (`listen_state` on each tray entity)
2. **Debounces** rapid changes (default 3 seconds) to avoid thrashing
3. **Runs reconciliation** when:
   - A tray entity changes (state or attributes)
   - The reconcile button is pressed
   - Startup delay expires (after helpers are ready)
   - Periodic safety poll (default 600 seconds)

4. **Per-slot logic:**
   - Reads `tag_uid` from tray attributes
   - If RFID present: looks up Spoolman spool by `extra.rfid_tag_uid` (UID match)
   - If non-RFID (empty tag): uses metadata fingerprint (material + color) vs Spoolman
   - Binds slot to spool: writes `input_text.ams_slot_N_spool_id`, updates Spoolman location
   - Clears previous slot occupant's location when binding changes (one spool per slot)

5. **Identity model (v4):**
   - **RFID spools:** `lot_nr` = tray_uuid (32-char hex from RFID chip)
   - **Non-RFID spools:** `lot_nr` = `type|filament_id|color_hex` (pipe-delimited signature)

## Spoolman Integration

All apps communicate with Spoolman via REST:

| App | Spoolman usage |
|-----|----------------|
| **ams_rfid_reconcile** | GET spools, PATCH location, extra.rfid_tag_uid, lot_nr |
| **ams_rfid_guard** | GET spools, PATCH location=QUARANTINE on policy violations |
| **spoolman_dropdown_sync** | GET /api/v1/filament, populates input_select |
| **ams_print_usage_sync** | PUT /api/v1/spool/{id}/use with use_weight |
| **filament_weight_tracker** | GET spools for before/after weight snapshots |

**Location lifecycle:**
- `AMS1_Slot1` … `AMS129_Slot1` — spool in AMS slot
- `Shelf` — in inventory, not in AMS (cleared when slot unbound)
- `New` — never auto-selected; explicit enrollment only
- `QUARANTINE` — set by Guard on RFID policy violations

## AMS Slot Management

Slots are mapped by `ams_units` config:

| Unit type | AMS index | Slots (default) |
|-----------|-----------|-----------------|
| ams_2_pro | 0 | 1, 2, 3, 4 |
| ams_ht | 128 | 5 |
| ams_ht | 129 | 6 |

Each slot has:
- **Tray entity:** `sensor.{prefix}_ams_{index}_tray_{slot}` (from Bambu integration)
- **Spool ID helper:** `input_text.ams_slot_N_spool_id` (reconciler-owned)
- **Unbound reason:** `input_text.ams_slot_N_unbound_reason` (for UI)

## RFID vs Non-RFID Spools

### RFID spools (Bambu Lab with RFID chip)

- Printer reads `tag_uid` from tray
- Reconcile matches `tag_uid` to Spoolman `extra.rfid_tag_uid`
- Single match → bind; zero match → deterministic candidates (Shelf, Bambu, material/color); multiple → tie-break or strict REFUSE
- States: `PENDING_RFID_READ` → `RFID_REGISTERED` → `STABILIZED` (or `OK`)

### Non-RFID spools (no chip or empty tag)

- `tag_uid` is empty or all-zero
- Reconcile uses metadata fingerprint: material, color, type
- Single match → bind, write signature to `lot_nr` → `OK_NON_RFID_REGISTERED`
- Multiple match → tie-break (prefer used, next-man-up, full pick)
- Zero match / generic sentinel → `NEEDS_MANUAL_BIND` (user assigns via dashboard)
- Empty tray → `UNBOUND_TRAY_EMPTY` (no notification)

### Unbound reason codes

| Code | Meaning |
|------|---------|
| `UNBOUND_TRAY_EMPTY` | Tray empty; no spool to bind |
| `UNBOUND_NO_TAG_UID` | No RFID read |
| `UNBOUND_NO_RFID_TAG_ALL_ZERO` | Non-RFID tray |
| `UNBOUND_TAG_UID_NO_MATCH` | RFID not found in Spoolman |
| `UNBOUND_TAG_UID_AMBIGUOUS` | Multiple spools match |
| `NEEDS_MANUAL_BIND` | User must assign spool |
| `WAITING_FOR_CONFIRMATION` | Low-confidence hold |

## Operator Status States

The `sensor.filament_iq_operator_status` (or configurable entity) synthesizes print status for automations and UI:

| State | Meaning |
|-------|---------|
| `offline` | Printer offline or unavailable |
| `idle_ready` | Printer idle, ready to print |
| `printing_normally` | Print in progress, no errors |
| `printing_attention_needed` | Print in progress, errors present |
| `paused_user` | Paused by user |
| `paused_error` | Paused due to error |
| `finished_success` | Print completed successfully |
| `failed_requires_intervention` | Print failed or error state |
| `unknown` | Unrecognized status |

Used by:
- **filament_weight_tracker** — triggers before/after snapshots on `printing_normally` → `idle`/`finished`/`failed`
- **ams_print_usage_sync** — triggered by `P1S_PRINT_USAGE_READY` event (from HA automation on print finish)
- Dashboard cards — display printer state

## Print Usage Sync Flow

1. **Print start:** HA automation records start snapshot (fuel gauge per slot) to `input_number.filament_iq_start_slot_N_g`
2. **Print end:** HA automation records end snapshot, fires `P1S_PRINT_USAGE_READY`
3. **ams_print_usage_sync** listens for event, reads slot→spool from `input_text.ams_slot_N_spool_id`
4. **Consumption:** RFID slots = start_g - end_g; non-RFID = time-weighted or equal split
5. **Spoolman write:** `PUT /api/v1/spool/{id}/use` with `use_weight`
6. **Dedup:** `seen_job_keys.json` prevents double-apply for same print

## RFID Guard

**ams_rfid_guard** runs periodically (default 300s) and enforces:

- Spools with `extra.rfid_tag_uid` must have `extra.ha_spool_uuid`
- Bambu Lab filament (vendor match) must have `ha_spool_uuid` if RFID-managed
- Violations → `location=QUARANTINE` (or warn_only mode)
