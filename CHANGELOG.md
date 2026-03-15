# Changelog

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
