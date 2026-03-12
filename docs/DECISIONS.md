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

## Template for new entries

## YYYY-MM-DD — [Short decision title]

**Decision:** [What was decided, 1-2 sentences]

**Alternatives considered:**
- [Option] — rejected, [reason]

**Why:** [Reasoning, evidence, tradeoffs accepted]
