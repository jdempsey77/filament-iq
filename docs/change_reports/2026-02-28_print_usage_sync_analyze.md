# ANALYZE — Print Usage Sync: Pre-implementation Inventory

**Date:** 2026-02-28
**Scope:** Read-only analysis for `appdaemon/apps/ams_print_usage_sync.py`
**Trigger:** Custom HA event `P1S_PRINT_USAGE_READY` fired at end of `p1s_remaining_snapshot_on_finish` automation

---

## Q1 — Spoolman Consumption Endpoint

| Property        | Value                                           |
|-----------------|------------------------------------------------|
| **URL**         | `PUT /api/v1/spool/{id}/use`                   |
| **HTTP method** | **PUT** (not POST)                             |
| **Body**        | `{"use_weight": <float>}` or `{"use_length": <float>}` |
| **`use_weight` unit** | Grams                                    |
| **Success**     | 200 — returns updated spool JSON               |
| **Errors**      | 400 (empty body), 404 (spool not found), 422 (validation) |

At least one of `use_weight` or `use_length` is required. Positive values subtract from remaining; negative values add back. For our use case, `{"use_weight": <grams_consumed>}` is the correct payload.

**Existing usage in codebase:** The HA Spoolman integration service `spoolman.use_spool_filament` wraps this endpoint and is currently called in the finish automation (line 937). No direct REST calls exist in `appdaemon/apps/`.

---

## Q2 — Print Status State Machine

### Automations referencing `print_status`

| Automation | Line | Trigger | States |
|---|---|---|---|
| `printer_air_purifier_lan` | 180 | Any change | ON: `running, printing, pause, paused`; OFF: `idle, standby, finish, finished, completed, failed, error` |
| `p1s_capture_active_tray_entity` | 354 | `to: [running, printing]` | Only entering active print |
| `p1s_record_trays_used_during_print` | 465 | Tray state changes | Condition: `print_status in [running, printing]` |
| `p1s_remaining_snapshot_init` | 510 | `to: [running, printing]` | Entering active print |
| `p1s_remaining_snapshot_on_tray_first_active` | 683 | Tray `active` attr → true | Condition: `print_status in [running, printing]` |
| `p1s_remaining_snapshot_on_finish` | 762 | `from: [running, printing, pause, paused]` | **No `to:` constraint — fires for all exits** |
| `p1s_debug_print_status_transition` | 744 | Any change | Logs all transitions (debug mode) |

### State transitions per outcome

| Outcome | Sequence |
|---|---|
| Successful print | `idle` → `running`/`printing` → `finish`/`finished`/`completed` → `idle` |
| Failed print | `running`/`printing` → `failed`/`error` → `idle` |
| Cancelled mid-print | `running`/`printing` → `canceled` → `idle` |
| Cancelled before start | `idle` → (no `running` entered) | Init automation never fires, start_json stays `{}` |
| Paused then finished | `running` → `pause`/`paused` → `finish` |
| Paused then cancelled | `running` → `paused` → `canceled` |

### Does the finish automation fire for failed/cancelled?

**Yes.** The trigger uses `from: [running, printing, pause, paused]` with no `to:` constraint. It fires for transitions to **any** state including `failed`, `error`, and `canceled`.

The `is_failed` / `should_decrement` logic (lines 780–781) handles this:

```yaml
is_failed: "{{ trigger_state in ['failed', 'error', 'canceled'] }}"
should_decrement: "{{ not is_failed or states('input_boolean.p1s_decrement_on_failed') == 'on' }}"
```

No trigger changes are needed. The event payload carries `print_status` so AppDaemon can make its own decision.

---

## Q3 — RFID vs Non-RFID Detection

### Available signals

| Signal | Mechanism | Reliability |
|---|---|---|
| **Status helper** (`input_text.ams_slot_{N}_status`) | Persisted by reconciler | **Most reliable — survives printer power cycles** |
| **`tag_uid` attribute** of tray sensor | `"0000000000000000"` = non-RFID | Only valid when printer is actively connected |
| **Fuel gauge** (`sensor.p1s_tray_{N}_fuel_gauge_remaining`) | `0.0` when `tray_weight` is 0 | Unreliable — 0.0 for both RFID and non-RFID when printer is off |

