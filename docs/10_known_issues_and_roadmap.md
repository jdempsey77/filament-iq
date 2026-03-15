# Current Maturity

System is stable and deployed at v1.0.0. Core reconciliation is hardened with
full auto-enrollment for both RFID and non-RFID spools. All 6 slots resolve
automatically on first insert with zero manual intervention required for
enrolled spools. All three lifecycle phases (identity, usage tracking, weight
sync) now run in AppDaemon — 7 HA automations replaced.

## Version History

### v1.0.0 — Five-Phase Consumption Pipeline (2026-03-15)
- Five-phase pipeline: collect → decide → execute → notify → finalize
- Decision engine (`consumption_engine.py`) extracted as pure function — zero I/O
- RFID delta always wins for RFID spools (ground truth rule)
- Depletion handling: rfid_delta_depleted, 3mf_depleted, depleted_nonrfid
- Print history records: `appdaemon/apps/data/print_history/{job_key}.json`
- Confidence levels on every decision (high/medium/low)
- 3MF Tier 2.75 slot position matching removed (index vs slot mismatch)
- active_print.json persistence across AppDaemon restarts

### v0.9.0 — Pool_g Removal + Smart Empty Guard (2026-03-09)
- Removed pool_g estimation — two write paths only: RFID delta + 3MF match
- Smart Empty Guard prevents over-decrement on short/interrupted prints
- Write-ahead dedup with `_last_processed_job_key` fix
- 6 obsolete helpers removed (57/57 validated)

### v0.8.0 — Native FTPS + 3MF Tier 2.75 (2026-03)
- Native FTPS fetch via `ftplib.FTP_TLS` (replaced curl subprocess)
- 3MF Tier 2.75 slot position matching for non-RFID slotss
- Phantom consumption fixes (false usage on non-prints)

### v0.7.1 — Reconciler Performance (2026-03)
- Reconciler 26s → 0.4s (batch Spoolman fetch, eliminated serial API calls)

### v0.7.0 — Sync Color on Bind (2026-03)
- PATCHes filament `color_hex` on manual bind to match AMS tray-reported color
- Fixes lot_sig mismatches and 3MF matching failures from Bambu preset colors

## Resolved Since v0.6

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
- Sync color on bind — Spoolman filament color auto-PATCHed on manual assign.
- Native FTPS — replaced curl subprocess with ftplib.FTP_TLS.
- Pool_g removal — eliminated estimation-based write path, two paths only.
- Phantom consumption — false usage events on non-print jobs eliminated.
- 3MF overcounting on non-success prints — replaced `_FAILED_STATES` blocklist
  with `_SUCCESS_STATES` allowlist (`frozenset({"finish"})`). Only `finish`
  triggers the full 3MF consumption path. Non-success terminal states fall back
  to RFID delta only. Phantom state values cleaned out; pybambu confirms a
  closed set of 10 `gcode_state` values. Job key now included in notification
  messages. (c50eac0)

---

# Known Issues

| Issue | Status |
|---|---|
| Non-RFID color must match Bambu profile color | By design — Spoolman filament color_hex must match what Bambu reports for that filament_id profile, not the actual spool color |
| Two non-RFID spools with identical type+filament_id+color → AMBIGUOUS_SIG | By design — requires manual bind via dashboard |
| 3MF_UNMATCHED for brief tray activations | Backlog — short tray activations during print may not match 3MF plate data |
| Dashboard custom:mod-card not installed | Fixed for AMS Pro card. AMS HT card pending same fix. |
| print_history directory not auto-created | Directory must exist before first print completes. Created by deploy script or manually: `mkdir -p /addon_configs/a0d7b954_appdaemon/apps/data/print_history` |

---

# Backlog

## High Priority
- Validate usage tracking end-to-end with real prints
- Reference dashboard for OSS repo
- 3MF_UNMATCHED handling for brief tray activations

## Low Priority
- 3MF fetch timing optimization
- Dropdown label format in Spoolman sync
- Clean up `input_number.filament_iq_start/end_slot_N_g` helpers
- Phase 3: Portability
- Phase 4: OSS Packaging

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

## Phase 2b — Print Pipeline ✅
- P1S_PRINT_USAGE_READY pipeline verified ✅
- dry_run flipped to false ✅
- Two write paths: RFID delta + 3MF match (pool_g removed) ✅
- Write-ahead dedup with persisted seen_job_keys ✅
- Smart Empty Guard ✅
- _SUCCESS_STATES allowlist fix — 3MF path gated to `finish` only ✅
- Native FTPS fetch ✅
- 3MF Tier 2.75 slot position matching — REMOVED in v1.0 (index vs slot mismatch)

## Phase 2d — v1.0 Consumption Engine
- Five-phase pipeline (collect/decide/execute/notify/finalize) ✅
- consumption_engine.py extracted as pure decision function ✅
- RFID ground truth rule — delta always wins ✅
- Depletion methods: rfid_delta_depleted, 3mf_depleted, depleted_nonrfid ✅
- Print history persistence ✅
- Confidence levels on all decisions ✅
- 3MF Tier 2.75 removed (index vs slot mismatch) ✅
- active_print.json disk persistence ✅

## Phase 2c — Dashboard Polish
- AMS HT card — fix custom:mod-card wrapper (same fix as AMS Pro)
- Badge: sensor.ams_unbound_slot_count working, verify display
- Tap behavior: bound slots no-op tap confirmed
- Add spool quantity field to Add Spool dialog
- Reference dashboard for OSS repo

## Phase 3 — Portability
Multi-printer support. Config-driven slot maps. Externalise hardcoded entity names.

## Phase 4 — Open Source Packaging
Documentation, install tooling, example configs.
