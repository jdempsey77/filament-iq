# Filament IQ — Project State

> Last updated: 2026-05-25 (v1.10.1 — RunoutTracker zero-write: post-write remaining zeroed for ran_out slots; reconciler suppressed for ran_out slots)

Snapshot of released versions, test coverage, key decisions, and open work. Updated after each release.

---

## Version Table

| Component | Version | Commit | Branch | Released |
|-----------|---------|--------|--------|----------|
| AppDaemon package | v1.10.1 | — | main | 2026-05-25 |
| Manager card (lovelace) | v1.9.8 | — | main | 2026-05-24 |
| Monitor (ska) | v1.6.3 | (deployed, not tagged) | main | 2026-05-19 |

---

## Test Counts

| Suite | Passing | Failing | Notes |
|-------|---------|---------|-------|
| filament-iq (all) | 1511 | 0 | |

### Tests added in v1.10.1 session (2026-05-25) — RunoutTracker zero-write

| Test | File | Guards |
|------|------|--------|
| `test_runout_zero_override_fires_when_remaining_nonzero` | test_ams_print_usage_sync.py | ran_out=on + post-write remaining=56g → PATCH remaining_weight=0 + RUNOUT_ZERO_OVERRIDE |
| `test_runout_zero_override_noop_when_already_zero` | test_ams_print_usage_sync.py | ran_out=on + remaining=0 → no PATCH (already zeroed) |
| `test_runout_zero_override_noop_when_boolean_off` | test_ams_print_usage_sync.py | ran_out=off + remaining=56g → no PATCH (normal print) |
| `test_reconcile_skips_slot_when_ran_out_boolean_on` | test_ams_print_usage_sync.py | ran_out=on → reconciler skips slot, RECONCILE_RUNOUT_SKIP logged |
| `test_reconcile_proceeds_when_ran_out_boolean_off` | test_ams_print_usage_sync.py | ran_out=off → normal reconcile proceeds, PATCH fires |

### Tests added in v1.10.1 session (2026-05-25) — location-first pre-filter

| Test | File | Guards |
|------|------|--------|
| `test_nonrfid_location_pre_filter_single_match_two_slots` | test_ams_rfid_reconcile.py | v1.10.1: pre-filter resolves two ambiguous non-RFID slots via Spoolman location |
| `test_nonrfid_location_pre_filter_zero_match_falls_through` | test_ams_rfid_reconcile.py | v1.10.1: 0 location matches falls through silently (bootstrap case) |
| `test_nonrfid_location_pre_filter_conflict_warning` | test_ams_rfid_reconcile.py | v1.10.1: 2+ location matches logs NONRFID_LOCATION_CONFLICT, falls through to tiebreak |
| `test_nonrfid_location_pre_filter_unenrolled_spool` | test_ams_rfid_reconcile.py | v1.10.1: unenrolled (no lot_nr) spool resolved via location match |
| `test_nonrfid_lot_nr_dual_slot_warning` | test_ams_rfid_reconcile.py | v1.10.1: NONRFID_LOT_NR_DUAL_SLOT warning when two AMS spools share lot_nr |
| `test_nonrfid_lot_nr_dual_slot_no_warning_for_shelf` | test_ams_rfid_reconcile.py | v1.10.1: no NONRFID_LOT_NR_DUAL_SLOT when duplicate spool is on Shelf |

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
| filament-iq | main | v1.10.1 committed and tagged |
| home_assistant | main | Clean working tree |

---

## Key Decisions

### 2026-05-25 — RunoutTracker zero-write: post-write remaining zeroed for ran_out slots (v1.10.1)

**Decision**: Zero-write belongs in `AMSPrintUsageSync`, NOT `RunoutTracker`. When `input_boolean.ams_slot_N_ran_out` is on at print finish, the consumption estimate may undershot (Spoolman shows non-zero remaining after the /use write). Fix has two parts:

1. **`_apply_runout_zero_overrides`** — called immediately after `_execute_writes` in `_do_finish`. If ran_out boolean is on AND post-write remaining > 0, issues `PATCH remaining_weight=0` directly to Spoolman and logs `RUNOUT_ZERO_OVERRIDE`. No-op if remaining already 0 or boolean off. Preserves the /use audit trail (USAGE_PATCHED log stays) and does not corrupt `_detect_runout_split` which reads `spoolman_remaining` before `_execute_writes`.

2. **Reconciler suppression** — `_reconcile_rfid_weight_slot` now checks the ran_out boolean at entry and returns immediately with `RECONCILE_RUNOUT_SKIP` (DEBUG). Prevents the 60-second-deferred RFID reconciler from overwriting the zero back to the fuel-gauge-derived estimate. Boolean stays on until a new spool is bound to the slot, at which point reconcile naturally resumes.

5 new tests. All 1511 passing.

### 2026-05-25 — Location-first pre-filter for non-RFID spool resolution (v1.10.1)

