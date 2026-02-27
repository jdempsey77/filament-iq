# Spool Matching Specification v2
## Canonical Reference — Reconciler Behavior

This document is the authoritative specification for how the AMS reconciler identifies and binds spools to slots. All reconciler code must implement this spec exactly. When code and spec conflict, the spec wins.

**Revision history:**
- v1: Initial spec derived from design sessions
- v2: Updated based on deep code analysis (ANALYZE report, 2026-02-27). Key changes: canonicalizer gap identified and made P0 fix, HT path clarified as shared capability not slot-specific, safety poll made status-only, tie-break and EOL edge cases tightened, previous-occupant guard added.

---

## P0 Fix — Canonicalizer Module Missing (Fix Before Everything Else)

**The `spoolman_extra_canonicalizer` module does not exist in the codebase.** The `ImportError` fallback in `ams_rfid_reconcile.py` lines 44–53 has always been active. All RFID UID and UUID encoding/decoding runs through the manual fallback path with no double-encoding protection.

This is the most likely root cause of intermittent recognition failures. A UID written with slightly different encoding than it was read will never match.

**Required action (must be completed before any other reconciler changes):**

1. Create `scripts/spoolman_extra_canonicalizer.py` implementing:
   - `canonicalize_rfid_tag_uid(raw)` — decode, strip quotes/whitespace, uppercase, validate hex pattern, return empty string for sentinels (all-zero, empty, `""` literal)
   - `canonicalize_ha_spool_uuid(raw)` — decode, strip, validate UUID format
   - `canonicalize_extra_scalar(raw)` — generic decode for other extra string fields
   - `encode_extra_json_string(value)` — single JSON encode (e.g. `"ABC"` → `'"ABC"'`); never double-encode
   - `is_double_encoded(raw)` — detect `'"\\"ABC\\""'` pattern
   - `validate_extra_value_no_quotes(raw)` — assert no raw quote characters remain after decode

2. Add a startup log line confirming the canonicalizer loaded successfully. If it fails to import, log at ERROR level and halt initialization — do not silently fall back.

3. Add a gate to `skill_test.sh` that confirms the module exists and imports cleanly.

---

## Design Principles

- The reconciler is **event-driven**, not polling-driven. It fires on tray state change events and manual triggers only.
- Spools are **never auto-created** by the reconciler. A spool that does not exist in Spoolman is a data gap, not a creation opportunity. The correct response is always NEEDS_ACTION.
- Identity is **explicit and sticky**. Once a spool is bound to a slot, the binding persists until a real physical change is detected.
- The candidate pool is **ordered and exhausted top-down**. Lower tiers are only searched when higher tiers yield no match.
- All encoding and decoding of Spoolman extra fields **must go through the canonicalizer**. Never use raw `json.dumps()` or manual string manipulation for extra field values.

---

## Spool Lifecycle

```
Order → Receive → Create in HA interface → Spoolman record created (location: New)
      → User opens spool, moves to Shelf in Spoolman
      → User inserts into AMS slot
      → Reconciler fires, matches and binds
      → In use (location: AMS1_SlotX etc.)
      → Removed from AMS → no location change (unless EOL, see below)
      → Returned to Shelf by user if desired
      → ... repeat insertions ...
      → Spool hits 0g and is removed → location: Empty (excluded forever)
```

**Location meanings:**

| Location | Meaning | Eligible for matching? |
|---|---|---|
| `New` | Received but not yet opened/staged | Tier 3 only (exact match only — see below) |
| `Shelf` | Opened, staged, ready to use | Tier 1 (primary) |
| `AMS1_Slot1..4` | In AMS unit 1, slots 1–4 | Tier 2 only if that slot's tray state is explicitly `empty` |
| `AMS128_Slot1` | In AMS HT unit (slot 5) | Tier 2 only if that slot's tray state is explicitly `empty` |
| `AMS129_Slot1` | In AMS HT unit (slot 6) | Tier 2 only if that slot's tray state is explicitly `empty` |
| `Empty` | EOL — used up and removed | Never eligible at any tier |

---

## Reconciler Triggers

The reconciler fires on:
- Tray entity state change (insertion, removal, swap detected by Bambu integration)
- Manual reconcile button press (`input_button.p1s_rfid_reconcile_now`)
- Event `AMS_RECONCILE_ALL` (status-only run, no writes)

**Safety poll:** Retained but changed to `status_only=True`. Fires every 600 seconds for diagnostic purposes only — detects drift and logs warnings but performs no Spoolman PATCHes and no helper writes. Never auto-corrects. Operator triggers a manual reconcile if drift is detected.

Debounce: 5–10 seconds after the triggering event before executing, to allow sensor state to stabilize.

---

## Slot Handling — All Slots Use the Same Contract

