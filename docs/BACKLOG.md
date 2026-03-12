# Filament IQ — Backlog

> Full codebase audit: 2026-03-10 | Red team audit: 2026-03-12 | Last updated: 2026-03-12

## Legend
- 🔴 HIGH — production risk, fix immediately
- 🟡 MEDIUM — fix in next sprint
- 🟢 LOW — cleanup, fix when convenient
- ✅ DONE — completed
- 🔵 FEATURE — new capability, not a bug

---

## In Progress

_None — all in-progress items completed._

## Recently Completed (moved from In Progress)

| # | Status | Finding | Source | Fix |
|---|--------|---------|--------|-----|
| 1 | ✅ | `_active_run` not reset in finally — Spoolman outage permanently blocks reconciler | R3 #1/#2 | `1713c7b` |
| 2 | ✅ | `seen_job_keys.json` non-atomic write — crash mid-write corrupts dedup history | R1 #4 | `1713c7b` |
| 3 | ✅ | USAGE_SANITY_CAP 300g → 1000g — blocked legitimate 484g print | Prod incident | `1bbc233` |
| 4 | ✅ | RFID_IDENTITY_STUCK false positive on enrolled slots after AppDaemon restart | Prod bug | `bb10a74` |
| 5 | ✅ | "New" location spools excluded from dropdown — fresh spools unbindable | Prod bug | `bb10a74` |
| 6 | ✅ | `tray_uuid` missing from UNBOUND_REASON log lines — diagnostics gap | Prod | `bb10a74` |
| 7 | ✅ | Write-ahead dedup: permanent data loss if Spoolman times out (job deduped but never written) | Red team #1 | `02910eb` |
| 8 | ✅ | CI mock paths: `appdaemon.apps.filament_iq.*` → `filament_iq.*` for both repos | CI fix | `11f9d07` + `b08cf66` |

---

## Filament IQ Repo — Audit Findings

### HIGH

| # | Status | Finding | Source | File / Line |
|---|--------|---------|--------|-------------|
| 1 | ✅ | `_SUCCESS_STATES` allowlist — 3MF overcounting on non-success prints | R1 #1 | `ams_print_usage_sync.py` — fixed `c50eac0` |
| 2 | ✅ | Test harness drift from real `initialize()` — `_TestableUsageSync` missing `spoolman_sensor_prefix`, `printer_ip`, `threemf_fetch_method` attrs; silent test failures | R2 #8 | `tests/test_ams_print_usage_sync.py` — fixed `4368ce5` |
| 3 | ✅ | `_spoolman_patch` not mocked in test harness — depleted-spool path could make real HTTP calls | R2 #7 | `tests/test_ams_print_usage_sync.py` — fixed `4368ce5` |
| 4 | ✅ | Zero test coverage: `ams_rfid_guard.py` | R2 #1 | 14 tests — `4ce5332` |
| 5 | ✅ | Zero test coverage: `filament_weight_tracker.py` | R2 #2 | 9 tests — `4ce5332` |
| 6 | ✅ | Zero test coverage: `spoolman_dropdown_sync.py` | R2 #3 | 9 tests — `4ce5332` |
| 7 | ✅ | `_rehydrate_print_state()` never tested | R2 #4 | 7 tests — `4ce5332` |
| 8 | ✅ | Negative RFID delta clamping never tested | R2 #6 | 3 tests — `4ce5332` |
| 9 | ✅ | `_coerce_json_field` None path never tested | R2 #5 | 5 tests — `4ce5332` |
| 10 | ✅ | Write-ahead dedup ordering — `_persist_seen_job_keys()` before writes causes permanent data loss on Spoolman timeout | RT #1 | `ams_print_usage_sync.py` — fixed `02910eb` |

### MEDIUM

