# Changelog

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
- Three-tier filament consumption tracking (3MF parsing, RFID fuel gauge, time-weighted estimation)
- Spool identity management for RFID and non-RFID spools
- Automatic spool enrollment on first detection
- Spoolman consumption sync after each print
- HA dashboard with per-slot status, filament color bars, and active slot highlighting
- Support for AMS 2 Pro and AMS HT units in any combination
