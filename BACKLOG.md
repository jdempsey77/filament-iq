## Filament IQ Backlog

## Closed by v1.0 Rewrite

| Item | Closed by |
|------|-----------|
| Bug 13: 3MF overrides RFID delta (inverted precedence) | consumption_engine.py RFID guard |
| Bug 11: slot_position_material index mismatch | threemf_parser.py tier removed |
| Bug 16: notification shows pre-write remaining | _send_notification rebuilt |
| Bug 6: _finish_wait_tick 15s timeout too short | mechanism deleted entirely |
| Bugs 14/15: depleted spool location not updated | _execute_writes depletion handling |
| MEDIUM #3: _finish_wait_tick double-fire risk | mechanism deleted |
| MEDIUM #11: _on_print_finish guards untested | test_print_lifecycle.py |
| MEDIUM #12: empty job_key bypasses dedup | _do_finish explicit guard |
| LOW #8: 0g RFID delta untested | test_consumption_engine.py |
| LOW #10: USAGE_RFID_DEPLETED_WARNING observability | rfid_delta_depleted method |

### In Progress

### High Priority
- [ ] Multi-spool runout split for single-color prints — when a non-RFID spool depletes mid-print and Bambu auto-swaps to a second slot to finish, the 3MF single-filament force match assigns all consumption to the finishing slot and writes zero to the depleted spool. Fix requires: (1) persist `active_times` dict to `active_print.json` alongside `trays_used` so it survives AppDaemon restarts, (2) when `len(trays_used) > 1` but 3MF has only one filament entry, split total 3MF weight proportionally by `active_times` across all active slots, (3) ensure depleted spool gets its share written even if it's no longer in the tray at finish time. Confirmed live: 2026-03-19 Desk Organizer print, slot 4 (spool 58) depleted and swapped to slot 3 (spool 65), all 160.72g written to slot 3, spool 58 shows 100g remaining in Spoolman but is actually empty.
- [ ] Reference Filament IQ dashboard — ship a ready-to-import Lovelace dashboard with slot cards, Filament Library, Spool Inventory, and Printer status. Raw YAML view exists in filament-iq repo (dashboard/filament_iq.yaml) but needs polish for easy import.
- [ ] Investigate 3MF_UNMATCHED for brief tray activations — tray tracking misses slots used for very short durations. Root cause: active_tray sensor polling interval vs actual extrusion time. Workaround in place (3MF-matched slots merged into active_slots).

