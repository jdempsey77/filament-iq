## Filament IQ Backlog

### In Progress
- [ ] Verify all 3 lifecycle phases in production — Phases 1-3 coded and enabled, 7 HA automations disabled, monitoring for issues

### High Priority
- [ ] Reference Filament IQ dashboard — ship a ready-to-import Lovelace dashboard with slot cards, Filament Library, Spool Inventory, and Printer status. Raw YAML view exists in filament-iq repo (dashboard/filament_iq.yaml) but needs polish for easy import.
- [ ] Investigate 3MF_UNMATCHED for brief tray activations — tray tracking misses slots used for very short durations. Root cause: active_tray sensor polling interval vs actual extrusion time. Workaround in place (3MF-matched slots merged into active_slots).

### Medium Priority
- [ ] start_map fallback over-count — if trays_used empty, active_slots falls back to all start_map keys (all 6 slots). Idle RFID slots with gauge drift could produce phantom writes. Narrow trigger. Lines 319-332, both internal tracking and event data empty. (Audit Finding A, 2026-03-14)
- [ ] min_consumption_g discards valid small 3MF matches — slicer-exact 1.5g purge segment silently skipped by 2g minimum. Lines 439-446, filter applies to all methods including 3MF. Consider lowering or exempting 3MF path. (Audit Finding F, 2026-03-14)
- [ ] Rehydrated start snapshot from fuel gauges undercounts delta — when HA helper recovery fails, start_snapshot rebuilt from current fuel gauges mid-print. Delta = current - end, not original_start - end. (Audit Finding 8b, 2026-03-14)
- [ ] Spool_id snapshot at print start — snapshot spool_ids alongside fuel gauge in _start_snapshot or parallel _spool_id_snapshot. Usage sync reads from snapshot at finish instead of live helpers. Eliminates reconciler/usage sync coupling. Principal identified as correct long-term fix (option d).
- [ ] F1 availability template tolerance — RFID spools report remain=-1 to -2 when nearly depleted. Consider `remain >= -5 OR tray_weight > 0` to avoid falling to AMS remaining for valid near-empty RFID reads.
- [ ] Manually correct spool 39 consumption in Spoolman (~144g from grid print 2026-03-11 00:08, remaining showed 98.4g which may be stale)

### Low Priority
- [ ] Manual correction: spool 38 remaining weight in Spoolman (~110g lost from Gridfinity print 2026-03-13, slot 4 depletion incident)
- [ ] start_g > 0 guard should be >= 0 — line 407, RFID slot with exactly 0g start excluded from delta path. No practical impact (0g start yields 0g delta, caught by min_consumption_g). (Audit Finding B, 2026-03-14)
- [ ] remaining_weight default=1 masks depletion detection — line 460, if Spoolman omits remaining_weight in response, default 1 means depleted guard does not fire. Should default to 0. (Audit Finding D, 2026-03-14)
- [ ] 3MF fetch takes 11-15s consistently, triggering "Excessive time spent" warnings — investigate if FTPS listing of 154 files is the bottleneck. Could skip listing and download by constructed filename directly.
- [ ] Delete remaining obsolete HA helpers: input_number.filament_iq_start/end_slot_N_g (deferred — active test scripts reference them)
- [x] Change ACTIVE_PRINT_PERSISTED log level from DEBUG to INFO for visibility in normal monitoring (48f18ff)
- [ ] Investigate stale seen_job_keys.json at /addon_configs/a0d7b954_appdaemon/apps/data/ (should only exist at filament_iq/data/)
- [ ] Manually correct spool 52 consumption in Spoolman (~143g from grid print 2026-03-12 15:03)

### Done
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
