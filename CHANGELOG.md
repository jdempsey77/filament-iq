# Changelog

## [1.7.4] — 2026-04-11

### Fixed
- **Print duration reset on transient HA status blips mid-print** —
  `_on_print_status_change` re-fires `_on_print_start()` whenever the printer
  status leaves the in-print set (`running`, `printing`, `pause`, `paused`)
  and returns. Bambu/HA emit transient `prepare`, `unknown`, `unavailable`,
  or `idle` states during integration reloads and sensor dropouts, each of
  which was resetting `_print_start_time` and reissuing `_job_key`. A 2h+
  print was reported as 14m because the baseline was reset ~14m before the
  finish event. Fix: `_print_start_time` and `_job_key` are now set-once per
  logical print — `_on_print_start` is a no-op if either is already
  populated, and rehydration paths refuse to overwrite a live in-memory
  value with a stale disk value. Regression tests:
  `test_transient_status_blip_preserves_print_start_time`,
  `test_rehydrate_fills_empty_print_start_time`,
  `test_rehydrate_does_not_overwrite_live_print_start_time`.

## [1.7.3] — 2026-03-28

### Fixed
- **Non-authoritative color sync guard** — `_sync_filament_color_on_bind`
  was patching Spoolman filament color_hex with `000000` when AMS tray
  reported a non-authoritative color (e.g. dark spools read as black).
  Added guard to skip PATCH when `target_color` is in
  `TRAY_HEX_NON_AUTHORITATIVE`. Prevents filament record corruption
  and downstream RFID enrollment mismatches.

## [1.7.2] — 2026-03-28

### Fixed
- **Print duration resets on pause/resume** — `_on_print_status_change` was
  firing `_on_print_start()` on `paused → running` transitions, resetting
  `_print_start_time`, `_job_key`, and `_start_snapshot`. Duration was
  measured only from resume → end, not start → end. Fix: added `"pause"` and
  `"paused"` to the `old not in (...)` guard on the start branch, mirroring
  the existing end condition guard. (Bug observed: 46m reported for a multi-
  hour print after spool runout pause.)

### Added
- **Add Spool quantity field** — new stepper input (1–10) in the Add Spool
  dialog. Creates one Spoolman spool per count with identical parameters.
  Print label fires for each spool when checkbox is checked.

## [1.7.1] — 2026-03-26

### Fixed
- Wrong container path for bambulab cache — AppDaemon addon maps
  `/config` to `/homeassistant/` inside the container. Every cache
  miss since v1.7.0 was `file_not_found` due to this path bug.
  Corrected to `/homeassistant/www/media/ha-bambulab/{serial}/prints/cache/`.
- Cache not attempted on rehydration — `_try_cache_3mf()` now called
  at both rehydration sites when `_threemf_data` is None.
  Log tokens: `3MF_CACHE_REHYDRATE_HIT`, `3MF_CACHE_REHYDRATE_MISS`.

### Changed
- Cache retry before FTPS — on cache miss at t=10, one retry is
  scheduled at t=30 before FTPS fires. Uses `run_in`, never
  `time.sleep`. Each FTPS retry also checks cache first (v1.7.0).

## [1.7.0] — 2026-03-26

### Added
- ha-bambulab cache path as primary 3MF source — reads
  `slice_info.config` directly from ha-bambulab's local cache before
  attempting FTPS. Eliminates data loss from FTPS 530 errors on
  non-RFID slots during active prints. FTPS unchanged as fallback.
- Task name cross-validation prevents stale `gcode_file_downloaded`
  entity from causing wrong cache file reads.
- mtime guard uses print start time (not fixed window) — valid for
  prints of any length.
- Double-write guard: FTPS skipped if cache already populated.
- `bambulab_cache_path` and `gcode_file_entity` config keys.
- `parse_slice_info_file()` in `threemf_parser.py`.
- Log tokens: `3MF_CACHE_HIT`, `3MF_CACHE_MISS`, `3MF_CACHE_ERROR`,
  `3MF_CACHE_ALREADY_SET`.

## [1.6.3] — 2026-03-26

### Fixed
- SNAPSHOT_IMPLAUSIBLE false positive for non-RFID slots — the
  implausibility check (designed for RFID fuel gauges) incorrectly
  fired for non-RFID spools whose Spoolman fallback returns 0.0
  during startup. Guard now skips the check for non-RFID slots.
  Confirmed data loss: spool 76, ~400g, 2026-03-25. (Bug 16)
- Same guard applied to rehydration path.

### Tests
- test_snapshot_nonrfid_slot_fuel_gauge_unavailable_not_implausible
- test_snapshot_rfid_slot_fuel_gauge_zero_is_implausible (regression guard)

## [1.6.2] — 2026-03-25

### Fixed
- Never-initialized slot helpers show `unknown · unknown` on dashboard —
  reconciler tray-empty path now writes `UNBOUND_TRAY_EMPTY` to
  `unbound_reason` when the current value is `unknown`/empty/unavailable.
  Self-heals on every reconcile cycle. Fixes slot 7 / HT3 on first boot
  and any future new slots. (Bug 15)
- Startup debug loop hardcoded to `range(1, 7)` — now uses
  `sorted(self._tray_entity_by_slot.keys())` so new slots appear in
  startup logs automatically.

## [1.6.1] — 2026-03-25

### Added
- AMS HT3 support (ams_index 130, slot 7) — base.py unit registration,
  deprecated location map, monitor.py slot mapping, Lovelace card location
  tables. HA helper/automation/script/dashboard changes are in the
  home_assistant repo.
- `docs/adding-ams-unit.md` — runbook for adding any new AMS unit.
  HT3 is the worked example.

### Tests
- test_active_tray_ht3 — ams_index=130 maps to slot 7
- test_deprecated_location_mapping — AMS2_HT_Slot3 → AMS130_Slot1
- test_get_all_slots — updated to include slot 7

## [1.6.0] — 2026-03-25

### Fixed
- **Runout split finishing slot data loss on rehydrated prints** — `finishing_share`
  was silently discarded by the RFID suppression guard in `_collect_print_inputs`
  when the finishing spool had a valid RFID tag. The RFID delta on a rehydrated
  print is stale (start_g ≈ end_g), causing BELOW_MIN to drop the slot to
  `no_evidence`. `finishing_share` is now authoritative for runout split methods
  regardless of RFID tag presence. Confirmed data loss: 149.38g, spool_id=72,
  slot=3, 2026-03-25. (Bug 14)

### Changed
- RFID suppression rule refined: `_RUNOUT_SPLIT_METHODS` frozenset exempts
  `runout_split` and `runout_split_depleted` from the "RFID delta always wins"
  principle. RFID delta remains authoritative for all other scenarios.

### Tests
- 1 new regression test for runout split with RFID finishing slot on rehydrated
  print (283 total)

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
