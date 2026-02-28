# Spool Matching Specification v3
## Canonical Reference — Reconciler Behavior

This document is the authoritative specification for how the AMS reconciler identifies and binds spools to slots. All reconciler code must implement this spec exactly. When code and spec conflict, the spec wins.

**Revision history:**
- v1: Initial spec derived from design sessions
- v2: Updated based on deep code analysis (ANALYZE report, 2026-02-27). Key changes: canonicalizer gap identified and made P0 fix, HT path clarified as shared capability not slot-specific, safety poll made status-only, tie-break and EOL edge cases tightened, previous-occupant guard added.
- v3: Updated 2026-02-28. Key changes: unified code path for all 6 slots (proven by sensor schema analysis), filament_id as primary non-RFID signal, color demoted to fuzzy tiebreaker, generic sentinel short-circuit, tray_uuid identified as primary RFID identity (spool SN), HT-specific code paths and fingerprint format retired, Bambu vendor exclusion rule tightened, remain=-1 sentinel defined, color normalization rule formalized.

---

## P0 Fix — Canonicalizer Module (COMPLETE)

`spoolman_extra_canonicalizer.py` has been created and deployed. Import failure is a hard startup error (RuntimeError). All RFID UID and UUID encoding/decoding goes through the canonicalizer. This was the root cause of intermittent recognition failures. **Do not revert.**

---

## Design Principles

- The reconciler is **event-driven**, not polling-driven. It fires on tray state change events and manual triggers only.
- Spools are **never auto-created** by the reconciler. A spool that does not exist in Spoolman is a data gap, not a creation opportunity. The correct response is always NEEDS_ACTION.
- Identity is **explicit and sticky**. Once a spool is bound to a slot, the binding persists until a real physical change is detected.
- The candidate pool is **ordered and exhausted top-down**. Lower tiers are only searched when higher tiers yield no match.
- All encoding and decoding of Spoolman extra fields **must go through the canonicalizer**. Never use raw `json.dumps()` or manual string manipulation for extra field values.
- **One code path for all 6 slots.** No HT-specific branches. Slot identity is configuration, not logic.

---

## Hardware Model — Unified Slot Architecture (NEW in v3)

### All 6 slots are equivalent

The P1S system has two AMS units:
- **AMS 2 Pro** — slots 1–4, entities `sensor.p1s_*_ams_1_tray_1` through `ams_1_tray_4`
- **AMS HT** — slots 5–6, entities `sensor.p1s_*_ams_128_tray_1` and `ams_129_tray_1`

**All 6 tray sensors report an identical attribute schema**, verified from live production sensor data:
```
active, color, empty, filament_id, k_value, tray_weight, name,
nozzle_temp_min, nozzle_temp_max, remain, remain_enabled,
tag_uid, tray_uuid, type, icon, friendly_name
```

The AMS HT is architecturally a single-slot AMS. It is not a different hardware class. There is zero justification for separate code paths.

### Slot identity is configuration, not code

Which entity maps to which slot number lives in a config table only:
```python
TRAY_ENTITY_BY_SLOT = {
    1: "sensor.p1s_*_ams_1_tray_1",
    2: "sensor.p1s_*_ams_1_tray_2",
    3: "sensor.p1s_*_ams_1_tray_3",
    4: "sensor.p1s_*_ams_1_tray_4",
    5: "sensor.p1s_*_ams_128_tray_1",
    6: "sensor.p1s_*_ams_129_tray_1",
}
```

The reconciliation logic never branches on slot number. Any capability that applies to one slot applies to all.

### Retired in v3

The following HT-specific constructs must be removed:
- `_compute_ht_fingerprint` — replaced by `_build_tray_signature` for all slots
- `HT_GUARD` — replaced by universal all-zero identity check
- HT-only pending confirmation — now applies to all slots uniformly
- HT-only confidence gating — now applies to all slots uniformly
- Legacy `NONRFID|TYPE|COLOR|STATE` tray_signature format — any stored helper value starting with `NONRFID|` must be cleared on deploy and logged as `LEGACY_SIGNATURE_CLEARED slot={slot}`

---

