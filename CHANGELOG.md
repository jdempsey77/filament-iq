# Changelog

## [0.9.0] - 2026-03-09
### Changed (Breaking)
- **Removed pool_g / time-weighted / equal_split estimation** — usage pipeline now has exactly two write paths: RFID fuel gauge delta and 3MF slicer match. Slots with neither are logged (`USAGE_NO_EVIDENCE`) and skipped. Under-count is acceptable; phantom charges are eliminated.

### Added
- 3MF fetch race guard: wait up to 15s for 3MF data before processing print finish
- Batch Spoolman fetch: single `GET /api/v1/spool?limit=1000` replaces ~12 individual GETs per print finish
- Smart empty guard: physical tray presence check (tag_uid / tray state) before moving depleted spool to Empty
- Scoped unbound-slot warning to actively-used trays only (10s delay after print start)

### Fixed
- Write-ahead dedup: persist `seen_job_keys` before Spoolman writes (crash between write and persist no longer causes double-charge)

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
