# Spool Matching Specification v4
## Canonical Reference — Reconciler Behavior

This document is the authoritative specification for how the AMS reconciler identifies and binds spools to slots. All reconciler code must implement this spec exactly. When code and spec conflict, the spec wins.

**Revision history:**
- v1: Initial spec derived from design sessions
- v2: Updated based on deep code analysis (ANALYZE report, 2026-02-27). Key changes: canonicalizer gap identified and made P0 fix, HT path clarified as shared capability not slot-specific, safety poll made status-only, tie-break and EOL edge cases tightened, previous-occupant guard added.
- v3: Updated 2026-02-28. Key changes: unified code path for all 6 slots (proven by sensor schema analysis), filament_id as primary non-RFID signal, color demoted to fuzzy tiebreaker, generic sentinel short-circuit, tray_uuid identified as primary RFID identity (spool SN), HT-specific code paths and fingerprint format retired, Bambu vendor exclusion rule tightened, remain=-1 sentinel defined, color normalization rule formalized.
- v4: Updated 2026-02-28. Key changes: **lot_nr migration** — `lot_nr` replaces all Spoolman `extra` fields as the single identity storage field for both RFID and non-RFID spools. `extra.rfid_tag_uid` and `extra.ha_spool_uuid` retired (read-only fallback during migration window, never written). `comment` freed for human use. Canonicalizer marked migration-only. Non-RFID sig simplified to `type|filament_id|color_hex` (name and tag_uid dropped). HA_SIG write to comment retired.

---

## P0 Fix — Canonicalizer Module (MIGRATION-ONLY as of v4)

`spoolman_extra_canonicalizer.py` remains deployed but is now **migration-only**. It is used only to read legacy `extra.rfid_tag_uid` and `extra.ha_spool_uuid` values during the migration fallback window. It must not be used for any new writes. Once all spools have `lot_nr` populated, the canonicalizer can be retired (see Legacy Field Cleanup task).

Module-level docstring must read:
```python
# MIGRATION ONLY — retire after all spools have lot_nr populated. See Legacy Field Cleanup task.
```

**Do not revert the canonicalizer. Do not add new write paths through it.**

---

## Design Principles

- The reconciler is **event-driven**, not polling-driven. It fires on tray state change events and manual triggers only.
- Spools are **never auto-created** by the reconciler. A spool that does not exist in Spoolman is a data gap, not a creation opportunity. The correct response is always NEEDS_ACTION.
- Identity is **explicit and sticky**. Once a spool is bound to a slot, the binding persists until a real physical change is detected.
- The candidate pool is **ordered and exhausted top-down**. Lower tiers are only searched when higher tiers yield no match.
- **Identity lives in `lot_nr` only.** No extra fields are written. No canonicalization required. Plain string PATCH.
- **One code path for all 6 slots.** No HT-specific branches. Slot identity is configuration, not logic.

---

## Hardware Model — Unified Slot Architecture

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

---

## Spool Identity — v4 Model

### The single identity field: `lot_nr`

`lot_nr` is a native Spoolman top-level string field. It is the **only** field the reconciler reads for identity matching and the **only** field it writes for identity enrollment. No extra fields. No encoding. Plain string PATCH.

| Spool type | `lot_nr` value | `comment` field |
|---|---|---|
| Bambu RFID | `tray_uuid` e.g. `38D1181E8F024FDA9D040D3BE3A20312` | Free for human notes |
| Non-RFID | Identity sig e.g. `PLA\|GFL05\|898989` | Free for human notes |

### RFID spool identity

RFID spools have both hardware fields populated with non-zero values:

| Field | Source | Role |
|---|---|---|
| `tray_uuid` | RFID chip data | **Primary identity** — spool factory serial. Stable, orientation-independent. Stored in `lot_nr`. |
| `tag_uid` | RFID chip hardware UID | **Retired** — no longer stored or matched. Orientation-dependent. |

**Non-RFID indicator:** Both fields are all-zero:
- `tag_uid == "0000000000000000"`
- `tray_uuid == "00000000000000000000000000000000"`

### Non-RFID spool identity sig

Format: `type|filament_id|color_hex`

All values lowercased, stripped. Color normalized (6 hex chars, no `#`, no alpha). Max 255 chars.

Examples:
- `pla|gfl05|898989`
- `petg|gfg99|ffffff`

**Fields dropped from sig vs v3:** `name` and `tag_uid` are no longer included. The sig is now purely filament-property-derived — stable across renames and orientation changes.