## Spool Identity Fields

### RFID spools

RFID spools have both identity fields populated with non-zero values:

| Field | Source | Role | Notes |
|-------|--------|------|-------|
| `tray_uuid` | RFID chip | **Primary identity** | Spool serial number (SN). Shown in Bambu Studio. Stable per spool. Verified: all-zero for non-RFID. |
| `tag_uid` | RFID chip | **Secondary identity** | Raw RFID hardware UID. Shown in Bambu Handy. Can get stuck/stale without physical reload. Used for Spoolman `rfid_tag_uid` enrollment. |

`tray_uuid` is the preferred sticky signal because it is the manufacturer's serial number — more stable than `tag_uid` which can exhibit `RFID_IDENTITY_STUCK` behavior.

**Non-RFID indicator:** Both fields are all-zero:
- `tag_uid == "0000000000000000"`
- `tray_uuid == "00000000000000000000000000000000"`

### Non-RFID spools

| Field | Reliability | Notes |
|-------|-------------|-------|
| `filament_id` | **High** (when non-generic) | Bambu catalog ID (e.g. `GFL05`). Primary non-RFID match signal when non-sentinel. |
| `type` | High | Material type (PLA, PETG, ABS). Always present. |
| `name` | Medium | Filament name from chip. May differ from Spoolman filament name. |
| `color` | **Low** | From chip. Does NOT reliably match Spoolman filament color (Spoolman colors come from external catalog). Never a hard match gate. |
| `remain` | Sentinel | `-1` means weight unknown. Not empty. Treat as no data. |

### Generic sentinel filament_ids (NEW in v3)

Bambu uses the `xx99` suffix for "unknown filament of this type":

| ID | Meaning |
|----|---------|
| `GFL99` | Generic PLA |
| `GFG99` | Generic PETG |
| `GFA99` | Generic ABS |
| Pattern | Any `filament_id` ending in `99` (case-insensitive) |

Generic sentinels **must short-circuit to NEEDS_ACTION immediately**, before any waterfall matching. There is no reliable signal to distinguish between multiple spools of the same generic type. Emit reason: `GENERIC_FILAMENT_NO_AUTO_MATCH`.

A spool whose name starts with "Generic" but whose `filament_id` is non-sentinel (e.g. `GFL05`) is NOT blocked — the specific filament_id is the signal, not the name.

---

## Color Normalization (NEW in v3)

Tray sensor reports color with `#` prefix and alpha channel: `#898989FF`

**Normalization rule (canonical):**
1. Strip leading `#`
2. If length is 8 (RRGGBBAA), strip last 2 characters (alpha channel)
3. Uppercase for Spoolman storage
4. Lowercase for HA_SIG

Examples:
- `#898989FF` → `898989`
- `#000000FF` → `000000`
- `#FFFFFFFF` → `FFFFFF`

**Color is never a hard match gate.** Spoolman filament colors come from an external catalog and do not reliably match sensor-reported colors. Color is used only as a fuzzy tiebreaker when multiple candidates remain after stronger signals have been applied.

---

## Spool Lifecycle

```
Order → Receive → Create in HA interface → Spoolman record created (location: New)
      → User opens spool, moves to Shelf in Spoolman
      → User inserts into AMS slot
      → Reconciler fires, matches and binds
      → In use (location: AMS1_SlotX etc.)
      → Removed from AMS → no location change (unless EOL)
      → Returned to Shelf by user if desired
      → ... repeat insertions ...
      → Spool hits 0g and is removed → location: Empty (excluded forever)
```

**Location meanings:**

| Location | Meaning | Eligible for matching? |
|---|---|---|
| `New` | Received but not yet opened/staged | Tier 3 only (exact match only) |
| `Shelf` | Opened, staged, ready to use | Tier 1 (primary) |
| `AMS1_Slot1..4` | In AMS 2 Pro, slots 1–4 | Tier 2 only if source slot tray state is `empty` |
| `AMS128_Slot1` | In AMS HT unit (slot 5) | Tier 2 only if source slot tray state is `empty` |
| `AMS129_Slot1` | In AMS HT unit (slot 6) | Tier 2 only if source slot tray state is `empty` |
| `Empty` | EOL — used up and removed | Never eligible at any tier |