| # | Finding | Source | File / Line |
|---|---------|--------|-------------|
| 1 | Empty string / "idle" status passes RFID delta guards — should guard explicitly | R1 #1 | `ams_print_usage_sync.py:811` |
| 2 | `_filter_trays_by_duration` empty set + `start_map` re-inclusion inconsistency | R1 #2 | `ams_print_usage_sync.py:920-927` |
| 3 | `_finish_wait_tick` double-fire risk if `cancel_timer` silently fails | R1 #3 | `ams_print_usage_sync.py:879-904` |
| 4 | "offline" status writes RFID delta for incomplete prints — consider `_FAILED_STATES` | R1 #7 | `ams_print_usage_sync.py:946-955` |
| 5 | `manage_ha.sh` restart sequence missing `wait_for_ha` between HA and AppDaemon restart | R1 #6 | `scripts/manage_ha.sh:372-375` |
| 6 | `monitor_print.sh` JSON via string concatenation — unescaped `$state` could produce malformed JSON | R1 #5 | `scripts/monitor_print.sh` |
| 7 | ✅ Real file I/O in tests — `_persist_seen_job_keys` writes to `data/seen_job_keys.json` on every test | R2 #9 | `tests/test_ams_print_usage_sync.py` — fixed `4368ce5` |
| 8 | `_check_unbound_trays()` never tested | R2 #10 | `ams_rfid_reconcile.py` |
| 9 | `_fetch_spools_cache()` never directly tested | R2 #11 | `ams_rfid_reconcile.py` |
| 10 | `_build_slot_data()` never directly tested | R2 #12 | `ams_rfid_reconcile.py` |
| 11 | `_on_print_finish` guards never tested | R2 #13 | `ams_print_usage_sync.py` |
| 12 | Empty string `job_key` bypasses dedup — never tested | R2 #14 | `ams_print_usage_sync.py` |
| 13 | `TestFtpErrorHandling` makes real network connections — ~12s timeouts | R2 #15 | `tests/test_threemf_parser.py` |
| 14 | Two `@pytest.mark.skip` tests — delete or rewrite | R2 #16 | `tests/` |
| 15 | `_validate_config` never directly tested | R2 #17 | `ams_print_usage_sync.py` |
| 16 | Blocking Spoolman HTTP in AppDaemon callbacks — all calls synchronous, mitigated by timeouts | R3 #5 | `ams_rfid_reconcile.py`, `ams_print_usage_sync.py` |
| 17 | Blocking FTPS/subprocess in callbacks — printer offline blocks AppDaemon up to 15s | R3 #6 | `threemf_parser.py` |
| 18 | `cancel_timer` not wrapped in try/except | R3 #3 | `ams_rfid_reconcile.py:758` |
| 19 | `_rfid_identity_tracker` initialized via getattr hack, not in `initialize()` | R3 #4 | `ams_rfid_reconcile.py:892-896` |
| 20 | Undocumented config keys in `apps.yaml` | R3 #7 | `appdaemon/apps/apps.yaml` |
| 21 | Persist in-flight print state to `active_print.json` — mid-print restart loses all consumption data. Write `{job_key, start_snapshot, trays_used}` on print start, resume on restart if print still active, delete on finish. ~20 lines | RT #2 | `ams_print_usage_sync.py` |
| 22 | Coverage push to 75%+ on critical write paths — `_spoolman_use()` failure/retry, `_handle_finish_event()` end-to-end, reconciler PATCH write-back (0% coverage). Delete 2 fake math tests, unskip/delete 2 skipped pool-logic tests. Target 75% overall, 70%+ per module | RT #3 | `tests/` |
| 23 | AppDaemon deploy verification — `manage_ha.sh` exits 0 even if addon fails to start. Add `ha addons info` status poll (timeout 60s, exit 1 if not started) | RT #4 | `scripts/manage_ha.sh` |
| 24 | Duplicate `tray_uuid` detection in reconciler — two slots with same tray_uuid should flag both CONFLICT, log WARNING, notify operator. Prevents double-counting on cloned RFID chips | RT #5 | `ams_rfid_reconcile.py` |
| 25 | Config validation on startup — add type + range checks: `spoolman_url` (valid URL), `max_consumption_g` (>0), `scan_interval_seconds` (>0), `color_tolerance` (0-255). Log ERROR and refuse to initialize on bad config | RT #6 | All app files |

### LOW

| # | Finding | Source | File / Line |
|---|---------|--------|-------------|
| 1 | `manage_ha.sh` sources `deploy.env` not `deploy.env.local` | R1 #8 | `scripts/manage_ha.sh` |
| 2 | Dead `import time` | R3 #8 | `ams_print_usage_sync.py:22` |
| 3 | `_last_notify_by_key` grows unbounded | R3 #9 | `ams_rfid_guard.py` |
| 4 | Wrong entity name in dashboard agent spec | R3 #10 | `docs/09_dashboard_agent.md` |
| 5 | Agent spec files 02-05 missing from `docs/` | R3 #11 | `docs/` |
| 6 | Inline `import time` in hot paths | R3 #13 | `ams_rfid_reconcile.py` |
| 7 | pause/idle/empty string status never tested | R2 #18 | `tests/test_ams_print_usage_sync.py` |
| 8 | 0g RFID delta for RFID slot never tested | R2 #19 | `tests/test_ams_print_usage_sync.py` |
| 9 | `_fetch_3mf_curl()` curl fallback path untested | R2 #20 | `tests/test_threemf_parser.py` |

---

## HA Config Repo — Audit Findings

### HIGH (all fixed)

| # | Status | Finding | Fix |
|---|--------|---------|-----|
| 1 | ✅ | `ha_last_restart_time` missing from `configuration.yaml` | Fixed `1c43350` |
| 2 | ✅ | Entity name mismatch: `appdaemon_` vs `filament_iq_startup_suppress_swap` (5 refs) | Fixed `1c43350` |
| 3 | ✅ | `scripts.yaml` field/variable mismatch: `slot` vs `slot_number` (3 refs) | Fixed `1c43350` |

