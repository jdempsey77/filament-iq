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