1. **Location-first pre-filter landed (v1.10.1)** — Non-RFID reconciler
   now partitions the full `all_nonrfid_ids` candidate pool by whether
   each spool's Spoolman `location` field matches the physical slot's
   canonical location BEFORE the weight tiebreak runs. Single match →
   resolve immediately with `NONRFID_LOCATION_MATCH`; 2+ matches →
   `NONRFID_LOCATION_CONFLICT` WARNING + fall through; 0 matches →
   silent fall through to weight tiebreak (bootstrap case). Fixes two
   failure classes simultaneously: (a) same-filament dual-load (spool 94
   at AMS1_Slot4 vs spool 95 at AMS1_Slot1, both pla|gfl04|bcbcbc),
   (b) cross-brand same-color pool inflation (unenrolled spool 87 Shelf
   vs spool 95 AMS1_Slot1). Also added `NONRFID_LOT_NR_DUAL_SLOT`
   WARNING in `_enroll_lot_nr` when another AMS-slot spool already holds
   the same lot_nr (enrollment proceeds). 6 new tests. PE review passed
   after one test fix (location="shelf" lowercase required for correct
   index-build and tiebreak available-filter behavior).

2. **Filament ID in lot_nr rejected** — Adding |fid{spoolman_filament_id}
   to the lot_nr string is architecturally blocked: `_build_lot_sig_for_lookup`
   only has Bambu tray attributes at query time — Spoolman filament.id is
   not available without a circular lookup. A 4-segment enrolled lot_nr
   would produce zero matches against a 3-segment lookup key. Deferred
   to backlog pending a full lookup mechanism redesign.

3. **Data fixes applied** — Spool 77 lot_nr corrected from pla|gfl04|bcbcbc
   to pla|gfl40|bcbcbc (wrong filament ID enrolled). Spool 95 Spoolman
   location corrected to AMS1_Slot1 (was incorrectly set to Shelf while
   physically loaded). Spool 94 confirmed at AMS1_Slot4.

### 2026-05-23 — Profile verification pipeline (v1.10.0)

Four phases delivered across PR #83–#85.

1. **Profile verification pipeline (v1.10.0)** — Local Niimbot render (Phase 1), AppDaemon backend
   (Phase 2), FilamentsTab UX (Phase 3), read-only surfaces + row indicators + manual linking (Phase 4).
   Verification state in `profile_verifications.json` (AppDaemon data dir). PR #83, #84, #85.

2. **Spoolman extra field rejected** — Spoolman validates extra fields against a registered schema;
   arbitrary keys return "Unknown extra field" error. Verification state stored in AppDaemon data
   directory instead (`profile_verifications.json`).

3. **REST API events don't reach AppDaemon listen_event** — AppDaemon subscribes via WebSocket;
   `POST /api/events/` fires into HA bus but not AppDaemon's listener. Card uses
   `hass.connection.sendMessage({ type: 'fire_event', ... })` which goes through WebSocket.

4. **Scorer color specificity tiebreak** — Added `len(cand_color) * 0.001` to color bonus. Fixes
   Gray (id=128) beating Light Gray (id=602) on identical 1.15 scores against "PLA Basic Light Gray".

5. **Profile URL pattern** — `https://3dfilamentprofiles.com/filament/details/{id}` where `id` is the
   integer id field from filaments.json. `short_code` URL pattern does not work (redirects to homepage).

6. **Niimbot pipeline architecture** — Pre-baked PNG pipeline replaced. `NiimbotPrinter` writes
   `spool_id` (or `spool_id|profile_url` when verified) to `input_text` helper. `ska` render script
   fetches Spoolman, renders PIL label, composites QR when `profile_url` present.

7. **Tab persistence on Android companion** — `sessionStorage` preserves active tab across card reloads
   triggered by external link navigation. Profile links use `window.open('_blank','noopener')` to open
   in-app browser sheet instead of leaving the WebView.

8. **Bulk status fetch** — Single `filament_iq_profile_bulk_status_request` event on FilamentsTab mount
   returns all known verification statuses in one response. Avoids per-filament lookup events for row
   indicators.

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

## Open Backlog Additions (v1.10.0 — 2026-05-23)

### Medium Priority

- [ ] **Spool 65 (Sunlu Dual-Color Black+Red)** — no profile match on 3dfilamentprofiles.com.
  Manual link via "Link manually" UI if a matching profile exists. (2026-05-23)
- [ ] **Spool 68 material mismatch** — `lot_nr` has TPU signature but filament record shows
  Matte PLA Light Grey. Determine correct filament type and update Spoolman record. (2026-05-23)

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

## System Health (as of 2026-05-23)

| Check | Status |
|-------|--------|
| AppDaemon startup | Clean — CONFIG_VALID for all apps, no SIGTERM |
| Reconciler state | ok=7, unbound=0 (7 slots) |
| Monitor (ska) | Running, 5 threads, D11 heartbeat active |
| D11_H | Powered on, heartbeat keeping alive (240s interval) |
| Swatch labels | 48/48 spools high confidence; local PIL render pipeline active |
| Profile verification | FilamentProfileLookup live; profile_verifications.json in data dir |
| filament-iq origin | Clean — v1.10.0 pushed |
| home_assistant | Clean working tree |
| Test suite | 1495 passing, 0 failing |
