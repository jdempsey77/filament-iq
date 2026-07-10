# Changelog

## [1.9.9] — 2026-07-09

### Fixed

- **"+ Add filament" 422 with no visible error** — Spoolman's `POST
  /api/v1/filament` requires `density` (confirmed via its OpenAPI schema:
  `FilamentParameters.required = [density, diameter]`), but the quick-add
  form (`FilamentAddRow` in `FilamentsTab.jsx`) never collected or sent it.
  Every submission was rejected with a 422 (`density: Field required`).
  Added a required Density (g/cm³) input to the form, disabled the Create
  button until it's filled, and included `density` in the create payload.
- **Silent failure on create errors** — `handleCreate` only had a
  `try/finally`, so a rejected `ProxyError` (carrying Spoolman's real
  validation detail on `.body`) became an unhandled promise rejection with
  nothing shown to the user beyond a generic `Spoolman proxy error: 422` in
  the console. Added a `catch` that formats Spoolman's FastAPI/Pydantic
  `detail` array into a readable message (e.g. `density: Field required`)
  and surfaces it via the existing `.fiq-toast` error pattern (already used
  in `SlotsTab.jsx`/`SpoolsTab.jsx`), auto-dismissing after 5s. This
  generically catches any future 422 from this form, not just density.

## [1.9.8] — 2026-05-22

### Bug Fixes (AppDaemon — `ams_print_usage_sync`)

- **[A2] Stale 3MF cache cleared on print end** — `_threemf_data`,
  `_threemf_source_mtime`, and `_threemf_from_disk_restore` are now reset in
  `_on_print_end()`. Previously these fields were never cleared between prints,
  causing the `3MF_CACHE_ALREADY_SET` guard in `_fetch_3mf_background` to
  silently reuse the prior print's 3MF data for any subsequent print with the
  same filename — resulting in silent `USAGE_NO_EVIDENCE` for all non-RFID
  slots.
- **[A2] `threemf_source_mtime` persisted through `active_print.json`** —
  `_persist_active_print` now writes `threemf_source_mtime` to disk and both
  restore paths (`_rehydrate_print_state`, `_on_print_finish`) read it back.
  Previously both paths hardcoded `0.0`, which always triggered
  `3MF_STALE_FORCE` and discarded valid cached 3MF data after an AppDaemon
  restart mid-print.
- **[B] Spurious HA startup event no longer clobbers spool snapshot** —
  `_on_print_status_change` now returns early (logs `PRINT_STATUS_SPURIOUS_SKIP`)
  when a `running`/`printing` transition arrives while `_job_key` is already
  set. Previously, HA's synthetic `old=None` event after AppDaemon restart
  triggered the reset block, clearing `_spool_id_snapshot` and making
  `_on_print_start` a no-op (job_key guard), leaving spool tracking empty for
  the rest of the print.
- **[B] Empty snapshot not committed mid-print** — `_persist_active_print`
  now skips disk write with `PERSIST_SNAPSHOT_SKIPPED` when
  `_spool_id_snapshot` is empty during an active print. Prevents a second
  rehydration from reading `spool_ids={}` and producing `USAGE_SKIP
  reason=NO_ACTIVE_SLOTS`.

### Added

- **[A1] Mobile push for `NO_3MF_AND_TRAY_NOT_EMPTY`** — when `_do_finish`
  encounters `USAGE_NO_EVIDENCE reason=NO_3MF_AND_TRAY_NOT_EMPTY` on a bound
  non-RFID slot, a push notification is sent via the configured
  `notify_service`. The alert includes spool name, slot number, and job key so
  the user can make a manual Spoolman correction. Silent failures on
  `call_service` are caught and logged as `USAGE_NO_EVIDENCE_NOTIFY_FAILED`
  without aborting the decisions loop.

### Tests

+9 new tests in `test_ams_print_usage_sync.py` covering all three fixes.
Full suite: 1479 passed.

---

## [1.9.3] — 2026-05-17

### Fixes
- **fiq-card flex layout** — add `display: flex; flex-direction: column` to `.fiq-card`
  so subnav, stats, and body stack correctly as a column.

---

## [1.9.2] — 2026-05-17

### Changes
- **Sub-nav moved to top** — Slots / Spools / Filaments / Vendors tab bar now renders
  as its own full-width row immediately below the card header, above the stats row.
  Tabs stretch to fill the row evenly.
- **SpoolsTab filters confirmed** — location filter (All locations / In AMS / Shelf /
  New / Unassigned), vendor filter, and material filter all present and working.

---

## [1.9.1] — 2026-05-17

### Changes
- **SlotsTab horizontal rows** — replaced square `SlotCard` grid with `SlotRow`
  horizontal component for all sections (AMS 2 Pro, HT Units, External).
  Each row: 44×52px color swatch · brand + ID + active badge · material bold ·
  color name · fuel bar · grams + chevron. Active slot gets blue tint background.
- **HT Units sub-headers** — each HT unit gets a labeled sub-header row (HT1/HT2/HT3)
  showing humidity + temp, or an amber drying badge (`♨️ temp°C · time`) when drying.
- **Section background** — cards use `#2c2c2e` (was `#1c1c1e`) for better contrast.

