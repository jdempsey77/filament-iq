# Filament IQ — Project State

Snapshot of released versions, test coverage, key decisions, and open work. Updated after each release.

---

## Version Table

| Component | Version | Commit | Branch | Released |
|-----------|---------|--------|--------|----------|
| AppDaemon package | v1.7.6 | b93ed7c | feature/printer-dashboard | 2026-05-10 |
| Manager card (lovelace) | v1.2.1 | — | — | — |

> v1.7.6 is tagged on `feature/printer-dashboard`. PR to `main` pending.

---

## Test Counts

| Suite | Passing | Failing | Notes |
|-------|---------|---------|-------|
| filament-iq (all) | 1417 | 7 | 7 pre-existing `consumption_engine` failures (unrelated to v1.7.6) |

### Tests added in v1.7.6 session (2026-05-10)

| Test | File | Guards |
|------|------|--------|
| `test_a1_pre_write_depletion_guard_skips_write` | test_spoolman_writes.py | PATCH 1: pre-write depletion guard skips /use when remaining ≤ 0 |
| `test_a2_notify_exception_does_not_abort_decisions_loop` | test_spoolman_writes.py | PATCH 1: notify/ failure does not abort subsequent slot writes |
| `test_b1_depleted_chip_sets_unbound_reason` | test_ams_rfid_reconcile.py | PATCH 3: depleted chip triggers UNBOUND_REASON + push notify |
| `test_b2_allows_nonrfid_sig_duplicate` | test_ams_rfid_reconcile.py | PATCH 2: non-RFID pipe-sig bypasses UUID duplicate block |
| `test_b2_rfid_uuid_duplicate_blocked` | test_ams_rfid_reconcile.py | PATCH 2: RFID UUID duplicate correctly blocked |
| `test_notify_abort_guard` | test_spoolman_writes.py | PATCH 1: regression guard for notify-abort scenario |

---

## Pending Commits / PR Status

| Repo | Commit | Status |
|------|--------|--------|
| filament-iq | b93ed7c (v1.7.6) | Tagged, pushed to origin/feature/printer-dashboard — **PR to main pending** |
| home_assistant | a053107 | Synced, committed, clean working tree |

---

## Key Decisions

### 2026-05-10 — Physical inventory audit revealed phantom overconsumption (BUG CLASS 1)

Spoolman was receiving `/use` writes for spools already at `remaining_weight = 0`. The pre-write depletion guard
(`_execute_writes` fetches `/api/v1/spool/{id}` before calling `/use`) was added to prevent further phantom writes.
Decision: fail open on Spoolman fetch error (allow write) to avoid blocking legitimate consumption records.

**Why:** A spool at 0g that gets a `/use` call drives remaining into negative values, corrupting Spoolman inventory.
The guard is cheap (one GET per write) and eliminates the class of bug entirely.

### 2026-05-10 — RFID lot_nr aliasing (BUG CLASS 2): restrict duplicate block to UUID-format lot_nr values only

`_enroll_lot_nr` blocked enrollment when `lot_nr` already existed in any spool. Non-RFID spools use pipe-sigs
(`"pla|gfa00|ff0000"`) that are legitimately shared across multiple spools of the same material. The PATCH 2 guard
(`_is_lot_nr_uuid`) restricts the duplicate block to 32-char hex UUIDs only, allowing non-RFID enrollment to proceed.

**Why:** Blocking non-RFID enrollment caused valid spools to go unbound, producing false UNBOUND_ACTION_REQUIRED
notifications and preventing accurate slot assignment for non-RFID AMS trays.

### 2026-05-10 — Depleted RFID chip path (B1): use lotnr_to_all_spools for identity lookup, lotnr_to_spools for candidates

The B1 code path (RFID chip seen in tray but all owning spools are at Empty location) needed to find the owning
spool to set UNBOUND_REASON and fire push notify. `lotnr_to_spools` excluded Empty/New location spools (by design,
for candidate filtering). A separate `lotnr_to_all_spools` index (includes all locations) was added for identity
lookup only, while candidate pools continue to use the filtered index.

**Why:** Mixing identity lookup and candidate filtering into one index caused depleted chips to fall through to
`UNBOUND_GENERIC` instead of the more actionable `RFID_CHIP_BELONGS_TO_DEPLETED_SPOOL` path.

---

## Open Backlog Additions (v1.7.6)

### Low Priority

- [ ] **SpoolsTab color family filter** — Add a color-family grouping filter to the Spoolman SpoolsTab card
  (e.g., "Blues", "Reds", "Neutrals") to make large spool libraries easier to browse. Purely cosmetic,
  no backend changes needed. (2026-05-10)

- [ ] **External location grouped card** — Lovelace card view that groups spools by external storage location
  (shelf label, bin, cabinet) rather than by printer slot. Useful for physical inventory checks.
  Requires `extra.storage_location` field convention in Spoolman. (2026-05-10)

- [ ] **Spool 92 unknown state investigation** — During v1.7.6 session, spool 92 appeared in Spoolman with
  location "Unknown" and no associated slot binding. Investigate whether this is a reconciler edge case
  (e.g., spool inserted then immediately removed before reconcile cycle completed) or a data entry artifact.
  Low urgency — no active slot impacted. (2026-05-10)

---

## System Health (as of v1.7.6 deploy)

| Check | Status |
|-------|--------|
| AppDaemon startup | Clean — CONFIG_VALID for all apps, no SIGTERM |
| Reconciler state | ok=6, unbound=0 |
| v1.7.6 tag | Pushed to origin/feature/printer-dashboard |
| PR to main | Pending |
| home_assistant sync | Clean working tree, committed at a053107 |
| Pre-existing test failures | 7 (consumption_engine — unrelated to v1.7.6 scope) |