### Medium Priority
- [x] NONRFID_EMPTY_TRAY_CLEAR sets location="Shelf" not "Empty" — _execute_writes now PATCHes location=Empty after successful depleted_nonrfid write. Prevents reconciler from returning depleted spools to Shelf candidate pool. (v1.0.3, commit 38a1aa3)
- [x] start_map fallback over-count — active slots now narrowed to trays_used & start_snapshot.keys() in _do_finish. Phantom writes from idle RFID slots with gauge drift eliminated. (v1.0.3, commit c731414)
- [x] min_consumption_g exempts 3MF methods — 3mf and 3mf_depleted bypass the 2g floor since slicer data is authoritative. RFID and depleted_nonrfid still subject to floor. (Audit Finding F, v1.0.1)
- [x] Print completion notifications broken — persistent_notification called with invalid notification_id key, silently failing since v1.0.0. Fixed by switching to notify/mobile_app_jd_pixel_10xl. (v1.0.2, commit 5fe38f1)
- [ ] Rehydrated start snapshot from fuel gauges undercounts delta — when HA helper recovery fails, start_snapshot rebuilt from current fuel gauges mid-print. Delta = current - end, not original_start - end. (Audit Finding 8b, 2026-03-14)
- [x] Spool_id snapshot at print start — active_print.json now persists trays_used (sorted list) and spool_id_snapshot (slot → spool_id). _load_active_print returns full dict. Both call sites restored on rehydrate. (v1.0.2, commit 286564a)
- [x] F1 fuel gauge near-empty tolerance — _read_fuel_gauge now accepts fg >= -5 (was >= 0). Near-empty RFID spools reporting -1 to -5g no longer fall back to AMS remaining. (v0.12.5)
- [ ] Manually correct spool 39 consumption in Spoolman (~144g from grid print 2026-03-11 00:08, remaining showed 98.4g which may be stale)
- [ ] Spoolman used_weight invariant break — RFID reconciler PATCHes remaining_weight directly, making `remaining + used != initial`. Benign for Filament IQ today but breaks any Spoolman consumer of used_weight. Track for future. (Skeptic Review, 2026-03-14)
- [x] Partial lot_sig matching for missing color_hex — _build_lot_sig returns partial sig type|filament_id| when color_hex absent. Lot_nr index prefix-match fallback + filament_id-only unenrolled filter added. Generic filament IDs (98/99) blocked. Single-candidate-only auto-bind guard. Note: color was never missing from reconciler — ha-bambulab exposes color as "color" attribute (#RRGGBBAA), reconciler reads correctly. Partial sig is a genuine safety net. (v1.0.6, commit beadf76)

### Low Priority
- [x] start_g >= 0 guard — RFID delta now accepts 0g start. (Audit Finding B, v0.12.5)
- [x] remaining_weight default=0 — depleted guard now fires on missing Spoolman field. (Audit Finding D, v0.12.5)
- [x] Auto-reconcile settling delay for new non-RFID spools — UNBOUND_NO_RFID_TAG_ALL_ZERO now schedules full reconcile after 90s (configurable nonrfid_settle_delay_s in apps.yaml). _settle_pending guard prevents duplicate timers. (v1.0.6, commit 47d180b)
- [ ] Monitor pre_weights accuracy on mid-print rehydration — when monitor restarts mid-print and rehydrates, pre_weights snapshot reflects mid-print Spoolman values not true print-start values. Weight deltas in artifact will be understated. Low impact since rare. (2026-03-18)
- [ ] 3MF fetch Phase 3 optimization — event loop blocking fixed (v1.0.4). Timing data shows connect=2.0s list=5.5s download=4.3s total=11.9s. Direct RETR optimization would save ~5.5s (skip listing). Deferred — no longer blocks prints since fetch runs in background thread.
- [x] Delete remaining obsolete HA helpers: input_number.filament_iq_start/end_slot_N_g — helpers deleted from configuration.yaml. test_scenario_1, test_scenario_4, test_clear_binding, p1s_debug_force_finish_path scripts removed from scripts.yaml. input_boolean.filament_iq_debug_finish_trigger and associated automations removed. HA config valid, core restarted clean. (v1.0.3)
- [x] Change ACTIVE_PRINT_PERSISTED log level from DEBUG to INFO for visibility in normal monitoring (48f18ff)
- [x] Investigate stale seen_job_keys.json at /addon_configs/a0d7b954_appdaemon/apps/data/ — confirmed correct path at apps/filament_iq/data/seen_job_keys.json. No stale copy exists. Resolved by v1.0 rewrite.
- [x] NONRFID_UNENROLLED_MATCH writes skipped on safety_poll — status_only=True gate incorrectly blocked spool_id helper write, lot_nr enrollment, and Spoolman location update on safety poll cycles (every 5 min). Only non-status-only triggers (manual reconcile, tray state change) completed the bind. Fixed by removing status_only gate from deterministic unique match path. Also fixed _set_helper AppDaemon cache bug (plain get_state → _get_helper_state) and added diagnostic logging for all helper entities. (v1.0.4, commit afbf356)
- [x] Print duration shows "unknown" after AppDaemon restart — _print_start_time was in-memory only. active_print.json already persisted print_start_time but _load_active_print did not restore it. Fixed by restoring print_start_time on rehydrate. (v1.0.4)
- [x] FTPS 3MF fetch blocks AppDaemon event loop — 11-15s synchronous FTPS fetch ran on main event loop thread via run_in(), blocking all callbacks. Moved to background daemon thread with result delivery back to event loop via run_in(..., 0). Job key staleness check added. Timing instrumentation added: 3MF_TIMING log line with connect/list/download/parse breakdown. First timing data: connect=2.0s list=5.5s download=4.3s total=11.9s files=77. (v1.0.4)
- [x] Verify all 3 lifecycle phases in production — 11+ prints validated clean through v1.0.3. RFID delta, 3MF exact match, and depleted_nonrfid all confirmed working in production. (v1.0.4)


### Done
- [x] Depleted non-RFID spool detection — automatic consumption write when non-RFID slot depletes mid-print. Detects via trays_used + tray_state=Empty + tray_seconds guard. Consumes Spoolman remaining_weight to zero. (v0.13.0)
- [x] Reconciler status helper — writes human-readable status to input_text.filament_iq_reconciler_status after each cycle (ok/warn/paused + counts + reason + time). (v0.12.6)
- [x] 3MF single-filament force match — when trays_used has exactly one slot and 3MF has exactly one filament, match directly regardless of color/index. Fixes slicer index vs physical slot mismatch. (v0.12.6)
- [x] RFID reconciler hardening — print_active re-defer, tray_weight sanity bounds (50-2000g), 5g minimum delta threshold. 1226 passing. (v0.12.4, Skeptic Review)
- [x] 16 E2E pipeline mock tests — full _handle_usage_event decision matrix (8 scenarios) + 6 audit finding tests. 1215 passing. 919b484 (v0.12.3)
- [x] RFID reconciler deferred 60s post-print — prevents stale MQTT sensor from undoing consumption writes. Synchronous reconcile read cached pre-print RFID remain% and patched Spoolman back. bb4d47b (v0.12.2, Audit Finding E — HIGH, now fixed)
- [x] Rehydrate job_key from HA helper — reads full timestamp-suffixed key from input_text helper instead of re-deriving from task_name. Disk fallback in _finish_wait_tick as safety net. 474eebb (v0.12.1, RT #2 rehydrate fix). Note: original RT #2 described persisting trays_used; actual fix was reading _job_key from HA helper — active_print.json already persisted threemf_data, the bug was key mismatch on load.
- [x] Reconciler print-active freeze — full reconcile skip during active prints, 24h watchdog, post-print reconcile trigger, USAGE_SKIP data loss warning. 1194 tests. (v0.12.0)
- [x] Coverage push to 75% — 1177 tests, +451 new, per-module: base 100%, threemf 94%, dropdown 87%, weight 83%, guard 81%, usage 73%, reconcile 71% (v0.11.2, RT #3)
- [x] Hold slot bindings during active prints — reconciler skips re-evaluation of bound slots while print_active (v0.11.1, F4 / RT #6)
- [x] Persist active print state to disk — active_print.json survives AppDaemon restart (v0.11.0, F3 / RT #2, persistence layer — rehydrate key fix in v0.12.1)
- [x] Fuel gauge availability templates — sensors show "unavailable" instead of 0 when data missing (v0.11.0, F1)
- [x] Fix end_snapshot 0.0 regression — end snapshot reads fuel gauges correctly (v0.11.0)
- [x] Phase 1: Print start lifecycle in AppDaemon (job key, start snapshot, tray seeding)
- [x] Phase 2: Print finish lifecycle in AppDaemon (end snapshot, usage processing, dedup)
- [x] Phase 3: Debug logging, swap detection, rehydrate mutex, pause-state fix
- [x] Disable 7 HA lifecycle automations (A-G) — replaced by Phases 1-3
- [x] 3MF matching: lot_nr color fallback for third-party filament color mismatches
- [x] 3MF matching: unmatched consumption logged and skipped (pool_g estimation removed)
- [x] 3MF matching: all bound slots as candidates, not just trays_used
- [x] 3MF matching: 3MF-matched slots merged into active processing loop
- [x] Suppress full reconcile on manual assign (fffe84b)
- [x] Priority 1-7 from system audit
- [x] 3MF fetch pipeline (URL encoding, retry, multi-dir, unicode matching)
- [x] Non-RFID matching fix (nonrfid_enabled entity rename)
- [x] Reboot false-finish guard (to: filter + job-key dedup)
- [x] Double-fire fix (remove idle from trigger, stable job key)
- [x] Access code persistence (secrets.yaml + startup automation)
- [x] Auto-enroll lot_nr on manual assign
- [x] filament_iq/ package as primary runtime (Priority 7)
- [x] Legacy AppDaemon files retired
- [x] Deploy script updated for filament_iq/ package
- [x] CLAUDE.md project structure rules
- [x] Sync Color on Bind — sync_color_hex event field + handler, force re-enrollment, RFID guard, 32 new tests (v0.7.0)
- [x] Fix test_ams_rfid_reconcile import failure — CI skip guard + proper filament_iq module imports
- [x] Bind dialog UX — wide popup (size: wide), dropdown syncs to bound spool, no all-slots flicker on bind
- [x] Config-driven FilamentIQBase — no hardcoded serials/IPs in app code
- [x] Rename SLOT_ASSIGNED_NO_LOT_SIG → SLOT_ASSIGNED_LOT_SIG_EXISTS log message
- [x] Reconciler performance fix — 26s → ~3s (cached spool list, removed equality bypass) (v0.7.1)
- [x] Delete 6 obsolete HA helpers (end_json, last_processed_job_key, init_seed_debug, last_tray_entity, last_print_status_transition, finish_automation_checkpoint) — start_json and active_job_key kept (active in AppDaemon)
- [x] Remove pool_g / time-weighted / equal_split estimation — two write paths only (RFID delta, 3MF match). Under-count acceptable, phantom charges eliminated.
- [x] Phantom consumption fix — failed print guard, write-ahead dedup, smart empty guard
- [x] Native FTPS fetch via ftplib.FTP_TLS (implicit TLS, ~40% faster)
- [x] slot_position_material match tier (2.75) in 3MF matcher
- [x] Scoped unbound-slot warning to active trays only
- [x] Batch Spoolman fetch in usage pipeline (~12 HTTP calls → 1)
- [x] 3MF fetch race guard — wait up to 15s for data before processing finish

### Releases

| Version | Date | Summary |
|---------|------|---------|
| v1.0.6 | 2026-03-18 | Partial lot_sig matching + auto-reconcile settling delay |
| v1.0.5 | 2026-03-18 | Monitor added to repo (config-driven, deploy script) + rehydration job mismatch fix |
| v1.0.4 | 2026-03-18 | NONRFID safety_poll bind fix + FTPS fetch off event loop + print duration rehydrate |
| v1.0.3 | 2026-03-17 | start_map phantom write fix + depleted_nonrfid sets location=Empty + HA helper cleanup |
| v1.0.2 | 2026-03-17 | Print notifications fixed (mobile_app) + active_print.json spool_id snapshot |
| v1.0.0 | 2026-03-15 | Full pipeline rewrite: consumption_engine.py, five-phase architecture, RFID-delta-wins, threemf_parser bug fixes, SpoolmanRecorder test infrastructure, print_history persistence |
| v0.12.6 | 2026-03-14 | Reconciler status helper + 3MF single-filament force match |
| v0.12.5 | 2026-03-14 | Three audit fixes (start_g guard, depleted default, fuel gauge tolerance) |
| v0.12.4 | 2026-03-14 | RFID reconciler hardening (3 guards) |
| v0.12.3 | 2026-03-14 | 16 E2E pipeline tests from measurement audit |
| v0.12.2 | 2026-03-14 | Defer RFID reconciler 60s post-print |
| v0.12.1 | 2026-03-13 | Rehydrate job_key from HA helper |
| v0.12.0 | 2026-03-13 | Reconciler print-active freeze |
