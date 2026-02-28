# Current Maturity
System is mid-hardening. Core reconciliation is stable and deployed.
End-to-end edge cases still under validation:
- New location gating
- Swap detection during active print
- Weight drift reconciliation

Resolved since last update:
- RFID orientation mismatch (dual-chip tray_uuid instability) — resolved by v4
  lot_nr migration. tray_uuid is orientation-independent factory serial.

---
# Known Issues
| Issue | Status |
|---|---|
| Slot 2 RFID mismatch (spool 41 dual-chip UID) | Fix pending — patch lot_nr=38D1181E8F024FDA9D040D3BE3A20312 after P8 deploy |
| Slot 3 Marswork weight wrong (shows 1000g, actual ~300g) | Manual correction needed before dry_run=false |
| Slot 6 Generic PETG needs manual enrollment | Pending |
| dry_run=True in ams_print_usage_sync | Flip after P1S_PRINT_USAGE_READY pipeline verified |

---
# Roadmap

## Phase 0 — Stabilization ✅
Core reconciler deployed. Unified 6-slot architecture. 364 tests passing.

## Phase 1 — Deterministic Baseline ✅
Filament_id as primary non-RFID signal. Color as fuzzy tiebreaker.
Sentinel short-circuit. Bambu vendor exclusion tightened.

## Phase 2 — Identity Hardening (IN PROGRESS)
- **P8: lot_nr migration (NEXT)** — Replace extra field identity with lot_nr.
  Retire rfid_tag_uid write path. Retire ha_spool_uuid. Free comment field.
  Simplify non-RFID sig to type|filament_id|color_hex.
- **P9: Legacy field cleanup** — PATCH extra fields to null on all spools.
  Delete extra field definitions in Spoolman UI. Retire canonicalizer.
  Update test suite.
- Verify P1S_PRINT_USAGE_READY pipeline end-to-end
- Flip dry_run=false in ams_print_usage_sync
- Correct slot 3 Marswork weight
- Slot 6 manual enrollment

## Phase 3 — Portability
Multi-printer support. Config-driven slot maps. Externalise hardcoded entity names.

## Phase 4 — Open Source Packaging
Documentation, install tooling, example configs.