### Current slot status (live)

| Slot | Status | RFID? |
|---|---|---|
| 1 | `NON_RFID_REGISTERED` | No |
| 2 | `OK` | Yes |
| 3 | `NON_RFID_REGISTERED` | No |
| 4 | `OK` | Yes |
| 5 | `NON_RFID_REGISTERED` | No |
| 6 | `NEEDS_MANUAL_BIND` | No |

### Recommendation

For print-finish-time detection, use the **status helper** as the primary signal. The relevant constants from `ams_rfid_reconcile.py`:

- **RFID:** `OK`, `CONFLICT: MISMATCH`, `OK: FIXED_EXPECTED`
- **Non-RFID:** `NON_RFID_REGISTERED`, `OK_NON_RFID_REGISTERED`, `NEEDS_MANUAL_BIND`, `WAITING_FOR_CONFIRMATION`, `LOW_CONFIDENCE_NO_AUTO_MATCH`, `NON_RFID_UNREGISTERED`

However, since the AppDaemon app only needs to know whether fuel gauge data is trustworthy for a given slot, a simpler heuristic is sufficient: **if `start_g > 0` in the event payload for a slot, treat it as having valid fuel gauge data (RFID); if `start_g == 0`, treat it as non-RFID.** This avoids coupling to the reconciler's status vocabulary entirely.

---

## Q4 — Fuel Gauge for Non-RFID Slots

### Live sensor values (confirmed)

| Entity | State | Notes |
|---|---|---|
| `sensor.p1s_tray_5_fuel_gauge_remaining` | **0.0 g** | Non-RFID (tray_weight = 0) |
| `sensor.p1s_tray_6_fuel_gauge_remaining` | **0.0 g** | Non-RFID (tray_weight = 0) |

### Tray sensor attributes (slots 5 & 6)

| Attribute | Slot 5 (AMS 128 tray 1) | Slot 6 (AMS 129 tray 1) |
|---|---|---|
| `tray_weight` | `"0"` | `"0"` |
| `remain` | `-1` | `-1` |
| `tag_uid` | `"0000000000000000"` | `"0000000000000000"` |
| `empty` | `false` | `false` |

### Implication for consumption tracking

Non-RFID slots will always have `start_g == 0` in the fuel gauge snapshot. The init automation seeds from fuel gauge first, so these slots get zero. **Non-RFID consumption must be derived from the total `print_weight_g` minus RFID-measured consumption.**

---

## Q5 — input_text 255 Char Limit

### Helper definitions

| Entity | `max` | Current Value | Length |
|---|---|---|---|
| `p1s_tray_remaining_start_json` | 255 | `{"4":420.0}` | 12 |
| `p1s_tray_remaining_end_json` | 255 | `{}` | 2 |
| `p1s_print_job_key` | 100 | `1772209097.313429_●●_4x6_Double_Hei...` | ~51 |
| `p1s_trays_used_this_print` | 255 | `sensor.p1s_01p00c5a3101668_ams_1_tray_4` | ~43 |

### Worst-case analysis

A 6-slot JSON: `{"1": 1234.56, "2": 1234.56, "3": 1234.56, "4": 1234.56, "5": 1234.56, "6": 1234.56}` is ~88 characters — well within 255.

**No truncation risk.** The event payload is a HA event, not an `input_text` write, so there is no size limit on the event data itself. The JSON values were already persisted in the helpers before being copied into the event payload.

### Observation: `end_json` is `{}`

The last print's end JSON is empty. This is expected behavior — the finish automation computes `end_g` per-slot from fuel gauge sensors in real time (line 902) and writes to `input_number.p1s_end_slot_{N}_g`, but **does not populate `end_json`**. The `end_json` helper is unused by the finish automation's current logic. The event payload should carry individual slot end values, not `end_json`.

### Observation: `trays_used_this_print` format

The `p1s_record_trays_used_during_print` automation (line 465) now writes **comma-separated slot numbers** (`"1,4"` not entity IDs). The current value `sensor.p1s_01p00c5a3101668_ams_1_tray_4` is a legacy value from before the automation was fixed — it auto-cleans on next print (line 501–502 strips non-numeric values).

