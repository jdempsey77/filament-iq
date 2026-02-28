# ANALYZE — Dashboard 3D Printer View: Audit Against Current System

**Date:** 2026-02-28
**Scope:** Read-only audit of the 3D Printer view in `dashboards/dashboard.test.storage.yaml`
**No files were edited.**

---

## Q1 — Slot Button Audit: Slots 1–4 (AMS Pro)

All four slot buttons use the same pattern (example for slot 1, lines 449–565):

### Primary display

Shows the **tray sensor state** — the Bambu firmware filament name:

```yaml
primary: "{{ states('sensor.p1s_01p00c5a3101668_ams_1_tray_1') }}"
```

This is the Bambu-reported filament name (e.g., "Overture Matte PLA", "Bambu PLA Basic"). It comes from the printer firmware, not Spoolman.

### Secondary display

Shows remaining grams from the Spoolman-backed template sensor:

```yaml
secondary: "{{ states('sensor.ams_slot_1_remaining_g') }} g remaining"
```

### What `sensor.ams_slot_N_remaining_g` resolves to

**Template sensor** defined in `configuration.yaml` (lines 963–1001):

```
state_attr('sensor.spoolman_spool_' ~ states('input_text.ams_slot_N_spool_id'), 'remaining_weight')
  | default(states('sensor.spoolman_spool_' ~ states('input_text.ams_slot_N_spool_id')))
  | float(0)
  | round(0)
```

The chain is: `input_text.ams_slot_N_spool_id` → builds `sensor.spoolman_spool_{id}` → reads `remaining_weight` attribute from the Spoolman integration entity.

### RFID vs non-RFID compatibility

**Works for both.** The sensor reads from Spoolman via the reconciler-managed `spool_id` helper. It does not use the fuel gauge or any RFID-specific mechanism. If `spool_id` is 0 or invalid, it defaults to 0g.

### Current live values

| Slot | Tray Sensor State | remaining_g | Status |
|---|---|---|---|
| 1 | Overture Matte PLA | 1000g | NON_RFID_REGISTERED |
| 2 | Bambu PLA Basic | 110g | OK |
| 3 | Generic PLA | 1000g | NON_RFID_REGISTERED |
| 4 | Bambu PLA Basic | 150g | OK |

---

## Q2 — Slot Button Audit: Slots 5–6 (AMS HT)

Identical pattern to slots 1–4, in a separate horizontal-stack section (lines 1150–1460).

### Tray entities

| Slot | Tray Entity |
|---|---|
| 5 | `sensor.p1s_01p00c5a3101668_ams_128_tray_1` |
| 6 | `sensor.p1s_01p00c5a3101668_ams_129_tray_1` |

### Primary and secondary

Same as Q1 — primary shows Bambu firmware filament name, secondary shows `sensor.ams_slot_{5,6}_remaining_g`.

### Current live values

| Slot | Tray Sensor State | remaining_g | Status |
|---|---|---|---|
| 5 | Overture PLA | 349g | NON_RFID_REGISTERED |
| 6 | Generic PETG | 0g | NEEDS_MANUAL_BIND |

### Findings

- Slot 6 shows `0g` because its status is `NEEDS_MANUAL_BIND` — no spool is bound (`spool_id` = 0), so the template defaults to 0.
- The `remaining_g` sensor works correctly for non-RFID slots when a spool is bound (slot 5 = 349g).
- Popup markdown shows **deprecated location names**: `AMS2_HT_Slot1` and `AMS2_HT_Slot2` (lines 1177, 1227, 1372, 1422). The canonical locations are now `AMS1_Slot5` and `AMS1_Slot6`.

---

## Q3 — Vendor Data Gap

### No vendor entity per slot exists

There is no `input_text.ams_slot_N_vendor` or any per-slot vendor template sensor.

### Vendor data IS available via Spoolman integration