---

## [1.8.6] — 2026-05-17

### Fixes
- **MW badge real tap_action** — replaced `window.open()` in `custom_fields` HTML
  (blocked by HA shadow DOM) with a standalone `custom:button-card` using
  `tap_action: url`. Badge appears just below the hero thumbnail, left-aligned.
- **Tab bar config error + sticky removal** — removed `position: fixed` from the
  tab bar grid card_mod (HA layout containers prevent true fixed positioning).
  Removed 64px spacer card. Tab bar now sits at bottom of page content, full-bleed
  via `margin: 0 -16px`.
- **AMS 2 Pro humidity/temp contrast** — `sectionSubStyle` color bumped from
  `#636366` to `#8e8e93` to match HT bay humidity text visibility.

---

## [1.8.5] — 2026-05-17

### Fixes
- **Tab bar sticky** — position:fixed + 64px spacer card approach (partially worked).
- **MW badge click** — onclick in custom_fields HTML (partially worked, blocked in some HA contexts).
- Various minor dashboard tweaks.

---

## [1.8.4] — 2026-05-17

### Fixes
- **SpoolsTab filter row overflow** — Bind Slot button moved to its own full-width
  row above the filter row. Toolbar now wraps: search input takes the full first
  line (`flex: 1 1 100%`), dropdowns share the second line (`flex: 1 1 auto`).
  Eliminates horizontal overflow and scrollbar on mobile viewports.

---

## [1.8.3] — 2026-05-17

### Features
- **Slots tab card grid** — `SlotsSegment` redesigned from list rows to a three-section
  card grid: AMS 2 Pro (4 cards in flex row), HT Units (3 bays side by side with
  per-bay humidity label), and External (standalone slot 8). Each slot card shows a
  32×40 color swatch, brand + spool ID, material (bold), color name, grams remaining
  (red if <20%), and a 3px fuel bar (color-matched, red if warn). Active slot gets a
  blue border (`#0a84ff`). Needs-action states show in red. `SlotPopup` unchanged.

---

## [1.8.2] — 2026-05-17

### Features
- **Slots tab in filament-iq-manager** — `SlotsSegment` and `SlotPopup` ported
  from `PrinterDashboardCard` into a standalone `SlotsTab` component. The
  `filament-iq-manager` card now has a Slots tab (first in the tab bar) showing
  all AMS units with per-slot status, weight bars, and a tap-to-assign popup.
- **`initial_tab` config prop** — Card accepts `initial_tab: slots` (or any tab
  id) to mount on a specific tab. Used by the dashboard Slots tab to open the
  card directly on the Slots view.
- **`PrinterDashboardCard` `initial_segment` reverted** — No longer needed since
  the Slots tab embeds `filament-iq-manager` instead of `printer-dashboard`.

---

## [1.8.1] — 2026-05-17

### Features
- **`initial_segment` config prop** — `custom:printer-dashboard` now accepts
  `initial_segment: slots` (or any segment key) to mount directly on a specific
  segment. When set, the `SegBar` is hidden (the host dashboard's tab bar is
  the navigation mechanism). Enables the Slots tab in the 3D Printer dashboard
  to embed the card locked to the slots segment.