---

## Q6 — Finish Automation Modification

### Current action flow (lines 782–976)

| Line | Action |
|---|---|
| 784–788 | Checkpoint: `ENTERED_FIRST__BUILD_JSONFREE` |
| 790–797 | Reset debug trigger |
| 799–801 | Clear `p1s_print_active` mutex |
| 803–832 | Check if any slot has start data; **stop** if not |
| 839–940 | **Repeat loop (slots 1–6):** compute end_g, used_g, conditionally call `spoolman.use_spool_filament` |
| **937–940** | **`spoolman.use_spool_filament` — to be replaced** |
| 942 | `script.reload_spoolman_integration` |
| 949–970 | Build and send notification |
| 971–975 | Checkpoint: `complete` |

### Required changes

**Step 1 — Remove `spoolman.use_spool_filament` call (lines 937–940):**

The HA service call inside the repeat loop must be removed. AppDaemon will own the Spoolman write.

Keep the checkpoint write at lines 932–936 (`slot{{ N }}_decrement_{{ used_g }}g`) as a diagnostic marker — it documents what *would* have been written.

**Step 2 — Fire `P1S_PRINT_USAGE_READY` after the loop, before Spoolman reload:**

Insert between line 941 (end of repeat) and line 942 (`script.reload_spoolman_integration`):

```yaml
    - event: P1S_PRINT_USAGE_READY
      event_data:
        job_key: "{{ states('input_text.p1s_print_job_key') }}"
        task_name: "{{ states('sensor.p1s_01p00c5a3101668_task_name') }}"
        print_weight_g: "{{ states('sensor.p1s_01p00c5a3101668_print_weight') | float(0) }}"
        trays_used: "{{ states('input_text.p1s_trays_used_this_print') }}"
        start_json: "{{ states('input_text.p1s_tray_remaining_start_json') }}"
        end_json: "{}"
        print_status: "{{ trigger_state }}"
        is_failed: "{{ is_failed }}"
        should_decrement: "{{ should_decrement }}"
```

### Design decision: `end_json` vs per-slot `input_number`

The current automation does **not** build an `end_json` — it writes per-slot end values to `input_number.p1s_end_slot_{N}_g`. Two options:

**Option A (Recommended):** Add a template block after the repeat loop that builds `end_json` from `input_number.p1s_end_slot_{1-6}_g` and includes it in the event payload. This keeps all consumption data self-contained in the event.

**Option B:** Have AppDaemon read `input_number.p1s_end_slot_{N}_g` directly. This reintroduces helper reads in AppDaemon, which the agreed architecture avoids.

### Trigger changes needed?

**None.** The existing trigger (`from: [running, printing, pause, paused]` with no `to:` constraint) already fires for `failed`, `error`, and `canceled` states. The `is_failed` and `should_decrement` flags in the event payload give AppDaemon everything it needs to decide.

---

## Q7 — apps.yaml Entry

