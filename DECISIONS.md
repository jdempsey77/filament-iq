## Filament IQ Architecture Decisions

### 2026-03-13 — Rehydrate reads job_key from HA helper, not task_name

**Decision:** `_rehydrate_print_state()` reads `_job_key` from
`input_text.filament_iq_active_job_key` (`self._job_key_entity`) rather
than re-deriving from `task_name.replace(" ", "_")`.

**Alternatives considered:**
- Prefix match in `_load_active_print` — rejected, underscores are
  overloaded in task names, false match risk
- Store task_name separately in active_print.json — rejected, schema
  change unnecessary when HA helper already holds the correct value
- Re-fetch 3MF on rehydrate failure — rejected, adds FTPS I/O to
  startup path, not needed once key mismatch is resolved

**Why:** The epoch suffix in job_key is generated at print start and
written to the HA helper (survives restarts). The task_name entity
never includes this suffix. The previous code also overwrote the
helper with the truncated key before calling `_load_active_print`,
destroying the only surviving correct value. The start_snapshot
rehydrate path already uses the same helper-first pattern
(lines 1070-1074) — job_key rehydrate now follows it.

**Evidence:** 0.28mm print, 2026-03-13. `ACTIVE_PRINT_PERSISTED`
`has_3mf=True` at 22:47, AD restart at 00:57, `threemf_file=none` at 01:22.

### 2026-03-14 — RFID reconciler deferred 60s post-print

**Decision:** `_reconcile_rfid_weights()` deferred via `run_in(60s)`
instead of running synchronously in `_do_finish()`.

**Why:** Bambu MQTT tray sensor (`remain%`) is cached and does not
refresh immediately after print finish. Synchronous reconcile reads
stale pre-print weight and patches Spoolman back, undoing the
consumption write silently.

Research confirmed (docs/research/bambu_rfid_tag_internals.md) that
remain% and tray_weight are stored in encrypted blocks on the physical
RFID tag, not computed by the printer. The AMS reads the tag after
print finish, updates the remain% block, then pushes the new value
via MQTT to HA. The 60s delay covers the AMS re-read cycle. This is
not a fixed-interval refresh — it depends on when the AMS next
interrogates the tag — so 60s is a conservative minimum, not a
guaranteed safe window.

**Alternatives considered:**
- Slot exclusion list (skip slots written this pass) — rejected,
  complex state to maintain, deferred call is simpler and correct
- Threshold guard — rejected per prior decision (2026-03-11),
  any real difference should be corrected

**Evidence:** ANALYZE audit 2026-03-14. No production incident yet
identified but mechanism confirmed in code review.

### 2026-03-14 — RFID reconciler hardening (four guards)

**Decision:** Added four guards to `_reconcile_rfid_weight_slot` and
`_reconcile_rfid_weights_deferred`.

**Why:**
1. print_active re-defer — deferred reconcile was firing during
   back-to-back prints, reading mid-print sensor values
2. Directional guard — reconciler must never increase Spoolman weight
   post-print; upward direction always indicates stale remain%
3. tray_weight sanity bounds (50-2000g) — no upper/lower cap allowed
   factory errors or cloned tags to corrupt Spoolman to impossible values
4. Minimum delta threshold (5g) — integer remain% resolution is 10g on
   1000g spools; 5g threshold eliminates idle slot noise writes

**Evidence:** Skeptic review 2026-03-14. Back-to-back race (HIGH),
spool swap between write and reconcile (HIGH), tray_weight corruption
(HIGH), idle slot oscillation (MEDIUM).