---

## [1.8.0] — 2026-05-16

### New feature: Nav intent support

The card now supports external navigation via a Home Assistant helper entity.
When `input_text.filament_iq_nav_intent` is set to `"spool:N"` (where N is a
Spoolman spool ID) before the card mounts, the card will pre-open the edit
panel for that spool.

**Setup required (opt-in):**
1. Add `input_text.filament_iq_nav_intent` helper to `configuration.yaml`
2. Create `script.slot_tap_to_filament_iq` in `scripts.yaml`
3. Update slot button-card `tap_action` to call the script

**Payload format:** `"type:value"` — currently supported: `"spool:N"`.
Reserved for future: `"slot:N"`, `"action:add"`.

**Behavior notes:**
- Intent is consumed once at card mount and cleared immediately
- If the card remains mounted (rare, HA version dependent), subsequent
  intents will not be processed until the card remounts
- Last intent wins on rapid consecutive taps
- Absent, empty, unparseable, or zero-value intent → card opens normally, no error

---

## [1.7.6] — 2026-05-10

### Bug Fixes
- **[A1] Pre-write depletion guard** — `_execute_writes` now fetches
  current `remaining_weight` before writing; skips with
  `USAGE_DEPLETED_SKIP` if `<= 0`; fails open on fetch error to avoid
  blocking legitimate writes.
- **[A2] Slot helper cleared on depletion** — `input_text.ams_slot_N_spool_id`
  unconditionally cleared when a spool depletes; mobile push notification
  fired prompting manual rebind; notify exceptions caught and logged
  without aborting the decisions loop.
- **[B1] lot_nr identity index split** — `lotnr_to_all_spools` (includes
  Empty-location spools) used for chip identity lookup;
  `lotnr_to_spools` (excludes Empty) retained for candidate selection
  only; `RFID_CHIP_BELONGS_TO_DEPLETED_SPOOL` logged with unbound
  reason set and push notification fired when chip matches a depleted
  spool.
- **[B2] lot_nr uniqueness enforcement** — `_enroll_lot_nr` blocks
  enrollment when incoming RFID UUID lot_nr already exists on another
  spool; `LOT_NR_DUPLICATE_BLOCKED` logged; non-RFID pipe-sigs exempt
  (shared by design across identical rolls).

### Root cause
Physical inventory audit revealed phantom overconsumption writes
accumulating beyond spool capacity, and RFID chip re-enrollment aliasing
multiple spool records to the same physical chip. Both bugs traced to
missing invariant enforcement in the write and enrollment paths.

### Tests
+6 new tests across `test_spoolman_writes.py` and
`test_ams_rfid_reconcile.py`. Full suite: 1417 passed.

### Added
- **Makerworld link from hero card** — thumbnail and print title in the printer
  hero card are now clickable links. For prints with Makerworld metadata embedded
  in the cached 3MF (`3D/3dmodel.model`), the link resolves to the direct model
  page via the DSM ID in the Description field. Falls back to a Makerworld search
  URL using the model title when no DSM ID is present.
- **`sensor.filament_iq_makerworld_url`** — new HA sensor written by
  `ams_print_usage_sync` at cache hit, FTPS success, and rehydration. Cleared to
  `"unknown"` on print start and print end.
- **`sensor.filament_iq_model_title`** — new HA sensor with the model title from
  3MF metadata.
- **`sensor.filament_iq_threemf_filename`** — new HA sensor exposing the raw
  3MF cache filename for card consumption.
- **`parse_3mf_metadata()`** — new utility in `threemf_parser.py` that reads
  `3D/3dmodel.model` from a cached 3MF zip and extracts Title, Designer, and
  Makerworld URL.

## [1.2.2] — 2026-05-09 (card)

### Changed
- **Makerworld link on hero card** — thumbnail and title now link to the
  Makerworld model URL served from `sensor.filament_iq_makerworld_url`.

## [1.2.1] — 2026-04-23 (card)