```yaml
ams_print_usage_sync:
  module: ams_print_usage_sync
  class: AmsPrintUsageSync
  enabled: true
  spoolman_base_url: http://192.168.4.124:7912
  dry_run: false
  # dry_run: true   # Set true to log consumption without writing to Spoolman
  min_consumption_g: 2
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Kill-switch for the app |
| `spoolman_base_url` | str | — | Spoolman API base URL (no trailing slash) |
| `dry_run` | bool | `false` | Log consumption calculations without PUTting to Spoolman |
| `min_consumption_g` | float | `2` | Skip writes below this threshold (noise filter) |

Uses `spoolman_base_url` (not `spoolman_url`) to match `ams_rfid_guard` and `spoolman_dropdown_sync` conventions.

No helper entity configuration needed — all data arrives via the event payload.

---

## Gaps and Risks

### G1 — `end_json` is not populated by the finish automation

The automation computes `end_g` per slot and writes to `input_number.p1s_end_slot_{N}_g` but never assembles these into `end_json`. The event payload must either:
- (a) Build `end_json` inline from the per-slot `input_number` values (recommended), or
- (b) Carry each slot's `start_g`, `end_g`, and `used_g` individually

**Risk:** If `end_json` is sent as `"{}"`, AppDaemon cannot compute RFID consumption.
**Mitigation:** Add a template step after the repeat loop that builds the JSON.

### G2 — Non-RFID consumption allocation is best-effort

For non-RFID slots, there is no per-slot fuel gauge. Consumption is estimated as:

```
nonrfid_share = print_weight_g - sum(rfid_used_g)
```

If multiple non-RFID slots are active, the allocation strategy must be defined:
- Equal split across active non-RFID slots?
- Proportional to Spoolman `remaining_weight`?
- All to the "primary" tray?

**Risk:** Inaccurate per-spool tracking for multi-material non-RFID prints.
**Mitigation:** Start with equal split; flag for manual reconcile if > 2 non-RFID slots active.

### G3 — Deduplication storage

AppDaemon needs to remember processed `job_key` values to avoid double-writes on restart or event replay. Options:
- In-memory set (lost on restart — safe because restarts don't replay events)
- Persistent file or Spoolman spool `extra` field
- HA `input_text` helper

**Risk:** Low. AppDaemon events are not replayed on restart. In-memory dedup is likely sufficient.
**Mitigation:** Use an in-memory set. If restarts become an issue, persist to a file.

### G4 — `spoolman.use_spool_filament` removal timing

The HA service call (line 937) and the AppDaemon `PUT /api/v1/spool/{id}/use` must not both be active simultaneously, or consumption will be double-counted.

**Risk:** If the automation is deployed before the AppDaemon app is ready, consumption tracking stops. If both are active, consumption is doubled.
**Mitigation:** Deploy in two phases:
1. Deploy AppDaemon app with `dry_run: true`, verify logs
2. Remove the HA service call and add the event fire, deploy automation
3. Set `dry_run: false`, verify end-to-end

### G5 — Spoolman endpoint is PUT, not POST

The endpoint is `PUT /api/v1/spool/{id}/use`, not `POST`. Any HTTP helper code must use the correct method.

### G6 — `print_weight_g` sensor accuracy

`sensor.p1s_01p00c5a3101668_print_weight` reports the total filament weight for the print job (from the slicer). This is the *estimated* weight, not actual measured consumption. For RFID slots we use fuel gauge deltas (more accurate). For non-RFID, we fall back to the slicer estimate minus RFID-measured usage.

**Risk:** Slicer estimate can differ from actual usage (purge tower, failed layers, etc.).
**Mitigation:** Acceptable for non-RFID best-effort tracking. Log the delta for monitoring.

### G7 — `trays_used_this_print` legacy format

The helper may contain legacy entity ID strings from before the automation fix. The auto-clean logic (line 501–502) strips non-numeric values on the next print, but the *current* value in the event could be stale.

**Risk:** AppDaemon receives entity IDs instead of slot numbers.
**Mitigation:** Parse defensively — extract digits, ignore non-numeric tokens.

---

## Recommended Implementation Order

| Phase | Description | Files Changed |
|---|---|---|
| **P8a** | Create `ams_print_usage_sync.py` skeleton: event listener, payload parsing, RFID consumption calc, `dry_run` logging, dedup. No Spoolman writes yet. | `appdaemon/apps/ams_print_usage_sync.py`, `appdaemon/apps/apps.yaml` |
| **P8b** | Add non-RFID consumption allocation logic (equal split). Add Spoolman `PUT` integration with `min_consumption_g` gate. | `appdaemon/apps/ams_print_usage_sync.py` |
| **P8c** | Modify finish automation: build `end_json` from per-slot `input_number`, fire `P1S_PRINT_USAGE_READY` event, remove `spoolman.use_spool_filament` call. | `automations.yaml` |
| **P8d** | Tests for `ams_print_usage_sync.py`: RFID consumption, non-RFID allocation, dedup, dry_run, min threshold, failed print handling. | `tests/test_ams_print_usage_sync.py` |
| **P8e** | End-to-end: deploy `dry_run: true`, trigger test print, verify logs, then flip to `dry_run: false`. | Deploy + verify |