---

## Reconciler Triggers

The reconciler fires on:
- Tray entity state change (insertion, removal, swap)
- Manual reconcile button press (`input_button.p1s_rfid_reconcile_now`)
- Event `AMS_RECONCILE_ALL`

**Safety poll:** `status_only=True`. Fires every 600 seconds. Detects drift and logs warnings. No Spoolman PATCHes. No helper writes. Emits `SAFETY_POLL_DRIFT` when slots are not OK. Recovery requires manual reconcile or tray event.

**Debounce:** 5–10 seconds after triggering event to allow sensor state to stabilize.

---

## Shared Capabilities — All 6 Slots (unified in v3)

These capabilities previously existed only in the HT path. They now apply uniformly to all 6 slots.

### 1. Pending confirmation (all non-RFID slots)

Before treating a tray identity change as real, require:
- 2 consecutive observations of the same signature, OR
- 10 seconds of stable signature

Stored in `tray_signature` as `PENDING:<count>:<epoch>:<signature>`.

### 2. Confidence gating (all non-RFID slots)

Reject tray attributes as insufficiently specific when:
- `type` is missing or empty, OR
- `color` is missing or empty, OR
- tray state starts with "GENERIC" **AND** `filament_id` is a generic sentinel (`xx99`)

A spool with state "Generic PLA" but `filament_id=GFL05` is NOT gated.
Set status to `LOW_CONFIDENCE_NO_AUTO_MATCH` and notify.

### 3. State-aware fingerprinting (all slots)

The tray signature must include the tray state string to prevent intermediate states from being treated as the same identity.

---

## Candidate Pool — Three-Tier Waterfall

The same waterfall applies to both RFID and non-RFID spools.

### Pre-flight checks (all slots, before waterfall)

1. **Tray empty?** → UNBOUND, reason `TRAY_EMPTY`. Stop.
2. **Tray unavailable/unknown?** → UNBOUND, reason `TRAY_UNAVAILABLE`. Stop.
3. **Generic sentinel filament_id?** (`xx99`) → NEEDS_ACTION, reason `GENERIC_FILAMENT_NO_AUTO_MATCH`. Stop.
4. **Confidence gate fails?** → NEEDS_ACTION, reason `LOW_CONFIDENCE_NO_AUTO_MATCH`. Stop.
5. **Sticky mapping valid?** → `tray_uuid` (RFID) or `_build_tray_signature` matches stored `tray_signature` AND `spool_id > 0` → Re-bind immediately, skip waterfall.

### Tier 1 — Shelf

Spools with `location == "Shelf"`. Primary and expected pool.

### Tier 2 — Current AMS Slots (Empty Confirmed)

Spools whose Spoolman location is a canonical AMS slot where the tray entity currently has state explicitly `empty`.

**Guard:** `empty` only. `unavailable` and `unknown` are not empty.

**Additional guard:** Spool must not be the current `spool_id` helper of any other slot whose tray is not empty.

### Tier 3 — New (Last Resort, Exact Match Only)

Spools with `location == "New"`. Exact matches only:
- RFID: `extra.rfid_tag_uid` decoded via canonicalizer must exactly match tray `tag_uid`
- Non-RFID: Spoolman comment must exactly match computed HA_SIG

Fuzzy matching never used against New pool. No exact match → NEEDS_ACTION.

### Excluded Always

- `location == "Empty"` — never candidates
- `archived == true` — never candidates

---

## Waterfall Execution

| Candidates found | Action |
|---|---|
| Exactly 1 | Bind |
| More than 1 | Apply tie-break. Resolved → bind. Unresolvable → fall to next tier |
| 0 | Fall to next tier |

All tiers exhausted → NEEDS_ACTION.

---

## Match Key — RFID Spools

1. Normalize tray `tag_uid` via canonicalizer
2. Find spools where `extra.rfid_tag_uid` (decoded via canonicalizer) equals tray UID
3. Sticky: if `tray_uuid` matches stored `tray_signature` → re-bind immediately

