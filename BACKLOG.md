## Filament IQ Backlog

### In Progress
- [ ] Verify all 3 lifecycle phases in production — Phases 1-3 coded and enabled, 7 HA automations disabled, monitoring for issues

### High Priority
- [ ] Reference Filament IQ dashboard — ship a ready-to-import Lovelace dashboard with slot cards, Filament Library, Spool Inventory, and Printer status. Raw YAML view exists in filament-iq repo (dashboard/filament_iq.yaml) but needs polish for easy import.
- [ ] Investigate 3MF_UNMATCHED for brief tray activations — tray tracking misses slots used for very short durations. Root cause: active_tray sensor polling interval vs actual extrusion time. Workaround in place (3MF-matched slots merged into active_slots).

### Low Priority
- [ ] 3MF fetch takes 11-15s consistently, triggering "Excessive time spent" warnings — investigate if FTPS listing of 154 files is the bottleneck. Could skip listing and download by constructed filename directly.
- [ ] Delete remaining obsolete HA helpers: input_number.filament_iq_start/end_slot_N_g (deferred — active test scripts reference them)

### Done
- [x] Phase 1: Print start lifecycle in AppDaemon (job key, start snapshot, tray seeding)
- [x] Phase 2: Print finish lifecycle in AppDaemon (end snapshot, usage processing, dedup)
- [x] Phase 3: Debug logging, swap detection, rehydrate mutex, pause-state fix
- [x] Disable 7 HA lifecycle automations (A-G) — replaced by Phases 1-3
- [x] 3MF matching: lot_nr color fallback for third-party filament color mismatches
- [x] 3MF matching: unmatched consumption flows to time_weighted pool instead of being dropped
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