### Fixed
- **SlotBindRow dropdown missing spools** — `getBindableSpools` now uses
  `.filter()` so iteration never exits early. A spool with `location: Empty`
  at any index no longer silently drops all spools after it from the bind
  dropdown.
- **FILAMENT_IQ_SLOT_ASSIGNED missing from SlotBindRow path** — `onBind`
  callback now fires the event after the Spoolman PATCH, matching the
  `SpoolEditPanel` behavior. Reconciler updates `input_text.ams_slot_N_spool_id`
  immediately on slot-first bind instead of waiting for the next reconcile cycle.

## [1.7.5] — 2026-04-11

### Added
- **Per-slot skip reason labels in print-finish notification** — skipped slots
  now show the specific reason from `SlotDecision.skip_reason` instead of a
  generic "No data" line. New `_SKIP_REASON_LABELS` map covers every
  `consumption_engine` skip_reason: `NO_3MF_AND_TRAY_NOT_EMPTY` → "No slicer
  data", `BELOW_MIN` → "Below minimum threshold", `SANITY_CAP` → "Estimate
  exceeded sanity limit", `DATA_LOSS` → "Sensor data unavailable",
  `DEPLETED_BUT_NO_SPOOLMAN_REMAINING` → "Depleted, no Spoolman record",
  `UNKNOWN` → "Unknown reason". Unmapped values fall back to "No data".
  A regression test (`test_consumption_engine_skip_reason_keys_match_label_map`)
  fails if a new skip_reason is added to the engine without a label.
- **Mobile push for ambiguous reconciler outcomes** — new
  `_notify_mobile_match_needed(slot, reason)` helper fires a
  `notify/mobile_app_YOUR_DEVICE` push so the user is alerted in
  real time when a non-RFID slot needs manual binding. Title: "Filament IQ
  — Spool Match Needed". Message includes slot number, reason detail, and
  a "Open Spoolman to assign manually" call to action. Notify service is
  configurable via the `notify_service` apps.yaml arg. Wired into three
  previously-silent terminal decision points in `_run_reconcile_inner`:
  1. `_notify_nonrfid_needs_action` (used at lines 1817 / 1858) —
     non-RFID slot lacks an unambiguous Shelf match.
  2. Single-candidate path where the resolved spool is already active in
     another slot (line 1382) — reason: "Spool active in another slot;
     cannot auto-assign."
  3. Multi-candidate `lot_nr` ambiguity where tie-break did not resolve
     (line 1500) — reason: "Multiple candidates found; tie-break did not
     resolve to one winner."

### Fixed
- **Pre-existing test failures unblocked**:
  - `test_notification_shows_post_write_remaining_not_pre_write`: test
    harness was missing `self.notify_service`, causing `_send_notification`
    to AttributeError-and-swallow before any notify call landed. Initialized
    in `_TestableUsageSync.__init__`.
  - `TestCheckUnboundTrays::test_unbound_slot_warns` and
    `test_unbound_with_notify_target`: hardcoded the stale service name
    `mobile_app_YOUR_DEVICE`; updated to current default
    `mobile_app_YOUR_DEVICE`.
  - `test_3mf_fetch_runs_in_thread`: written before v1.7.0 cache-retry
    logic was added; `attempt=1` now early-returns through the cache path
    without spawning a thread. Test now calls with `attempt=2`.
  - `tests/conftest.py` `collect_ignore_glob` was pinned to a stale path
    (`appdaemon/apps/filament_iq/`, the home_assistant deployment layout),
    which silently excluded ALL `test_ams_*.py`, `test_threemf_*.py`, and
    `test_consumption_engine.py` from `pytest tests/` directory-level runs
    in this standalone repo. Suite count was reporting ~294 instead of
    ~1357. Fixed to accept either `apps/filament_iq/` (standalone) or
    `appdaemon/apps/filament_iq/` (deployed) layouts.
  - `TestAppendEvidenceReal::test_enabled_writes` and
    `TestAppendEvidenceLineReal::test_enabled_writes` (newly surfaced by
    the conftest fix): tests pre-dated the rotating-file logger refactor
    of `_append_evidence`. Now call `_ensure_evidence_path_writable()`
    in setup so `_evidence_logger` is initialized.

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
