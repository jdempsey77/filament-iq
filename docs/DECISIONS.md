# Filament IQ — Decision Log

Captures architectural and configuration decisions with reasoning.
Updated when a non-obvious choice is made. Referenced by agents
before proposing changes to established patterns.

---

## 2026-03-11 — RFID reconciler lives in ams_print_usage_sync.py

**Decision:** New method `_reconcile_rfid_weights()` added to
`ams_print_usage_sync.py`, called after `_handle_usage_event`.

**Alternatives considered:**
- Option A: New standalone app — rejected, timing race risk,
  no guarantee it runs after Spoolman writes complete
- Option C: ams_rfid_reconcile.py — rejected, missing print
  lifecycle context, stretches SRP

**Why B:** Guaranteed timing (synchronous after all writes),
zero new dependencies, all helpers already exist.

---

## 2026-03-11 — RFID is always ground truth, no threshold

**Decision:** `_reconcile_rfid_weight_slot()` patches Spoolman
on any difference, no minimum threshold.

**Alternatives considered:**
- 5g threshold (agent proposal) — rejected, rounding noise
  is <1g, any real difference should be corrected
- 2g threshold — rejected, same reasoning

**Why no threshold:** RFID chip reports integer percentages.
Rounding noise is deterministic and small. If it differs,
it's real drift, not noise.

---

## 2026-03-11 — max_consumption_g raised from 300g to 1000g

**Decision:** Sanity cap on per-slot 3MF consumption raised
to 1000g.

**Why:** 300g blocked a legitimate 484g Big Crate print.
Research confirmed P1S single AMS spool maximum is 1000g
(physical constraint). Any value above 1000g from a single
slot is a sensor error, not a real print.

**Evidence:** USAGE_SANITY_CAP fired on job
5x5x24U_-_209x209x172mm_-_Big_Crate_ (484g, slot 1, spool 58).

---

## 2026-03-09 — _SUCCESS_STATES restricted to "finish" only

**Decision:** 3MF consumption writes only on gcode_state=finish.

**Why:** Previous allowlist included "failed", "cancelled" etc.
causing overcounting on aborted prints. RFID delta path already
had this guard — 3MF path did not. Aligned both paths.

---

## 2026-03-09 — Blocking HTTP deferred

**Decision:** Spoolman and HA HTTP calls remain synchronous
(blocking) in AppDaemon callbacks.

**Why deferred:** AppDaemon's async model makes true
non-blocking HTTP complex. Current print volume (1-3 prints/day)
means blocking is acceptable. Timeouts (10-15s) prevent hangs.
Revisit if print volume increases or timeouts cause issues.

---

## 2026-03-09 — Monitor daemon on ska, not on HA

**Decision:** Print lifecycle monitor runs on ska (192.168.2.7)
as a systemd user service, not as an AppDaemon app.

**Why:** HA addon restarts would lose in-progress print state.
Separate host survives HA restarts, writes artifacts to NAS
independently. AppDaemon already has enough responsibility.

---

## 2026-03-11 — Import paths differ between private and OSS repos

**Decision:** OSS repo (filament-iq) uses `filament_iq.*`
import and `mock.patch` paths. Private repo (home_assistant)
uses the same `filament_iq.*` paths — not `appdaemon.apps.filament_iq.*`.

**Why:** pytest runs from the repo root, not from within
AppDaemon. The `appdaemon/apps/` directory is on `sys.path`
via conftest.py, so `filament_iq.*` resolves correctly in
both repos. The `appdaemon.apps` prefix is never correct in
test mock paths.

**Impact on sync:** `sync-oss.sh --copy` copies files
verbatim. Since both repos now use `filament_iq.*` paths,
no post-copy fixup is needed.

---

## 2026-03-12 — Fix C revert: _trays_used must NOT be seeded from _start_snapshot

**Decision:** Reverted `_trays_used = set(_start_snapshot.keys())` in
`_rehydrate_print_state()`. On rehydrate, `_trays_used` starts empty
and is populated only by `_seed_active_trays()` + tray change events.

**Alternatives considered:**
- Seed from `_start_snapshot.keys()` (Fix C, committed then reverted)
  — rejected, `_start_snapshot` contains all 6 loaded slots system-wide,
  not just the ones active in the current print
- Seed from `input_text.filament_iq_trays_used_this_print` — rejected,
  that helper is only written at print END (line 605), not during

**Why revert:** Production logs showed `REHYDRATE_TRAYS_USED
slots={1,2,3,4,5,6}` during a single-color Grid print. All 6 slots
would have been included in consumption, causing phantom writes.
Empty set + active tray seeding + events is correct: only trays
that actually feed filament get included.

**Evidence:** Grid print rehydrate log, commit `d28916c`.

---

## 2026-03-12 — 0g is a valid physical state, not sensor unavailable

**Decision:** Changed all `> 0` guards to `>= 0` for fuel gauge reads,
RFID delta end checks, and seed slot locks. 0g means depleted spool
still physically present, not sensor failure.

**Alternatives considered:**
- Keep `> 0` and treat 0g as unavailable — rejected, P1S returns
  string "unavailable" on sensor failure, not numeric 0
- Add a separate `DEPLETED` state — rejected, unnecessary complexity,
  0g naturally flows through existing math (delta = start - 0 = start)

**Why:** Crates print analysis showed 4 independent failures, all
rooted in 0g rejection. Asymmetric guard: `start_g > 0` (nothing to
consume from empty spool) but `end_g >= 0` (fully consumed is valid).

**Evidence:** Crates print prod logs, commit `79c67ef`.

---

## Template for new entries

## YYYY-MM-DD — [Short decision title]

**Decision:** [What was decided, 1-2 sentences]

**Alternatives considered:**
- [Option] — rejected, [reason]

**Why:** [Reasoning, evidence, tradeoffs accepted]
