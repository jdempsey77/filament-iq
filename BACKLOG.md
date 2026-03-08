Add a BACKLOG.md file to the repo root with these items:

## Filament IQ Backlog

### High Priority
- [ ] Move print lifecycle automations into AppDaemon (job key generation, start/end snapshots, finish event, dedup guard) — eliminate need for hand-built HA automations
- [ ] Suppress full reconcile on manual assign — only single-slot reconcile should fire (DONE - fffe84b)

### Low Priority  
- [ ] Rename SLOT_ASSIGNED_NO_LOT_SIG log message to SLOT_ASSIGNED_LOT_SIG_EXISTS when spool already has a lot_nr — current message is misleading
- [ ] Investigate "Excessive time spent" warnings in reconciler (26s for full reconcile)
- [ ] Pre-existing test failure in test_ams_rfid_reconcile (missing module import)