`_build_tray_signature` must be updated to produce this format. The v3 format `name|type|filament_id|color_hex|tag_uid` is retired for `lot_nr` storage. The v3 format may be retained internally as the `tray_signature` HA helper value (for sticky mapping), but must never be written to Spoolman `lot_nr`.

---

## Identity Resolution — `lot_nr` Primary + Legacy Fallback

### RFID path

```
tray_uuid = attrs.get("tray_uuid")
is_rfid = tray_uuid not in (None, "", "00000000000000000000000000000000")

1. Match tray_uuid against spool.lot_nr
   → Match found: BOUND. Done.

2. Fallback (migration): match tray tag_uid against extra.rfid_tag_uid (via canonicalizer)
   → Match found: write tray_uuid to lot_nr (plain PATCH). BOUND.

3. No match at any tier: UNBOUND → NEEDS_ACTION
```

### Non-RFID path

```
sig = build_lot_sig(type, filament_id, color_hex)  # type|filament_id|color_hex

1. Match sig against spool.lot_nr
   → Match found: BOUND. Done.

2. Fallback (migration): match computed HA_SIG against spool.comment
   → Match found: write sig to lot_nr (plain PATCH). BOUND.

3. No match at any tier: UNBOUND → NEEDS_ACTION
```

### Migration fallback rules

- Fallback paths are **read-only** — they never write to `extra` fields or `comment`
- Fallback paths **do write `lot_nr`** on successful match, promoting the spool to v4 identity
- Once `lot_nr` is populated, the fallback paths are never reached for that spool
- Fallback paths will be removed entirely after Legacy Field Cleanup task completes

---

## Generic Sentinel Filament IDs

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

## Color Normalization

Tray sensor reports color with `#` prefix and alpha channel: `#898989FF`

**Normalization rule (canonical):**
1. Strip leading `#`
2. If length is 8 (RRGGBBAA), strip last 2 characters (alpha channel)
3. Uppercase for Spoolman storage
4. Lowercase for sig and HA helper values

Examples:
- `#898989FF` → `898989`
- `#000000FF` → `000000`
- `#FFFFFFFF` → `FFFFFF`

**Color is never a hard match gate.** Used only as a fuzzy tiebreaker when multiple candidates remain after stronger signals have been applied.

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

## Shared Capabilities — All 6 Slots

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
- RFID: `lot_nr` must exactly match tray `tray_uuid`
- Non-RFID: `lot_nr` must exactly match computed sig

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

1. Read `tray_uuid` from tray sensor attributes (`attrs.get("tray_uuid")`)
2. Find spools where `spool.lot_nr` exactly equals `tray_uuid`
3. Sticky: if `tray_uuid` matches stored `tray_signature` → re-bind immediately

Rules:
- No canonicalization needed — `lot_nr` is a plain string
- Spool with empty `lot_nr` is never an RFID candidate via primary path
- Migration fallback only: if `lot_nr` empty, check `extra.rfid_tag_uid` via canonicalizer (read-only)
- On successful match via fallback: write `tray_uuid` to `lot_nr`

---

## Match Key — Non-RFID Spools

Applied in order. Stop at first step that produces exactly one candidate.

**Step 1 — Sticky mapping fast path:**
`_build_tray_signature` output matches stored `tray_signature` helper → re-bind immediately.

**Step 2 — lot_nr exact match:**
Compute sig = `type|filament_id|color_hex`. Find spools whose `lot_nr` matches exactly.

**Step 3 — Migration fallback (comment HA_SIG):**
If no `lot_nr` match, check spool `comment` for legacy HA_SIG match. If found: write sig to `lot_nr`, bind.

**Step 4 — filament_id exact match:**
If `filament_id` is non-generic AND Spoolman `filament.external_id` is populated:
- Match tray `filament_id` against `filament.external_id` exactly
- Eligible locations: Shelf only

**Step 5 — Vendor + material match:**
Match `filament.vendor.name` + `filament.material`. Eligible locations: Shelf only.

Bambu vendor spools **are eligible** when `filament_id` is non-generic (e.g. `GFA00`). Bambu spools are excluded only when `filament_id` is a generic sentinel — but those are already blocked by pre-flight.

**Step 6 — Color as fuzzy tiebreaker only:**
Applied only when multiple candidates remain after Steps 4–5. Tolerance-based comparison. Exact equality is not required and must not be enforced.