All six slots (1–4 on AMS1, 5–6 on HT units) use the **same three-tier waterfall, same sticky mapping, same tie-break, and same NEEDS_ACTION behavior**.

Slots 5 and 6 support RFID spools. If an RFID spool is inserted into an HT slot and the Bambu integration reports a non-zero `tag_uid`, the reconciler uses the RFID match path normally. The HT guard only fires when both `tag_uid` and `tray_uuid` are all-zero (the signal for a non-RFID spool in those slots).

**Shared capabilities (apply to all slots, not HT-only):**

These behaviors were previously implemented only in the HT-specific code path. They must be extracted into shared methods and applied uniformly across all six slots:

1. **Pending confirmation** — require 2 consecutive observations or 10 seconds of stability before treating a tray identity change as real. Prevents thrash from transient sensor glitches on any slot.

2. **Confidence gating** — before running the non-RFID waterfall, confirm tray attributes are specific enough to auto-match. Reject if `type` or `color` is missing, or if tray state starts with `GENERIC`. Set status to `LOW_CONFIDENCE_NO_AUTO_MATCH` and notify rather than attempting a low-quality match.

3. **State-aware fingerprinting** — the tray signature used for sticky mapping must include the tray state string, not just name/type/filament_id/color_hex. This prevents a slot transitioning through intermediate states from being treated as the same identity.

---

## Candidate Pool — Three-Tier Waterfall

The same three-tier waterfall applies to **both RFID and non-RFID** spools. The match key differs (see below) but the pool search order is identical.

### Tier 1 — Shelf

Spools with `location == "Shelf"`. Primary and expected pool.

### Tier 2 — Current AMS Slots (Empty Confirmed)

Spools whose `location` is any canonical AMS slot location where the tray entity for that slot currently has state explicitly `empty`.

Handles the real-world case where a spool is moved directly from one slot to another without returning to Shelf. The source slot being empty confirms the physical move.

**Guard:** Tray state must be explicitly `empty`. Any other state (including `unavailable`, `unknown`) → treat as occupied → exclude from candidates.

**Additional guard:** Only consider a spool from another slot as a Tier 2 candidate if that spool is not currently the active `spool_id` helper of any other slot whose tray is not empty. Prevents stealing a spool that is legitimately in use due to stale Spoolman location data.

### Tier 3 — New (Last Resort, Exact Match Only)

Spools with `location == "New"`. Handles spools that exist in Spoolman but haven't been moved to Shelf before insertion.

**Tier 3 is restricted to exact matches only:**
- RFID: spool's `extra.rfid_tag_uid` (decoded via canonicalizer) must exactly match the tray's `tag_uid`
- Non-RFID: spool's Spoolman comment must exactly match the computed HA_SIG for this tray

Fuzzy metadata matching is never used against the New pool. No exact match at Tier 3 → NEEDS_ACTION.

### Excluded Always

- `location == "Empty"` — never candidates at any tier
- `archived == true` — never candidates at any tier

---

## Waterfall Execution

At each tier, apply the match key (RFID or non-RFID). Then:

| Candidates found | Action |
|---|---|
| Exactly 1 | Bind |
| More than 1 | Apply tie-break. Resolved → bind. Unresolvable → fall to next tier |
| 0 | Fall to next tier |

All three tiers exhausted with no binding → NEEDS_ACTION.

---

## Match Key — RFID Spools

**Matching rule:** Normalize the tray `tag_uid` via canonicalizer. Find spools where `extra.rfid_tag_uid` (decoded and normalized via canonicalizer) equals the tray UID.

- Canonicalizer is mandatory before any comparison. Never compare raw values.
- A spool with empty/sentinel `rfid_tag_uid` after decode is never an RFID candidate.
- On first insertion, the UID is written to `extra.rfid_tag_uid` at bind time via the canonicalizer encode path.

---

## Match Key — Non-RFID Spools

**Step 1 — HA_SIG fast path (re-insertion):**
Compute HA_SIG from current tray metadata. Find spools whose Spoolman comment matches exactly. Fast and deterministic for known spools.

**Step 2 — Fuzzy metadata match (first insertion):**
Match on vendor + material + color_hex (normalized, with tolerance) + filament name/ID token overlap. Deterministic and bounded — when ambiguous, apply tie-break or fall to next tier. Never guess.

**Exclusions from non-RFID pool:**
- Spools with a non-empty `rfid_tag_uid` after decode — RFID spools are matched by UID only
- Bambu Lab branded spools without an RFID UID — Bambu spools are expected to be RFID-equipped; no UID means a data gap, not a fuzzy match opportunity

---

## Tie-Break — Least Remaining Grams

1. Choose the candidate with the least `remaining_weight`
2. Equal remaining weight → choose lowest Spoolman spool ID
3. Some candidates have null/missing `remaining_weight` → treat missing as worse than any known value; prefer candidates with known weight
4. All candidates have null/missing `remaining_weight` → cannot resolve → fall to next tier

