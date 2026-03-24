# Changelog

## [1.5.2] — 2026-03-24

### Added
- **EOL spool auto-archive** — new `auto_archive_depleted_spools` config flag
  (default: false). When enabled, automatically PATCHes `{"archived": true}`
  to Spoolman when a spool's post-write remaining drops to 0g. Archive failure
  is caught as WARNING and never blocks the unbind pipeline.

### Fixed
- **3MF_UNMATCHED data loss on rehydrated prints** — when AppDaemon restarted
  mid-print, `active_slots` narrowing ran before 3MF matching and excluded
  slots whose tray tracking was lost across the restart. Non-RFID consumption
  was silently dropped as `no_evidence`. Fix: pass `trays_used=None` to
  `match_filaments_to_slots` when rehydrated (disables incomplete slot filter),
  then readmit 3MF-matched slots into `_trays_used` for write processing.
  Confirmed data loss: 43.6g (2026-03-15), 9.65g (2026-03-24).
- **Snapshot trust validation** — `_build_start_snapshot` now excludes RFID
  slots where fuel gauge reads 0.0 but spool is bound and physically present
  (stale/uninitialized sensor). Logs `SNAPSHOT_IMPLAUSIBLE` at WARNING.
  Rehydration helper-recovery path also validated. Excluded slots produce
  explicit `DATA_LOSS: start_g not captured` instead of silent `BELOW_MIN`.

### Tests
- 4 new regression tests for rehydrated print 3MF matching
- 5 new tests for EOL spool auto-archive
- 5 new tests for snapshot plausibility validation (1159 total)

## [1.5.0] — 2026-03-21

### Added
- **Filament IQ Manager** — custom Preact Lovelace card for full spool,
  filament, and vendor management without leaving Home Assistant
- **filament_iq_proxy** — HA custom component proxying Spoolman API via
  WebSocket (works with Nabu Casa remote access)
- **SpoolmanDB import** — fuzzy search across 6,957+ filaments with
  one-click import to filament library
- **Location filter** — filter spool list by All / In AMS / Shelf /
  New / Unassigned
- **Location badge** — colored pill on each spool row showing actual
  AMS slot or storage location
- **AMS offline state** — AMS section headers and slot cards show
  "Disconnected" state when AMS unit is offline
- **Refresh button** — card header refresh to re-fetch all Spoolman data
- **Archive empty spools** — one-tap archive with confirm dialog
- **Spool ID badge** — monospace #ID badge on each spool row
- **Reference dashboard** — parameterized 3D Printer + Filament IQ views
  for new user setup (dashboards/filament-iq-reference.yaml)
- **Setup script** — interactive setup-dashboard.sh generates configured
  dashboard YAML from printer serial and AMS configuration
- **HACS resource repair script** — fix-hacs-resources.mjs auto-discovers
  correct HACS paths from filesystem
- **README.md** — full installation guide with screenshots,
  troubleshooting, and architecture diagram

### Changed
- Renamed "Filament Manager" to "Filament IQ" throughout (button, view
  title, card header)
- Reload button moved from 3D Printer page to Filament IQ card header
- Location display promoted from sub-line text to prominent colored badge
- Confirm dialog uses position:fixed — visible regardless of scroll position

### Removed
- Duplicate AMS slot status chips card from 3D Printer page
- Redundant "Filaments" and "Spools" nav buttons (pointed to deleted subviews)
- Old Filament Library and Spool Inventory dashboard subviews
- AppDaemon spoolman_proxy.py (replaced by filament_iq_proxy custom component)

### Fixed
- HACS resource paths corrected after storage file corruption
  (lovelace-mushroom, lovelace-card-mod, lovelace-layout-card, etc.)
- Service worker cache-busting via ?v=timestamp suffix on card resource URL
- Confirm dialog invisible when user scrolled to bottom of spool list
- WebSocket event subscription leak (unsubscribe after response received)

## [1.0.0] — 2026-03-15

### Architecture
- New consumption_engine.py: pure decision engine, zero AppDaemon dependency
- Five-phase pipeline: collect → decide → execute → notify → finalize
- 3MF fetched at print start (10s delay, retries to +160s from start)
  Eliminates finish-line race — _finish_wait_tick deleted
- active_print.json written at three lifecycle points:
  print start, 3MF fetch success, all retries failed
- Print history persisted to data/print_history/{job_key}.json
  Last 50 prints retained

### Bug Fixes
- RFID delta now always wins over 3MF for RFID spools [Bug 13]
- Depleted spool location always PATCHed to Empty after write [Bugs 14/15]
- Notification shows post-write remaining, not pre-write cache [Bug 16]
- slot_position_material matching tier removed — 0-based index ≠ 1-based slot [Bug 11]
- normalize_color() lowercase handling fixed for 8-char hex [Bug 10]
- Negative RFID delta clamped to 0 (sensor glitch protection)
- _finish_wait_tick 15s timeout race eliminated by start-time 3MF fetch [Bug 6]

### Tests
- New test_consumption_engine.py: 27 pure unit tests, no mocking required
  12-scenario parametrized matrix covers all decision paths
- New test_print_lifecycle.py: print start/end lifecycle coverage
- New test_spoolman_writes.py: write execution with SpoolmanRecorder assertions
- Deleted test_print_usage_sync.py: superseded
- SpoolmanRecorder fixture added to conftest.py
- test_rfid_slot_uses_rfid_delta_not_3mf: permanent Bug 13 regression guard

### SDLC
- docs/agents/07_code_review_agent.md: v1.0 domain rules
- R2 Tester: test style invariants (parametrize, SpoolmanRecorder, docstrings)
- docs/agents/01_orchestrator_agent.md: ENGINE_CLEAN gate added
- docs/06_weight_tracking.md: rewritten for v1.0 architecture
- docs/01_architecture.md: lifecycle and decision tree diagrams added
