# Changelog

## [0.10.1] - 2026-03-12

### Fixed
- **Write-ahead dedup permanent data loss**: `_persist_seen_job_keys()` was called BEFORE `_spoolman_use()` writes. If Spoolman timed out, the job was permanently deduped with no retry possible — silent consumption loss. Dedup now persists AFTER the write loop, gated on `write_failed == 0`. Failed writes leave the job key unpersisted so the next print-finish event retries all slots.

### Tests
- 695 tests passing (up from 693 at v0.10.0)
- 2 new dedup tests: `test_spoolman_failure_does_not_dedup`, `test_spoolman_success_persists_dedup`

## [0.10.0] - 2026-03-12

### Fixed
- **RFID_IDENTITY_STUCK false positive**: enrolled spools with matching `lot_nr == tray_uuid` no longer flagged stuck after AppDaemon restart + 60s. The `_rfid_matches_spool` check now runs before the stuck gate instead of inside it.
- **Sanity cap blocking large prints**: `max_consumption_g` raised from 300g to 1000g. A 484g Big Crate print was silently skipped. 1000g matches the P1S single spool physical maximum.

### Changed
- **"New" spools visible in dropdown**: Spools with `location=New` now appear in the slot assign dropdown, allowing fresh spools to be bound without manually editing Spoolman location first.
- `tray_uuid` added to all `UNBOUND_REASON` log lines alongside `tag_uid` for improved diagnostics.
- Mock patch paths in tests corrected from `appdaemon.apps.filament_iq.*` to `filament_iq.*` for CI compatibility.

### Tests
- 693 tests passing (up from 688 at v0.9.0)
- 3 new RFID stuck detection tests: false positive prevention, unmatched tag fires, stuck clears on match
- 2 new sanity cap tests: 484g passes, 1001g blocked
- `conftest.py` added for CI `sys.path` setup

## [0.9.0] - 2026-03-11

### Added
- **RFID ↔ Spoolman weight reconciler**: after every successful print finish, RFID slots are reconciled against Spoolman. RFID sensor is always ground truth (no threshold). Per-slot isolation, negative remain guard, dry_run safe. 9 tests.
- 3MF fetch race guard: wait up to 15s for 3MF data before processing print finish
- Batch Spoolman fetch: single `GET /api/v1/spool?limit=1000` replaces ~12 individual GETs per print finish
- Smart empty guard: physical tray presence check (tag_uid / tray state) before moving depleted spool to Empty
- Scoped unbound-slot warning to actively-used trays only (10s delay after print start)
- `_SUCCESS_STATES` allowlist: 3MF consumption only written on `gcode_state=finish`. Prevents overcounting on cancelled/failed/short prints.
- Unique job key with timestamp suffix (F5)
- Tray duration filter `min_tray_active_seconds` (F7)
- `trays_used` passed to 3MF matcher (F3)
- `cancelled` gcode_state variant handled (F10)
- FTPS retry extended: 4 attempts, 110s total window
- `unavailable`/`unknown` states added to `_FAILED_STATES`

### Fixed
- `_active_run` not reset in finally (`ams_rfid_reconcile.py`): Spoolman outage no longer permanently blocks reconciler
- `seen_job_keys.json` atomic write: crash-safe dedup via `tempfile` + `os.replace()`
- Write-ahead dedup: persist `seen_job_keys` before Spoolman writes (crash between write and persist no longer causes double-charge)
- Non-blocking finish wait: `run_in` chain replaces `time.sleep` (F2)

### Changed (Breaking)
- **Removed pool_g / time-weighted / equal_split estimation** — usage pipeline now has exactly two write paths: RFID fuel gauge delta and 3MF slicer match. Slots with neither are logged (`USAGE_NO_EVIDENCE`) and skipped. Under-count is acceptable; phantom charges are eliminated.

### Tests
- 688 tests passing (up from ~580 at v0.8.0)
- New test files: `test_ams_rfid_guard.py`, `test_filament_weight_tracker.py`, `test_spoolman_dropdown_sync.py`
- Test harness drift fixed: `_TestableUsageSync` mirrors real `initialize()` attributes
- `_spoolman_patch` and `_persist_seen_job_keys` mocked in test harness
- 46 new tests covering previously zero-coverage modules

### Security
- `_spoolman_patch` now mocked in test harness (prevents real HTTP in tests)
- `seen_job_keys.json` atomic write prevents corruption on crash
- Shell script quoting hardened

## [0.8.0] - 2026-03-09
### Fixed
- Phantom consumption: skip Spoolman writes for failed/cancelled/error prints
- Phantom consumption: zero fuel-gauge-delta guard prevents slicer estimate from becoming false pool
- Dedup: failed prints no longer stamp `_last_processed_job_key`, allowing retry with same job key

### Added
- Native FTPS fetch via `ftplib.FTP_TLS` with implicit TLS (replaces curl subprocesses, ~40% faster)
- `slot_position_material` match tier (2.75) in 3MF matcher — matches by filament index → AMS slot + material when color tiers fail
- Config toggle `threemf_fetch_method: native|curl` (default: native)

### Changed
- Renamed `SLOT_ASSIGNED_NO_LOT_SIG` → `SLOT_ASSIGNED_LOT_SIG_EXISTS` log message in reconciler

## [0.1.0] - TBD
### Added
- Initial public release
- Filament consumption tracking (RFID fuel gauge delta, 3MF slicer-exact matching)
- Spool identity management for RFID and non-RFID spools
- Automatic spool enrollment on first detection
- Spoolman consumption sync after each print
- HA dashboard with per-slot status, filament color bars, and active slot highlighting
- Support for AMS 2 Pro and AMS HT units in any combination