Rules:
- Canonicalizer mandatory before any comparison
- Spool with empty/sentinel `rfid_tag_uid` after decode is never an RFID candidate
- On first insertion, UID written to `extra.rfid_tag_uid` via canonicalizer encode path

---

## Match Key — Non-RFID Spools (revised in v3)

Applied in order. Stop at first step that produces exactly one candidate.

**Step 1 — Sticky mapping fast path:**
`_build_tray_signature` output matches stored `tray_signature` helper → re-bind immediately.

**Step 2 — HA_SIG Flow B (cross-slot re-insertion):**
Compute HA_SIG from tray metadata. Find spools whose Spoolman comment matches exactly.

**Step 3 — filament_id exact match (NEW):**
If `filament_id` is non-generic AND Spoolman `filament.external_id` is populated:
- Match tray `filament_id` against `filament.external_id` exactly
- Eligible locations: Shelf only

**Step 4 — Vendor + material match:**
Match `filament.vendor.name` + `filament.material`. Eligible locations: Shelf only.

Bambu vendor spools **are eligible** when `filament_id` is non-generic (e.g. `GFA00`). The old rule excluding all Bambu vendor spools from non-RFID matching is retired. Bambu spools are excluded only when `filament_id` is generic sentinel — but those are already blocked by pre-flight.

**Step 5 — Color as fuzzy tiebreaker only:**
Applied only when multiple candidates remain after Steps 3–4. Tolerance-based comparison. Exact equality is not required and must not be enforced.

**Exclusions from non-RFID pool:**
- Spools with non-empty `rfid_tag_uid` after decode — RFID spools matched by UID only

---

## Tie-Break — Least Remaining Grams

1. Least `remaining_weight`
2. Equal → lowest Spoolman spool ID
3. `remaining_weight` null/missing → worse than any known value
4. All null/missing → cannot resolve → fall to next tier

---

## On Successful Bind

1. Write `spool_id` helper
2. Write `expected_spool_id` helper
3. Write `tray_signature` helper using `_build_tray_signature` (all 6 slots)
4. Write HA_SIG to Spoolman comment (idempotent)
5. Move Spoolman `location` to canonical slot location
6. Clear previous occupant (with guard)

### tray_signature format (unified — v3)

`_build_tray_signature` format for all 6 slots:
```
name|type|filament_id|color_hex|tag_uid
```
All values: lowercased, stripped, max 255 chars. Color normalized per §Color Normalization.

Examples:
- Non-RFID: `overture matte pla|pla|gfl05|898989|`
- RFID: `bambu pla basic|pla|gfa00|000000|a6ec1bde00000100`

**Legacy format retired:** Any `tray_signature` beginning with `NONRFID|` must be cleared on deploy. Log `LEGACY_SIGNATURE_CLEARED slot={slot}`.

### HA_SIG format (Spoolman comment)

```
HA_SIG=bambu|filament_id=<id>|type=<type>|color_hex=<hex>
```
All lowercase. Color 6-char normalized, no `#`. Example:
```
HA_SIG=bambu|filament_id=gfa00|type=pla|color_hex=00ae42
```

Returns `None` (no write) when `filament_id` is generic sentinel or any required field is missing.

### Previous occupant clearing guard

Only move previous occupant if:
- Its Spoolman location matches this slot's canonical location, AND
- It is not currently the `spool_id` of any other slot whose tray is not empty

Move to `Shelf` if `remaining_weight > 0`, `Empty` if `remaining_weight <= 0`.

---

## On Empty Tray (Removal)

1. Check `remaining_weight` of bound spool
2. `<= 0` → EOL: move to `Empty`
3. `> 0` or `== -1` (unknown) → no location change
4. Both cases: clear `spool_id` to `0`, `expected_spool_id` to `0`, `tray_signature` to `""`

`remain == -1` from tray sensor means weight unknown. Do not treat as 0 or empty.

---

## End of Life (EOL)

When `remaining_weight <= 0` and tray transitions to `empty`:
- Move Spoolman `location` to `Empty`
- `Empty` excluded from all tiers permanently
- HA_SIG preserved for historical reference

---

