## Filament IQ Backlog

### In Progress
- [ ] Move print lifecycle automations into AppDaemon — Phase 1 coded and testing (job key, start snapshot, tray seeding). Phase 2 (finish logic) and Phase 3 (cleanup) still ahead.

### High Priority
- [ ] Sync Color on Bind — Update Spoolman filament color_hex to match AMS tray-reported color during manual bind/assign. Full spec in docs/sync_color_on_bind.md. Ship backend first (sync_color_hex event field + handler), dashboard UI later.
- [ ] Investigate 3MF_UNMATCHED for slot 4 (color f330f9, PLA) — miriam print matched slot 3 via 3MF but slot 4 was unmatched despite spool 51 being enrolled with that color
- [ ] Reference Filament IQ dashboard — ship a ready-to-import Lovelace dashboard with the filament_iq package. Include: AMS slot cards (color chip, progress bar, active slot indicator, bind/assign UI), Filament Library page (browse/search Spoolman filaments), Spool Inventory page (browse/search spools with remaining weight, location, lot_nr status), Printer status card (conditional on printing: task name, progress, layer count, ETA). Should work out of the box with minimal config (just printer serial and Spoolman URL). Currently Jerry's dashboard is hand-built in his HA config — extract the reusable parts into a reference YAML that ships with the package.

### Low Priority
- [ ] Rename SLOT_ASSIGNED_NO_LOT_SIG log message to SLOT_ASSIGNED_LOT_SIG_EXISTS when spool already has a lot_nr
- [ ] Investigate reconciler 26s runtime for full 6-slot reconcile ("Excessive time spent" warnings)
- [ ] Pre-existing test failure in test_ams_rfid_reconcile (missing module import)

### Done
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
