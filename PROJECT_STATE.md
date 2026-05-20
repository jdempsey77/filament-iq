# Filament IQ — Project State

Snapshot of released versions, test coverage, key decisions, and open work. Updated after each release.

---

## Version Table

| Component | Version | Commit | Branch | Released |
|-----------|---------|--------|--------|----------|
| AppDaemon package | v1.7.6 | b93ed7c | main | 2026-05-10 |
| Manager card (lovelace) | v1.9.3 | 3abb19a | main | 2026-05-18 |
| Monitor (ska) | v1.6.3 | (deployed, not tagged) | main | 2026-05-19 |

> Main is 19 commits ahead of origin — not yet pushed.

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
| filament-iq | main | **19 commits ahead of origin — not yet pushed** |
| home_assistant | main | Clean working tree |

---

## Key Decisions

### 2026-05-19 — D11HeartbeatLoop added to ska monitor (v1.6.3)

`D11HeartbeatLoop` added as a 5th daemon thread in `monitor/monitor.py`. Sends HEARTBEAT command
(0xdc, payload `bytes([1])`) via BLE to the D11_H label printer (MAC `C9:44:3A:01:03:09`) every
240 seconds. Runs the inline bleak script via the `~/niimprint-311-env/bin/python3` venv subprocess
(stdlib-only monitor cannot import bleak directly). Logs `NIIMBOT_HEARTBEAT_OK` or
`NIIMBOT_HEARTBEAT_FAIL`; never crashes the monitor on printer unavailability.

**Why:** The D11_H auto-powers off after ~5 minutes of BLE inactivity. Without a keepalive,
print jobs queued during idle periods failed because the printer was off and BLE reconnect took
too long. Heartbeat keeps it powered, eliminating the wake-up lag failure mode.

### 2026-05-18 — NIIMBOT D11_H BLE swatch label pipeline (end-to-end)

Full swatch label printing wired from HA card through AppDaemon to physical D11_H label.

Pipeline: `Card "Swatch Label" button → filament_iq_print_niimbot_label event →
NiimbotPrinter (AppDaemon) → Spoolman lookup → FilamentProfilesClient match →
input_text.filament_iq_niimbot_print_queue → ska NiimbotPrintLoop →
~/print_niimbot.sh <filament_id> → D11_H via BLE`

Key findings from HCI btsnoop capture:
- START_PRINT requires 9-byte payload (not 1-byte as documented)
- SET_DIMENSION requires 45-byte payload with LE dimensions + UUID
- D11_H printhead = 141px; label orientation: 141px wide × N px scroll
- Image row encodings: 0x84 (empty), 0x83 (indexed ≤6 black px), 0x85 (bitmap >6 black px)
- Slim label from 3dfilamentprofiles: rotate 90°, scale to 141×230px

25,945 slim label PNGs scraped to NAS at `/mnt/store/filament_iq/slim_labels/{id}.png`.
filaments.json (40MB, 25,945 entries) at `/mnt/store/filament_iq/filaments.json`.

### 2026-05-18 — FilamentProfilesClient color scoring + singleton cache

Color field scoring (+0.15, uncapped) added to break ties between same brand+material+type entries.
Color name extracted from `candidate["color"]` field after stripping `(NNNNN)` product code suffix.
Result: 48/48 spools at high confidence; all Bambu Lab PLA Basic color variants resolve uniquely.

Module-level `get_profiles_client(path)` cache added — both `label_printer` and `niimbot_printer`
share one instance; 40MB filaments.json loads exactly once per AppDaemon process.

AppDaemon path gotcha: inside the addon container, `/config/` is AppDaemon's config root and
`/homeassistant/` is HA's config root. `filament_profiles_path` in apps.yaml must use `/homeassistant/`.

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

## Open Backlog Additions (2026-05-18/19)

### Medium Priority

- [ ] **D11 print retry** — If `print_niimbot.sh` returns non-zero, wait 30s and retry once before
  clearing the queue. Handles printer wake-up lag for sparsely-used sessions. (2026-05-18)

### Low Priority

- [ ] **Spool 76 consumption correction** — ~400g unrecorded (2026-03-25). Patched to 600g remaining
  as estimate; actual unknown. (backlog since 2026-03-25)
- [ ] **Spool 39 consumption correction** — ~144g overcounted (2026-03-11). (backlog since 2026-03-11)
- [ ] **Rehydrated start snapshot undercounts delta** — Audit Finding 8b. (ongoing)
- [ ] **Post-rehydration delayed cache retry** — Deferred until 3MF_CACHE_REHYDRATE_MISS observed
  in production logs. (v1.7.2 deferral)
- [ ] **v2.2.22 compat check** — When ha-bambulab v2.2.22 is released. (ongoing)

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

## System Health (as of 2026-05-19)

| Check | Status |
|-------|--------|
| AppDaemon startup | Clean — CONFIG_VALID for all apps, no SIGTERM |
| Reconciler state | ok=7, unbound=0 (7 slots) |
| Monitor (ska) | Running, 5 threads, D11 heartbeat active |
| D11_H | Powered on, heartbeat keeping alive (240s interval) |
| Swatch labels | 48/48 spools high confidence, all PNGs present on NAS |
| filament-iq origin | 19 commits ahead — push pending |
| home_assistant | Clean working tree |
| Test suite | 1417 passing, 7 pre-existing consumption_engine failures |