**Exclusions from non-RFID pool:**
- Spools with non-empty `lot_nr` that does not match computed sig — these are RFID or different non-RFID spools

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
3. Write `tray_signature` helper using `_build_tray_signature`
4. Write `lot_nr` to Spoolman if not already set (enrollment)
5. Move Spoolman `location` to canonical slot location
6. Clear previous occupant (with guard)

**Do NOT write to `comment`.** Comment is now free for human use.

### tray_signature format (HA helper — internal only)

`_build_tray_signature` format for all 6 slots (internal HA helper, not stored in Spoolman):
```
name|type|filament_id|color_hex|tag_uid
```
All values: lowercased, stripped, max 255 chars. Color normalized per §Color Normalization.

This format is used **only** for the internal `tray_signature` HA helper for sticky mapping. It is **not** written to Spoolman `lot_nr`. The `lot_nr` sig format is `type|filament_id|color_hex` only.

**Legacy format retired:** Any `tray_signature` beginning with `NONRFID|` must be cleared on deploy. Log `LEGACY_SIGNATURE_CLEARED slot={slot}`.

### lot_nr enrollment sig format (Spoolman — persisted)

```
type|filament_id|color_hex
```
Lowercase. Color 6-char normalized, no `#`. Example:
```
pla|gfl05|898989
```

Returns no write when `filament_id` is generic sentinel or any required field is missing.

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

`lot_nr` mismatch or non-RFID material mismatch:
- Status: `CONFLICT: MISMATCH`, clear helpers, notify, require user intervention. No auto-correction.

---

## UID Integrity (v4)

### Write paths

`lot_nr` written only via bind path. Automatic reconcile writes `lot_nr` on first enrollment only. Overwriting existing `lot_nr` with a different value is refused unless explicitly triggered by manual enrollment.

### Overwrite protection

Refuse to overwrite existing different `lot_nr`. Log:
```
LOT_NR_CONFLICT spool_id={id} existing={existing} tray_uuid={tray} reason=refuse_overwrite
```

### Encoding

None required. `lot_nr` is a plain string. No JSON encoding. No canonicalizer.

---

## Spoolman Data Requirements

### lot_nr population

All active spools should have `lot_nr` populated. The reconciler populates it organically on first load/bind. For the immediate known case:

**Spool 41 (Bambu Green) — patch immediately:**
```
lot_nr = 38D1181E8F024FDA9D040D3BE3A20312
```

### filament.external_id population

Non-RFID filament_id exact match (Step 4) requires `filament.external_id` populated with Bambu catalog IDs.

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
- Overwrite existing `lot_nr` binding with different value
- Silently succeed when truth guard fires
- Use raw `json.dumps()` for any Spoolman field
- Treat `unavailable` or `unknown` tray state as `empty`
- Treat `remain == -1` as zero or empty
- Write to Spoolman when tray identity signals are inconsistent
- Write to `comment` field (reserved for human use)
- Write to `extra.rfid_tag_uid` or `extra.ha_spool_uuid` (retired fields)
- PATCH `extra` for any identity purpose
- Move spool whose location does not match expected source location
- Run fuzzy matching against `New` location pool
- Branch on slot number in reconciliation logic
- Use `_compute_ht_fingerprint` or write `NONRFID|` format signatures
- Use color as a hard match gate
- Include `name` or `tag_uid` in the `lot_nr` sig

---

## Implementation Priorities

| Priority | Item | Status | Rationale |
|---|---|---|---|
| **P1** | Canonicalizer | ✅ COMPLETE | Root cause fix. Now migration-only in v4. |
| **P2** | Safety poll status_only + remove auto-create | ✅ COMPLETE | Aligns with event-driven principle. |
| **P3** | Startup readiness waiter | ✅ COMPLETE | Fixes DomainException startup race. |
| **P4** | Unified code path — retire HT-specific code | ✅ COMPLETE | One path for all 6 slots. |
| **P5** | Non-RFID matching improvements | ✅ COMPLETE | filament_id primary signal. Color tiebreaker. Sentinel short-circuit. |
| **P6** | Three-tier waterfall | ✅ COMPLETE | Tier 2 AMS slot empty-confirmed. |
| **P7** | Previous occupant clearing guard | ✅ COMPLETE | Prevents irreversible moves of in-use spools. |
| **P8** | lot_nr identity migration (Spec v4) | ✅ COMPLETE | All identity stored in lot_nr. extra fields retired. canonicalizer migration-only. comment freed. RFID and non-RFID auto-enroll. |
| **P9** | Legacy field cleanup | **NEXT** | PATCH extra fields to null. Delete extra field definitions in Spoolman UI. Retire canonicalizer entirely. Update test suite. |
