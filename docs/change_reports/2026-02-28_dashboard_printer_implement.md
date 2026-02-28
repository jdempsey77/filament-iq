# IMPLEMENT — Dashboard 3D Printer View: P1 + P2 + P3

**Date:** 2026-02-28
**Files changed:** `dashboards/dashboard.test.storage.yaml`
**Baseline:** Initialized from `dashboards/dashboard.stage.yaml` (2936 lines)

---

## Step 0 — HEALTHY_STATUSES (Canonical List)

Extracted from `appdaemon/apps/ams_rfid_reconcile.py` (lines 109–152):

### All STATUS_ constants

| Constant | String Value | Healthy? |
|---|---|---|
| `STATUS_OK` | `OK` | Yes |
| `STATUS_OK_FIXED_EXPECTED` | `OK: FIXED_EXPECTED` | Yes |
| `STATUS_NON_RFID_REGISTERED` | `NON_RFID_REGISTERED` | Yes |
| `STATUS_OK_NONRFID` | `OK_NON_RFID_REGISTERED` | Yes |
| `STATUS_MISMATCH` | `CONFLICT: MISMATCH` | No (bound but metadata conflict) |
| `STATUS_UNBOUND_NO_TAG` | `UNBOUND: no_tag` | No |
| `STATUS_UNBOUND_TRAY_UNAVAILABLE` | `UNBOUND: TRAY_UNAVAILABLE` | No |
| `STATUS_UNBOUND_MANUAL_CREATE` | `UNBOUND: manual_create_required` | No |
| `STATUS_UNBOUND_ACTION_REQUIRED` | `UNBOUND: ACTION_REQUIRED` | No |
| `STATUS_UNBOUND_FLOW_B_PARTIAL` | `UNBOUND: FLOW_B_PARTIAL` | No |
| `STATUS_CONFLICT_DUPLICATE_UID` | `CONFLICT: DUPLICATE_UID` | No |
| `STATUS_CONFLICT_MISSING_CANONICAL` | `CONFLICT: missing_canonical_location` | No |
| `STATUS_CONFLICT_AMBIGUOUS_METADATA` | `CONFLICT: AMBIGUOUS_METADATA_NO_UNREGISTERED` | No |
| `STATUS_PENDING_RFID_READ` | `PENDING_RFID_READ` | No |
| `STATUS_WAITING_CONFIRMATION` | `WAITING_FOR_CONFIRMATION` | No |
| `STATUS_NEEDS_MANUAL_BIND` | `NEEDS_MANUAL_BIND` | No |
| `STATUS_LOW_CONFIDENCE` | `LOW_CONFIDENCE_NO_AUTO_MATCH` | No |
| `STATUS_RFID_IDENTITY_STUCK` | `RFID_IDENTITY_STUCK` | No |

### HEALTHY_STATUSES

```yaml
- "OK"
- "OK: FIXED_EXPECTED"
- "NON_RFID_REGISTERED"
- "OK_NON_RFID_REGISTERED"
```

---

## Step 2 — Spoolman Entity Structure (Confirmed)

**Entity pattern:** `sensor.spoolman_spool_{id}` (e.g., `sensor.spoolman_spool_45`)

**Vendor attribute:** `filament_vendor_name`

Confirmed via HA API for `sensor.spoolman_spool_45`:

| Attribute | Value |
|---|---|
| `filament_vendor_name` | `Overture` |
| `filament_name` | `Matte PLA Light Grey` |
| `filament_material` | `PLA` |
| `filament_color_hex` | `898989` |

**Template for vendor lookup:**

```jinja
{% set sid = states('input_text.ams_slot_N_spool_id') %}
{% set vendor = state_attr('sensor.spoolman_spool_' ~ sid, 'filament_vendor_name') %}
{% if vendor and vendor not in ['unknown', 'unavailable', 'none', 'None'] %}{{ vendor }} · {% endif %}
```

---

## P1 — Fix "Needs Action" False Positives

### Finding: NOT APPLICABLE to stage baseline

The stage dashboard (`dashboard.stage.yaml`) does **not** contain `ams_slot_N_status` conditional cards. The "Needs Action" conditionals that showed false positives existed only in the old `dashboard.test.storage.yaml` file (in the "AMS Slot Overview (TEST)" section at lines 1536+), which was overwritten when we initialized from stage.

**No P1 changes were made.** If status-based conditionals are added to the dashboard in the future, they must include all 4 HEALTHY_STATUSES in the `state_not` list.

---

## P2 — Add Vendor to Slot Button Secondary Text

### Changes (6 instances)

For each of the 6 non-empty slot buttons, the secondary field was updated from:

```yaml
secondary: >-
  {{ states('sensor.ams_slot_N_remaining_g') }} g
  remaining
```

To:

```yaml
secondary: >-
  {% set sid = states('input_text.ams_slot_N_spool_id') %}
  {% set vendor = state_attr('sensor.spoolman_spool_' ~ sid, 'filament_vendor_name') %}
  {% if vendor and vendor not in ['unknown', 'unavailable', 'none', 'None'] %}{{ vendor }} · {% endif %}{{ states('sensor.ams_slot_N_remaining_g') }}g remaining
```

**Expected rendering:** `Overture · 1000g remaining` (with vendor) or `0g remaining` (without vendor, e.g., unbound slot)

| Slot | Line (approx) | Tray Entity |
|---|---|---|
| 1 | 1585 | `ams_1_tray_1` |
| 2 | 1694 | `ams_1_tray_2` |
| 3 | 1803 | `ams_1_tray_3` |
| 4 | 1912 | `ams_1_tray_4` |
| 5 | 2380 | `ams_128_tray_1` |
| 6 | 2577 | `ams_129_tray_1` |

---

## P3 — Fix HT Slot Popup Location Names

### Changes (4 instances)

| Line (approx) | From | To |
|---|---|---|
| 2345 | `AMS2_HT_Slot1` | `AMS1_Slot5` |
| 2396 | `AMS2_HT_Slot1` | `AMS1_Slot5` |
| 2542 | `AMS2_HT_Slot2` | `AMS1_Slot6` |
| 2593 | `AMS2_HT_Slot2` | `AMS1_Slot6` |

---

## Verification Results

```
=== 1. All 7 views present ===
  - title: Home
  - title: Main Floor
  - title: Basement
  - title: outside
  - title: 3D Printer
    title: 'Sonos '
    title: Outlets
PASS

=== 2. No old HT location names ===
grep "AMS2_HT_Slot" → 0 matches
PASS

=== 3. Vendor template present in all 6 slot buttons ===
grep -c "filament_vendor_name" → 6
PASS

=== 4. AMS1_Slot5/6 location names ===
4 instances (2 for Slot5, 2 for Slot6)
PASS

=== 5. File size ===
2942 lines (baseline 2936 + 6 lines for vendor templates)
PASS
```

---

## Deploy Instructions

| File changed | Deploy command |
|---|---|
| `dashboards/dashboard.test.storage.yaml` | `./scripts/manage_ha.sh --stage` |

### Post-deploy verification

1. Open `/lovelace-stage` → 3D Printer view
2. Verify slot buttons show vendor name (e.g., "Overture · 1000g remaining")
3. Verify HT slot popups show AMS1_Slot5 / AMS1_Slot6 location names
4. Verify all 7 views load without errors