## No Match — NEEDS_ACTION

1. Set slot status to `UNBOUND: ACTION_REQUIRED`
2. Persistent HA notification with slot, tray signals, tier results, and resolution instruction
3. No auto-retry. User resolves via Spoolman then manual reconcile.

---

## Sticky Mapping — No Churn

If `tray_signature` unchanged AND `spool_id` points to valid spool → skip waterfall entirely.

Only re-run waterfall when tray identity actually changes (after pending confirmation).

---

## Mismatch Detection

RFID UID mismatch or non-RFID material mismatch:
- Status: `CONFLICT: MISMATCH`, clear helpers, notify, require user intervention. No auto-correction.

---

## UID Integrity

### Write paths

`rfid_tag_uid` written only via `_manual_enroll`. Automatic reconcile never writes `rfid_tag_uid`.

### Overwrite protection

Refuse to overwrite existing different UID. Log:
```
RFID_UID_CONFLICT spool_id={id} existing_uid={existing} tray_uid={tray} reason=refuse_overwrite
```

### Encoding

Correct: `"\"1D33DD3B00000100\""` (length 18). Empty encoded `"\"\""` (length 2) treated as absent. Canonicalizer enforces single-layer encoding. Import failure = hard startup error.

---

## Spoolman Data Requirements

### filament.external_id population (NEW in v3)

Non-RFID filament_id exact match (Step 3) requires `filament.external_id` populated with Bambu catalog IDs.

Until populated, matching falls back to vendor + material + fuzzy color. Generic sentinels always short-circuit.

**Priority IDs to populate:**
- `GFA00` — Bambu PLA Basic
- `GFL04` — Overture PLA
- `GFL05` — Overture Matte PLA
- All other non-`xx99` filament_ids seen in production

### Location conventions

| Location | Meaning |
|----------|---------|
| `Shelf` | On shelf, available for loading |
| `AMS1_Slot1` through `AMS1_Slot4` | Loaded in AMS 2 Pro |
| `AMS128_Slot1` | Loaded in AMS HT slot 5 |
| `AMS129_Slot1` | Loaded in AMS HT slot 6 |
| `New` | Received, not yet staged |
| `Empty` | EOL — permanently excluded |

---

## What the Reconciler Must Never Do

- Auto-create Spoolman spool records
- Auto-select from `location == "Empty"`
- Overwrite existing RFID UID binding with different UID
- Silently succeed when truth guard fires
- Use raw `json.dumps()` for extra field encoding
- Treat `unavailable` or `unknown` tray state as `empty`
- Treat `remain == -1` as zero or empty
- Write to Spoolman when tray identity signals are inconsistent
- PATCH `extra` without reading current extra first
- Move spool whose location does not match expected source location
- Run fuzzy matching against `New` location pool
- Branch on slot number in reconciliation logic
- Use `_compute_ht_fingerprint` or write `NONRFID|` format signatures
- Use color as a hard match gate

---

## Implementation Priorities

| Priority | Item | Status | Rationale |
|---|---|---|---|
| **P1** | Canonicalizer | ✅ COMPLETE | Root cause fix. Hard import failure. 39 tests. |
| **P2** | Safety poll status_only + remove auto-create | ✅ COMPLETE | Aligns with event-driven principle. |
| **P3** | Startup readiness waiter | ✅ COMPLETE | Fixes DomainException startup race. 420s budget, 3-condition probe. |
| **P4** | Unified code path — retire HT-specific code | **NEXT** | One path for all 6 slots. Retire `_compute_ht_fingerprint`, `HT_GUARD`. Extract pending confirmation + confidence gating to shared methods. Clear legacy `NONRFID|` tray_signatures on deploy. Parameterized tests across slots 1–6. |
| **P5** | Non-RFID matching improvements | After P4 | filament_id as primary signal. Color as fuzzy tiebreaker. Sentinel short-circuit. Fix Bambu vendor exclusion. |
| **P6** | Three-tier waterfall | After P5 | Implement Tier 2 (AMS slot empty-confirmed). |
| **P7** | Previous occupant clearing guard | After P6 | Prevents irreversible moves of in-use spools. |
