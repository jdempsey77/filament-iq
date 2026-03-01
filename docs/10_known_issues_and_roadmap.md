# Current Maturity

System is stable and deployed. Core reconciliation is hardened with full
auto-enrollment for both RFID and non-RFID spools. All 6 slots resolve
automatically on first insert with zero manual intervention required for
enrolled spools.

Resolved since last update:
- RFID orientation mismatch (dual-chip tray_uuid instability) — resolved by v4
  lot_nr migration. tray_uuid is orientation-independent factory serial.
- P8 lot_nr migration — complete. All identity stored in lot_nr. extra fields
  retired. canonicalizer retired. comment field freed.
- RFID auto-enrollment — new RFID spools auto-enroll on first insert via
  sig-based fallback (type|filament_id|color_hex).
- Non-RFID auto-match — unenrolled spools found via material+color search.
  Sentinel skip moved to last resort.
- Non-RFID swap detection — tray color/material change auto-detected, helper
  cleared, rematch triggered without manual intervention.
- Non-RFID empty tray clear — empty tray with bound helper now moves spool to
  Shelf and clears helper automatically.
- RFID-enrolled spool exclusion — UUID lot_nr spools excluded from non-RFID
  candidate pool.
- Ambiguous sig slot-steal — spools bound in another slot excluded from
  rematch candidates.
- Material normalization — PLA+, PLA-CF, PETG-CF etc. normalized for matching.
- Startup swap suppression — spool-swap-during-print automation suppressed for
  90 seconds after AppDaemon restart to prevent false positives.
- Data integrity — startup lot_nr patches removed. Duplicate lot_nr on spool 4
  cleaned up.

---

# Known Issues

| Issue | Status |
|---|---|
| Non-RFID color must match Bambu profile color | By design — Spoolman filament color_hex must match what Bambu reports for that filament_id profile, not the actual spool color |
| Two non-RFID spools with identical type+filament_id+color → AMBIGUOUS_SIG | By design — requires manual bind via dashboard |
| dry_run=True in ams_print_usage_sync | Flip after P1S_PRINT_USAGE_READY pipeline verified end-to-end |
| Dashboard custom:mod-card not installed | Fixed for AMS Pro card. AMS HT card pending same fix. |

---

# Roadmap

## Phase 0 — Stabilization ✅
Core reconciler deployed. Unified 6-slot architecture. 364 tests passing.

## Phase 1 — Deterministic Baseline ✅
Filament_id as primary non-RFID signal. Color as fuzzy tiebreaker.
Sentinel short-circuit. Bambu vendor exclusion tightened.

## Phase 2 — Identity Hardening ✅
- **P8: lot_nr migration** ✅ — lot_nr is sole identity field. extra fields
  retired. canonicalizer retired. comment freed. RFID and non-RFID both
  auto-enroll on first insert. Unenrolled spool fallback. Swap detection.
  Empty tray clear. RFID exclusion from non-RFID pool. Material normalization.
  Ambiguous sig slot-steal protection. Startup swap suppression.
- **P9: Legacy field cleanup (NEXT)** — PATCH extra fields to null on all
  spools. Delete extra field definitions in Spoolman UI. Retire canonicalizer
  entirely. Update test suite to remove UUID/canonicalizer references.

## Phase 2b — Print Pipeline
- Verify P1S_PRINT_USAGE_READY pipeline end-to-end
- Flip dry_run=false in ams_print_usage_sync
- Validate consumption tracking after first real print

## Phase 2c — Dashboard Polish
- AMS HT card — fix custom:mod-card wrapper (same fix as AMS Pro)
- Badge: sensor.ams_unbound_slot_count working, verify display
- Tap behavior: bound slots no-op tap confirmed
- Add spool quantity field to Add Spool dialog

## Phase 3 — Portability
Multi-printer support. Config-driven slot maps. Externalise hardcoded entity names.

## Phase 4 — Open Source Packaging
Documentation, install tooling, example configs.