### MEDIUM

| # | Finding | File |
|---|---------|------|
| 1 | ✅ `notify.notify` → `notify.mobile_app_jd_pixel_10xl` — leak detection + HA watchdog | `automations.yaml` — fixed `1039a5c` |
| 2 | `input_boolean` manifest incomplete — 3 missing: `filament_iq_print_active`, `filament_iq_needs_reconcile`, `filament_iq_nonrfid_enabled` | `helpers_manifest.yaml` |
| 3 | Fuel gauge template sensors lack `availability` template — report 0.0 when printer offline | `configuration.yaml` |
| 4 | 12 dead `input_number` helpers: `ams_slot_{1-6}_extras_weight`, `ams_slot_{1-6}_filament_id` | `configuration.yaml` |
| 5 | Washer notification missing cycle guard — fires on HA restart / washer unplugged | `automations.yaml` |
| 6 | `input_select.active_filament_spool` dead trigger reference in `spoolman_low_filament_warning` | `automations.yaml` |
| 7 | `preflight_input_text.sh` only probes 1 entity — doesn't validate all 6 slot binding helpers | `scripts/preflight_input_text.sh` |
| 8 | 8 disabled legacy automations reference non-existent helpers — should be deleted | `automations.yaml` |
| 9 | Air purifier automation fires on HA restart | `automations.yaml` |

### LOW

| # | Finding | File |
|---|---------|------|
| 1 | `service:` vs `action:` inconsistency — ~50 deprecated uses | `automations.yaml`, `scripts.yaml` |
| 2 | 6 `ams_slot_N_spool_id` helpers missing explicit `max:` | `configuration.yaml` |
| 3 | 2 more dead helpers: `spoolman_new_filament_vendor_id`, `ams_placeholder_filament_id` | `configuration.yaml` |
| 4 | Deck camera automation alias misleading ("turn on" but action is turn_off); weekday condition lists all 7 days | `automations.yaml` |
| 5 | ✅ `scripts.yaml` truncation comment wrong (1024 vs 255) | Fixed `1c43350` |
| 6 | `initial_state: false` deprecated — use `enabled: false` | `automations.yaml` |
| 7 | Disabled debug automation references wrong entity name | `automations.yaml` |

---

## Features — Planned

| # | Feature | Description |
|---|---------|-------------|
| 1 | ✅ RFID-Spoolman weight reconciler | `_reconcile_rfid_weights()` in `ams_print_usage_sync.py`. Runs after every print finish, RFID always ground truth (no threshold). Per-slot isolation, negative remain guard, dry_run safe. 9 tests. Commits `3214e6c` + `4304ba0`. |
| 2 | ✅ Background Monitor daemon | Deployed as `filament-iq-monitor.service` on ska (systemd user unit). HA availability + print lifecycle monitoring with structured JSON artifacts to `/mnt/store/filament_iq/monitor/`. Committed `59d68df`, fixes `f513d49` `a27569f`. |
| 3 | 🔵 `auto_empty_spools` re-enable | Re-enable after verifying F1 fix in production logs. |
| 4 | 🔵 Dashboard — inventory view | Full spool inventory card showing all 6 AMS slots + shelf spools. |
| 5 | 🔵 Dashboard — system health | AppDaemon health, last reconcile, last print, error counts. |
| 6 | 🔵 OSS prep | Reference dashboard, README, install docs for public release. |
| 7 | ✅ HA token rotation script | `scripts/rotate-secret.sh` — rotates HA long-lived token on Mac + ska. Committed `efc7cad`. |
| 8 | ✅ loginctl linger | `deploy-monitor.sh` enables linger so monitor survives SSH disconnect. Fixed `a27569f`. |

---

## Releases

| Version | Date | Key Changes |
|---------|------|-------------|
| v0.10.1 | 2026-03-12 | Write-ahead dedup fix — failed Spoolman writes now retryable (`02910eb`, PR #17) |
| v0.10.0 | 2026-03-12 | RFID stuck false positive, sanity cap 1000g, dropdown New filter, tray_uuid logs, CI mock paths (PR #15, #16) |
| v0.9.0 | 2026-03-11 | RFID weight reconciler, 3MF race guard, batch Spoolman fetch, smart empty guard, removed pool estimation (PR #14) |
| v0.8.0 | 2026-03-09 | Native FTPS, phantom consumption fix, slot_position_material match tier |

---

## Deferred / Accepted

| # | Finding | Rationale |
|---|---------|-----------|
| 1 | Blocking Spoolman HTTP (R3 #5) | Architectural fix deferred. Mitigated by 10-20s timeouts. AppDaemon event loop impact acceptable for current print volume. |
| 2 | Blocking FTPS (R3 #6) | Same rationale. 4-attempt retry with 110s total window sufficient for network hiccups. |
