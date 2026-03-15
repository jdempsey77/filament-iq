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
- [ ] Verify all 3 lifecycle phases in production — Phases 1-3 coded and enabled, 7 HA automations disabled, monitoring for issues

### High Priority
- [ ] Reference Filament IQ dashboard — ship a ready-to-import Lovelace dashboard with slot cards, Filament Library, Spool Inventory, and Printer status. Raw YAML view exists in filament-iq repo (dashboard/filament_iq.yaml) but needs polish for easy import.
- [ ] Investigate 3MF_UNMATCHED for brief tray activations — tray tracking misses slots used for very short durations. Root cause: active_tray sensor polling interval vs actual extrusion time. Workaround in place (3MF-matched slots merged into active_slots).

### Medium Priority
- [ ] NONRFID_EMPTY_TRAY_CLEAR sets location="Shelf" not "Empty" — reconciler moves depleted non-RFID spool to Shelf instead of Empty because it has no consumption context. Separate fix from depleted detection. (ANALYZE 2026-03-14)
- [ ] start_map fallback over-count — if trays_used empty, active_slots falls back to all start_map keys (all 6 slots). Idle RFID slots with gauge drift could produce phantom writes. Narrow trigger. Lines 319-332, both internal tracking and event data empty. (Audit Finding A, 2026-03-14)
- [ ] min_consumption_g discards valid small 3MF matches — slicer-exact 1.5g purge segment silently skipped by 2g minimum. Lines 439-446, filter applies to all methods including 3MF. Consider lowering or exempting 3MF path. (Audit Finding F, 2026-03-14)
- [ ] Rehydrated start snapshot from fuel gauges undercounts delta — when HA helper recovery fails, start_snapshot rebuilt from current fuel gauges mid-print. Delta = current - end, not original_start - end. (Audit Finding 8b, 2026-03-14)
- [ ] Spool_id snapshot at print start — snapshot spool_ids alongside fuel gauge in _start_snapshot or parallel _spool_id_snapshot. Usage sync reads from snapshot at finish instead of live helpers. Eliminates reconciler/usage sync coupling. Principal identified as correct long-term fix (option d).
- [x] F1 fuel gauge near-empty tolerance — _read_fuel_gauge now accepts fg >= -5 (was >= 0). Near-empty RFID spools reporting -1 to -5g no longer fall back to AMS remaining. (v0.12.5)
- [ ] Manually correct spool 39 consumption in Spoolman (~144g from grid print 2026-03-11 00:08, remaining showed 98.4g which may be stale)
- [ ] Spoolman used_weight invariant break — RFID reconciler PATCHes remaining_weight directly, making `remaining + used != initial`. Benign for Filament IQ today but breaks any Spoolman consumer of used_weight. Track for future. (Skeptic Review, 2026-03-14)

### Low Priority
- [ ] Manual correction: spool 38 remaining weight in Spoolman (~110g lost from Gridfinity print 2026-03-13, slot 4 depletion incident)
- [x] start_g >= 0 guard — RFID delta now accepts 0g start. (Audit Finding B, v0.12.5)
- [x] remaining_weight default=0 — depleted guard now fires on missing Spoolman field. (Audit Finding D, v0.12.5)
- [ ] 3MF fetch takes 11-15s consistently, triggering "Excessive time spent" warnings — investigate if FTPS listing of 154 files is the bottleneck. Could skip listing and download by constructed filename directly.
- [ ] Delete remaining obsolete HA helpers: input_number.filament_iq_start/end_slot_N_g (deferred — active test scripts reference them)
- [x] Change ACTIVE_PRINT_PERSISTED log level from DEBUG to INFO for visibility in normal monitoring (48f18ff)
- [ ] Investigate stale seen_job_keys.json at /addon_configs/a0d7b954_appdaemon/apps/data/ (should only exist at filament_iq/data/)
- [ ] Manually correct spool 52 consumption in Spoolman (~143g from grid print 2026-03-12 15:03)

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
| v1.0.0 | 2026-03-15 | Full pipeline rewrite: consumption_engine.py, five-phase architecture, RFID-delta-wins, threemf_parser bug fixes, SpoolmanRecorder test infrastructure, print_history persistence |
| v0.12.6 | 2026-03-14 | Reconciler status helper + 3MF single-filament force match |
| v0.12.5 | 2026-03-14 | Three audit fixes (start_g guard, depleted default, fuel gauge tolerance) |
| v0.12.4 | 2026-03-14 | RFID reconciler hardening (3 guards) |
| v0.12.3 | 2026-03-14 | 16 E2E pipeline tests from measurement audit |
| v0.12.2 | 2026-03-14 | Defer RFID reconciler 60s post-print |
| v0.12.1 | 2026-03-13 | Rehydrate job_key from HA helper |
| v0.12.0 | 2026-03-13 | Reconciler print-active freeze |
