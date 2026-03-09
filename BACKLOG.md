## Filament IQ Backlog

### In Progress
- [ ] Verify all 3 lifecycle phases in production — Phases 1-3 coded and enabled, 7 HA automations disabled, monitoring for issues

### High Priority
- [ ] Reference Filament IQ dashboard — ship a ready-to-import Lovelace dashboard with slot cards, Filament Library, Spool Inventory, and Printer status. Raw YAML view exists in filament-iq repo (dashboard/filament_iq.yaml) but needs polish for easy import.
- [ ] Investigate 3MF_UNMATCHED for brief tray activations — tray tracking misses slots used for very short durations. Root cause: active_tray sensor polling interval vs actual extrusion time. Workaround in place (3MF-matched slots merged into active_slots).

### Low Priority
- [ ] 3MF fetch takes 11-15s consistently, triggering "Excessive time spent" warnings — investigate if FTPS listing of 154 files is the bottleneck. Could skip listing and download by constructed filename directly.
- [ ] Rename SLOT_ASSIGNED_NO_LOT_SIG log message to SLOT_ASSIGNED_LOT_SIG_EXISTS when spool already has a lot_nr
- [ ] Investigate reconciler 26s runtime for full 6-slot reconcile ("Excessive time spent" warnings)
- [ ] Clean up obsolete HA helpers that are no longer needed after lifecycle migration (filament_iq_start_json, filament_iq_end_json, filament_iq_active_job_key, filament_iq_last_processed_job_key, filament_iq_finish_automation_checkpoint, filament_iq_init_seed_debug, filament_iq_last_tray_entity, filament_iq_last_print_status_transition, input_number.filament_iq_start/end_slot_N_g)

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