---

## On Successful Bind

1. Write `spool_id` helper for the slot
2. Write `expected_spool_id` helper for the slot
3. Write `tray_signature` helper (tray identity = non-zero `tray_uuid` → normalized `tag_uid` → metadata fingerprint including tray state)
4. Write HA_SIG to Spoolman spool comment (idempotent — PATCH only if comment differs)
5. Move Spoolman spool `location` to canonical slot location
6. Clear previous occupant (with guard — see below)

**HA_SIG format:**
```
HA_SIG=bambu|filament_id=<id>|type=<type>|color_hex=<hex>
```
All values lowercase. Color hex 6-char normalized, no `#`. Example:
```
HA_SIG=bambu|filament_id=gfa00|type=pla|color_hex=00ae42
```

HA_SIG is written once and lives with the spool for its lifetime. Never cleared except at EOL.

**Previous occupant clearing guard:**
Only move the previous occupant spool if:
- Its Spoolman location actually matches this slot's canonical location (never move spools already elsewhere), AND
- It is not currently the active `spool_id` helper of any other slot whose tray is not empty

If conditions are met: move to `Shelf` if `remaining_weight > 0`, move to `Empty` if `remaining_weight <= 0`.

---

## On Empty Tray (Removal)

1. Check `remaining_weight` of the currently bound spool in Spoolman
2. If `remaining_weight <= 0` → EOL path: move Spoolman location to `Empty`
3. If `remaining_weight > 0` → no Spoolman location change
4. In both cases: clear `spool_id` to `0`, clear `expected_spool_id` to `0`, clear `tray_signature` to `""`

---

## End of Life (EOL)

When `remaining_weight <= 0` and tray transitions to `empty`:
- Move Spoolman `location` to `Empty`
- `Empty` spools are excluded from all tiers permanently
- HA_SIG preserved on the record for historical reference

---

## No Match — NEEDS_ACTION

1. Set slot status to `UNBOUND: ACTION_REQUIRED`
2. Send persistent HA notification with:
   - Slot number
   - Tray signals observed (type, color_hex, name, filament_id, tag_uid if RFID)
   - Result at each tier searched (0 candidates / ambiguous / no exact match)
   - Instruction: move the correct spool to Shelf in Spoolman, then press reconcile

The reconciler does not auto-retry. User resolves via Spoolman then manual reconcile.

---

## Sticky Mapping — No Churn

If `tray_signature` is unchanged since last reconcile AND `spool_id` helper points to a valid Spoolman spool:
- Skip the waterfall entirely
- No Spoolman PATCHes, no helper writes

Only re-run the waterfall when tray identity actually changes (confirmed after pending confirmation window).

---

## Mismatch Detection

RFID UID mismatch (bound spool UID ≠ tray `tag_uid` after canonicalization):
- Status: `CONFLICT: MISMATCH`, clear helpers, notify, require user intervention

Non-RFID material mismatch (bound spool material ≠ tray type):
- Same: CONFLICT, clear, notify, require intervention

No auto-correction in either case.

---

## What the Reconciler Must Never Do

- Auto-create Spoolman spool records
- Auto-select a spool from `location == "Empty"`
- Overwrite an existing RFID UID binding with a different UID
- Silently succeed when a truth guard fires
- Use raw `json.dumps()` or manual string manipulation for extra field encoding — always use canonicalizer
- Treat `unavailable` or `unknown` tray state as `empty`
- Write to Spoolman when tray identity signals are inconsistent
- PATCH `extra` without reading current extra first (PATCH replaces the entire extra block)
- Move a spool in Spoolman whose location does not match the expected source location
- Run fuzzy matching against the `New` location pool

---

## Implementation Priorities (Execute in Order)

Do not start a later priority until the earlier one is complete and TEST passes.

| Priority | Item | Rationale |
|---|---|---|
| **P1** | Create `spoolman_extra_canonicalizer.py`, make import failure a hard error, add TEST gate | Root cause of intermittent recognition failures. Everything else depends on correct encoding. |
| **P2** | Change safety poll to `status_only=True` | Eliminates unnecessary Spoolman writes, aligns with event-driven principle. Low risk, quick win. |
| **P3** | Remove `_create_spool_from_tray()` | Directly violates no-auto-creation rule. Must be gone before waterfall rewrite. |
| **P4** | Extract pending confirmation, confidence gating, state-aware fingerprinting from HT path into shared methods; apply to all 6 slots | Unifies slot contract, improves stability on all slots. |
| **P5** | Implement three-tier waterfall for both RFID and non-RFID paths | Core matching logic rewrite. Depends on P1 (canonicalizer) being solid first. |
| **P6** | Add previous occupant clearing guard | Prevents irreversible moves of legitimately-in-use spools. |