The Spoolman spool entity exposes vendor information as attributes. Confirmed for `sensor.spoolman_spool_45` (slot 1's bound spool):

| Attribute | Value |
|---|---|
| `filament_vendor_name` | `Overture` |
| `filament_vendor_id` | (integer) |
| `filament_name` | `Matte PLA Light Grey` |
| `filament_material` | `PLA` |
| `filament_color_hex` | `898989` |

### How to access vendor in dashboard templates

No new entity needed — pure dashboard template change:

```jinja
{{ state_attr('sensor.spoolman_spool_' ~ states('input_text.ams_slot_N_spool_id'), 'filament_vendor_name') | default('—') }}
```

### Existing unused `sensor.ams_slot_N_name` sensors

Template sensors `sensor.ams_slot_1_name` through `sensor.ams_slot_6_name` are defined in `configuration.yaml` (lines 960–997) and read `filament_name` from Spoolman. **These are NOT used anywhere in the dashboard.** The slot buttons show the tray sensor state (Bambu firmware name) instead.

---

## Q4 — Stale or Broken References

### Deprecated location names in popup markdown

| Line(s) | Current | Should Be |
|---|---|---|
| 1177, 1227 | `AMS2_HT_Slot1` | `AMS1_Slot5` |
| 1372, 1422 | `AMS2_HT_Slot2` | `AMS1_Slot6` |

These are cosmetic (markdown text only, not entity references), but are confusing since the reconciler uses canonical `AMS1_Slot{5,6}` names.

### "Needs Action" conditional missing non-RFID OK states

The detail section cards (lines 1554–1562, repeated for each slot) show "Needs Action" unless status is one of:

```yaml
state_not:
  - "OK"
  - "OK: FIXED_EXPECTED"
  - ""
  - unknown
  - unavailable
```

**Missing from the OK list:**
- `NON_RFID_REGISTERED` — the normal OK state for non-RFID slots
- `OK_NON_RFID_REGISTERED` — alternate non-RFID OK state
- `WAITING_FOR_CONFIRMATION` — debatable, but not an error state

**Impact:** All non-RFID slots (currently slots 1, 3, 5) permanently show "Needs Action" in the detail section even though they are correctly bound. This is the most visible bug.

### Scripts and automation references — all valid

| Entity | Exists | Notes |
|---|---|---|
| `script.reload_spoolman_integration` | Yes | Last triggered 2026-02-27 |
| `script.refresh_ams_spool_list` | Yes | Last triggered 2026-02-26 |
| `script.ams_slot_1_assign_and_update` | Yes | State: off |
| `script.ams_slot_{2-6}_assign_and_update` | Yes | Defined in scripts.yaml |
| `input_button.p1s_rfid_reconcile_now` | Yes | Last triggered 2026-02-28 |

No references to removed `automation.p1s_phase_2a_*` found in this dashboard.

### `sensor.ams_slot_6_remaining_g` returning 0

This is correct behavior — slot 6 has no bound spool (`NEEDS_MANUAL_BIND`), so the template defaults to 0. Not a broken reference.

---

## Q5 — Active Slot Highlighting

### `active` attribute confirmed present

Verified on `sensor.p1s_01p00c5a3101668_ams_1_tray_1`:

```
active: false
```

The attribute exists on all tray sensor entities (provided by the Bambu P1S integration).

### card_mod logic (example from line 555)

```jinja
{% set c = state_attr('sensor.p1s_01p00c5a3101668_ams_1_tray_1', 'color') %}
ha-card {
  --card-mod-icon-color: {{ c | default('rgb(var(--rgb-state-entity))') }};
  {% if state_attr('sensor.p1s_01p00c5a3101668_ams_1_tray_1', 'active') %}
  font-weight: bold;
  border-left: 3px solid var(--accent-color);
  {% endif %}
}
```

**Works correctly.** The `active` attribute is a boolean, and the card_mod template will highlight the active slot with a bold left border during printing.

---

## Q6 — Popup Manage Dialogs

### Helper entities — all exist

Each slot popup (browser_mod) references these helpers:

| Entity Pattern | Example Slot 1 | Exists | Current State |
|---|---|---|---|
| `input_select.ams_slot_N_select_spool` | `input_select.ams_slot_1_select_spool` | Yes | "— Select spool —" |
| `input_select.ams_slot_N_spool_type` | `input_select.ams_slot_1_spool_type` | Yes | "Bambu Lab (plastic)" |
| `input_number.ams_slot_N_filament_id` | `input_number.ams_slot_1_filament_id` | Yes | 1.0 |
| `input_number.ams_slot_N_gross_weight` | `input_number.ams_slot_1_gross_weight` | Yes | 793.5 |

Confirmed for all 6 slots.

### Are these the right mechanism post-reconciler?

**Partially.** The popup "Assign & Update" button calls `script.ams_slot_N_assign_and_update`, which is the **legacy pre-reconciler manual assignment** mechanism. This script writes directly to Spoolman — it bypasses the reconciler.

Post-reconciler, manual assignment should go through:
1. Setting the `spool_id` helper (which the reconciler owns)
2. Or using the reconciler's manual bind workflow

The legacy popup mechanism still *works* (the scripts exist and are functional), but it creates a dual-authority problem: the script writes to Spoolman, and the reconciler may overwrite the result on next poll.

### Manual bind TODO items

All 6 slot detail sections contain placeholder text:

```
Manual bind: TODO (missing input_select.ams_slot_N_manual_spool / bind script)
```

These TODOs were never implemented. The reconciler now provides the manual bind capability, but the dashboard doesn't surface it in the slot buttons' popup.

---

## Q7 — Missing Features vs Current System

### Features the system supports but the dashboard doesn't surface

| Feature | Available Entity | Surfaced? | Location Gap |
|---|---|---|---|
| **Reconciler status** | `input_text.ams_slot_N_status` | Only in detail section, NOT in main slot buttons | Main button secondary should show status |
| **Vendor/brand name** | `sensor.spoolman_spool_{id}` attr `filament_vendor_name` | No | Template-only change |
| **Spoolman filament name** | `sensor.ams_slot_N_name` (template sensor) | No (buttons show tray sensor state instead) | Template-only change |
| **RFID vs non-RFID indicator** | Derivable from status helper | No | Template-only change |
| **Filament color from Spoolman** | `sensor.spoolman_spool_{id}` attr `filament_color_hex` | No (icon color uses tray sensor `color` attr) | Template-only change |
| **Spool ID on main buttons** | `input_text.ams_slot_N_spool_id` | Only in detail section | Template-only change |
| **Expected spool ID** | `input_text.ams_slot_N_expected_spool_id` | Only in detail section (correct) | N/A |
| **Canonical location names** | Defined in reconciler | No (deprecated names in popup) | Text-only change |
| **Non-RFID OK state recognition** | Status values defined in reconciler | Broken (always shows "Needs Action") | Conditional fix |

### Structural gaps

1. **Two source-of-truth conflict:** Slot buttons show Bambu firmware names (tray sensor), while `sensor.ams_slot_N_name` shows Spoolman filament names. These can differ. The dashboard should show the Spoolman name as primary.

2. **Legacy popup vs reconciler:** The popup "Assign & Update" workflow bypasses the reconciler. There's no UI surface for the reconciler's manual bind (which is triggered by `input_button.p1s_rfid_reconcile_now`).

3. **No print usage tracking visibility:** P8 (print usage sync) runs in the background but the dashboard doesn't show consumption history or last print summary.

---

## Prioritized Changes

### P1 — Fix "Needs Action" false positives (HIGH — functional bug)

**Type:** Dashboard YAML only

Add non-RFID OK states to the conditional's `state_not` list in both the detail section and the TEST overview section (24 instances across 12 slot cards):

```yaml
state_not:
  - "OK"
  - "OK: FIXED_EXPECTED"
  - "NON_RFID_REGISTERED"
  - "OK_NON_RFID_REGISTERED"
  - ""
  - unknown
  - unavailable
```

### P2 — Add vendor/brand to slot button secondary (MEDIUM — information gap)

**Type:** Dashboard YAML only (template change)

Replace secondary line:

```yaml
secondary: >-
  {{ states('sensor.ams_slot_1_remaining_g') }} g remaining
```

With:

```yaml
secondary: >-
  {% set sid = states('input_text.ams_slot_1_spool_id') %}
  {% set spool = 'sensor.spoolman_spool_' ~ sid %}
  {{ state_attr(spool, 'filament_vendor_name') | default('') }}
  {{ states('sensor.ams_slot_1_remaining_g') }}g remaining
```

### P3 — Fix deprecated location names in popups (LOW — cosmetic)

**Type:** Dashboard YAML only (text change)

| Change | From | To |
|---|---|---|
| Lines 1177, 1227 | `AMS2_HT_Slot1` | `AMS1_Slot5` |
| Lines 1372, 1422 | `AMS2_HT_Slot2` | `AMS1_Slot6` |

### P4 — Show reconciler status on main slot buttons (MEDIUM — visibility)

**Type:** Dashboard YAML only

Add a tertiary line or badge showing `input_text.ams_slot_N_status` on the main slot buttons, color-coded (green for OK states, yellow for pending, red for conflicts).

### P5 — Use Spoolman filament name instead of tray sensor state (LOW — correctness)

**Type:** Dashboard YAML only

Replace primary from `states('sensor.p1s_...ams_1_tray_1')` with `states('sensor.ams_slot_1_name')` (the existing unused template sensor). This shows the Spoolman-tracked filament name rather than the Bambu firmware label.

### P6 — Add RFID/non-RFID indicator (LOW — informational)

**Type:** Dashboard YAML only

Add an icon badge or text indicator showing whether each slot is RFID or non-RFID. Derivable from status helper: `NON_RFID_REGISTERED` → non-RFID, `OK` → RFID.

---

## Entity Requirements Summary

| Change | New HA Entities Needed? | Notes |
|---|---|---|
| P1 (fix false positives) | No | Dashboard conditional fix only |
| P2 (vendor name) | No | Available via existing `filament_vendor_name` attr |
| P3 (location names) | No | Text change only |
| P4 (status on buttons) | No | Available via existing `input_text.ams_slot_N_status` |
| P5 (Spoolman name) | No | Available via existing `sensor.ams_slot_N_name` |
| P6 (RFID indicator) | No | Derivable from existing status helper |

**All proposed changes are pure dashboard YAML changes. No new template sensors, input helpers, or configuration.yaml changes are required.**
