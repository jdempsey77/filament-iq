# RFID System Architecture — End-to-End Audit

This document describes the AMS ↔ Home Assistant ↔ Spoolman RFID flow, identity model, location lifecycle, failure modes, and hardening recommendations. It is the single reference for deterministic enrollment and selection behavior.

---

## 1. Current Architecture

### 1.1 Components

| Component | Role |
|-----------|------|
| **Printer (Bambu P1S)** | Reads RFID from tray; reports tray state, tag_uid, type, color, etc. via MQTT/integration. |
| **HA entities** | Tray sensors (e.g. `sensor.p1s_01p00c5a3101668_ams_1_tray_1`) hold state + attributes: tag_uid, type, color, filament_id, tray_weight, remain. |
| **AppDaemon: ams_rfid_reconcile** | Listens to tray state changes and events; runs deterministic candidate discovery, tie-break, binding; writes Spoolman (extra.rfid_tag_uid, location, comment/HA_SIG). |
| **AppDaemon: ams_rfid_guard** | Validates Spoolman spools (rfid_tag_uid ⇒ ha_spool_uuid; RFID-managed filament ⇒ ha_spool_uuid); can set location=QUARANTINE. |
| **AppDaemon: ams_rfid_usage_sync** | Syncs tray usage (remain) to Spoolman remaining_weight when printing. |
| **Spoolman** | Source of truth for spools: id, filament_id, location, remaining_weight, comment, extra (rfid_tag_uid, ha_spool_uuid as JSON-encoded strings). |

### 1.2 Insertion Flow (End-to-End)

1. **Printer** — User inserts tray; printer reads RFID and updates internal tray state.
2. **HA entity update** — Integration pushes tray state to HA; sensor entity gets `state` (e.g. valid/empty) and `attributes` (tag_uid, type, color, name, filament_id, tray_weight, remain).
3. **tag_uid read** — Reconcile reads `attrs.tag_uid`, canonicalizes via `_canonicalize_tag_uid(raw)` (shared canonicalizer when available).
4. **Reconciliation trigger** — Reconcile is triggered by:
   - `listen_state` on each tray entity (attribute="all") → debounced run
   - Event `bambu_rfid_reconcile_now` (e.g. UI button)
   - Event `AMS_RECONCILE_ALL` (script.reconcile_all_ams_slots; status-only option)
   - Startup delay and safety poll
5. **Candidate discovery** — For slot with tag_uid:
   - UID lookup: spools with `_extract_spool_uid(spool) == tag_uid` (decode + canonicalize; never raw).
   - If exactly one → known binding path (location + helpers + optional HA_SIG stamp).
   - If zero UID matches → deterministic candidates: no rfid_tag_uid, location in ("", "shelf", "unknown") **and not "new"**, vendor Bambu Lab, material/color match; **location "New" is excluded before tie-break.**
   - If one deterministic candidate → bind (write extra.rfid_tag_uid, location, helpers).
   - If multiple → tie-break (prefer_used, next_man_up, full_pick) or strict_mode → REFUSE; on pick → bind.
   - Flow B: HA_SIG comment match when UID match count 0 and deterministic candidates 0; eligible only when _unjson(rfid_tag_uid)=="" (so encoded empty `""` is correctly treated as unregistered).
6. **Binding to spool** — `_bind_uid_to_spool` + `_force_location_and_helpers`: PATCH Spoolman extra.rfid_tag_uid (and ha_spool_uuid if missing), PATCH location to AMS1_SlotX; set HA helpers (ams_slot_N_spool_id, expected_spool_id, tray_signature, etc.).
7. **Writing rfid_tag_uid to Spoolman** — All writes go through `_patch_spool_extra_robust`: canonicalize then single JSON encode (e.g. `encode_extra_json_string`) for rfid_tag_uid/ha_spool_uuid; never double-encode.

---

## 2. Identity Model

- **extra.rfid_tag_uid** — Stored as JSON-string literal (e.g. `"\"C7D26F7B00000100\""`). Single source of truth for “which tag is bound to this spool.” All reads: decode (e.g. _unjson / canonicalizer) then compare; never use raw value for “is registered.”
- **extra.ha_spool_uuid** — UUID string, JSON-encoded same way. Used for HA-side correlation and Guard invariant (rfid_tag_uid present ⇒ ha_spool_uuid must be set).
- **Normalization** — Shared `spoolman_extra_canonicalizer`: decode → strip/quotes/sentinels → TAG_UID_RE / UUID_RE. Sentinels (e.g. "", "0000000000000000") canonicalize to empty.
- **ha_spool_uuid** — Set on first bind if missing; not changed on re-bind.
- **tray_uuid** — From tray attributes; used for non-RFID detection (e.g. 00000000… + empty=false), not for Spoolman identity.
- **comment / HA_SIG** — Stamped after bind: `HA_SIG=bambu|filament_id=…|type=…|color_hex=…`. Flow B eligibility: comment==HA_SIG and decoded rfid_tag_uid=="" and ha_spool_uuid set.
- **Signature stamping** — Idempotent; only PATCH when comment != HA_SIG.

---

## 3. Location Lifecycle Model

| Location | Meaning | Auto-selected? | Written by |
|----------|---------|----------------|------------|
| **Shelf** | In inventory, not in AMS | Yes (if metadata match) | Reconcile (when moving spool off slot) |
| **AMS1_Slot1..4, AMS128_Slot1, AMS129_Slot1** | In AMS slot | N/A (already bound) | Reconcile on bind / force_location |
| **New** | Never used; explicit enrollment only | **No** (excluded in _find_deterministic_candidates) | User/import; never auto-selected |
| **QUARANTINE** | Guard violation | No | Guard |
| **Archived** | archived=true in Spoolman | Not in candidate filter by location; archived=false required for Flow B eligibility in practice | External |

Reconcile never writes "New"; it only **excludes** spools with location "New" from deterministic candidate selection.

**One spool per location:** To avoid multiple spools showing the same AMS slot (ghosts), reconcile enforces one spool per slot location. When a slot’s binding changes or the slot is unbound, the **previous** spool’s Spoolman location is cleared first: if the previous helper spool is still at that slot’s canonical location, it is PATCHed to `Shelf` before writing the new binding or setting helpers to 0. Only spools actually at the slot’s location are moved; no destructive move of spools that are already elsewhere. Log line: `CLEAR_PREVIOUS_SLOT_OCCUPANT slot=N old=<id> new=<id> from=<slotloc> to=Shelf`.

---

## 4. Failure Modes

- **Multiple same color** — Tie-break (prefer_used, next_man_up, full_pick) or strict_mode REFUSE; no bind if ambiguous.
- **Duplicate spool same weight** — full_pick chooses smallest spool_id; otherwise CONFLICT.
- **Encoded empty tag `\"\"` vs ""** — All core code uses decode/canonicalize before “has tag” checks; **no logic in reconcile/guard/usage_sync incorrectly treats `\"\"` as registered.** Scripts that do `if extra.get("rfid_tag_uid"):` without decoding would be wrong; audit found no such pattern in AppDaemon apps.
- **strict_mode on/off** — When on, multiple metadata matches → REFUSE (no auto-pick). When off, tie-break applies.
- **Binding conflicts** — Sticky: if spool already has a different tag_uid, _bind_uid_to_spool raises; no overwrite.
- **RFID mismatch** — Expected vs resolved spool_id or tray vs spool color mismatch → CONFLICT: MISMATCH; optional autofix when only expected mismatch (trust RFID).
- **Stale cached values** — Helpers (expected_spool_id, etc.) updated on every successful bind and cleared when tray empty; evidence log and transcripts support debugging.

---

## 5. Diagram-like Flow

```
Printer RFID read
       ↓
HA tray sensor state/attributes (tag_uid, type, color, …)
       ↓
AppDaemon: listen_state(tray) / events → debounce → _run_reconcile
       ↓
Reconcile: GET /api/v1/spool?limit=1000
       ↓
Per slot: tag_uid → UID lookup (decode extra.rfid_tag_uid)
  → 1 match: known binding (location + helpers + HA_SIG if needed)
  → 0 match: _find_deterministic_candidates (exclude location "New"; Shelf/empty/unknown; Bambu; material/color)
    → 1 candidate: bind (PATCH extra + location; set helpers)
    → >1: tiebreak or strict REFUSE
  → Flow B if 0 candidates: HA_SIG match → bind
       ↓
Spoolman: PATCH /api/v1/spool/{id} (extra, location, comment)
       ↓
Guard: periodic check; rfid_tag_uid ⇒ ha_spool_uuid; RFID filament ⇒ ha_spool_uuid; else QUARANTINE
       ↓
Usage sync: when printing, tray remain → PATCH remaining_weight for bound spool
```

---

## 6. Single Source of Truth, Write Paths, Races, Idempotency

- **Single source of truth for binding** — Spoolman `extra.rfid_tag_uid` (decoded) is the authority for “this spool is bound to this tag.” HA helpers are derived/cache.
- **Write-path owners**  
  - **Reconcile:** extra.rfid_tag_uid, extra.ha_spool_uuid, location (AMS/Shelf), comment (HA_SIG).  
  - **Guard:** location=QUARANTINE only (no extra writes).  
  - **Usage sync:** remaining_weight only.  
  - **Create-from-tray / manual enroll:** extra + location + comment (reconcile code paths).
- **Race conditions** — Reconcile runs debounced and per run is sequential per slot; Spoolman PATCH is last-write-wins. Concurrent runs (e.g. two triggers) could in theory double-write; evidence log and no_write_paths help. No cross-process lock.
- **Idempotency** — Bind path: _bind_uid_to_spool skips if current_uid == tag_uid; _force_location_and_helpers only PATCHes if current_location != desired; HA_SIG stamp only if comment != ha_sig. So repeated triggers are safe.
- **Where duplicates could occur** — (1) Two spools with same tag_uid (reconcile detects duplicate_uids and sets CONFLICT: DUPLICATE_UID; no bind). (2) Same tag written to two spools by different flows (sticky binding prevents overwrite; first bind wins).

---

## 7. Risks

- **No distributed lock** — Two reconcile runs could both try to bind the same unbound spool; last PATCH wins. Mitigation: debounce and single-threaded run.
- **Guard runs independently** — Can set QUARANTINE after reconcile bound; next reconcile may see spool in QUARANTINE (reconcile does not clear QUARANTINE).
- **Spoolman API/network** — GET/PATCH failure aborts or partial run; evidence log and no_write_paths record what was skipped.
- **Encoded empty `\"\"`** — Core always decodes; any new script that checks raw `extra.get("rfid_tag_uid")` for truthiness would be wrong.

---

## 8. Recommended Hardening Steps

1. **Always decode before “has tag”** — Use _extract_spool_uid / _unjson / canonicalizer in all scripts that interpret extra.rfid_tag_uid.
2. **Location "New"** — Keep exclusion in _find_deterministic_candidates; do not auto-select "New" spools; document in Spoolman/UI that "New" means explicit enrollment only.
3. **Strict mode** — Use strict_mode_reregister when multiple same-color spools exist and you want no auto-pick until explicit enrollment.
4. **Evidence log** — Keep evidence_log_path writable; use for audits and replay.
5. **Guard** — Keep rfid_tag_uid ⇒ ha_spool_uuid and RFID-managed ⇒ ha_spool_uuid invariants; consider warn_only for transition.
6. **Single reconcile entrypoint** — Prefer script.reconcile_all_ams_slots / service call so all triggers use same path and debounce.

---

## 9. Deterministic Enrollment Model

- **Explicit enrollment** — Manual: event `bambu_rfid_manual_enroll_tag_to_spool` with slot + spool_id. Create-from-tray: event `bambu_rfid_create_spool_from_tray` with slot (creates spool with location set to slot, then reconcile runs).
- **Auto-enrollment** — Only when exactly one deterministic candidate (after excluding "New") or one Flow B HA_SIG match; or when tie-break picks one and strict_mode is off. No new spool creation except via create-from-tray.

---

## 10. Deterministic Selection Model

- **Eligible for auto-selection** — Spool has no rfid_tag_uid (decoded empty); location in ("", "shelf", "unknown"); **location != "new"**; vendor Bambu Lab; material and color match tray; optional name/filament_id narrowing.
- **Excluded** — Already has rfid_tag_uid; location "New"; location not Shelf/empty/unknown; vendor/material/color mismatch.
- **Tie-break order** — (1) strict_mode → REFUSE. (2) prefer_used (exactly one with remaining < initial). (3) next_man_up (margin ≥ 200g). (4) full_pick (both ≥ 950g, smallest id). (5) CONFLICT.

---

## 11. State Transition Diagram (Slot + Spool)

```
[Empty tray]
    → clear expected helpers; status UNBOUND/no_tag or NON_RFID_UNREGISTERED

[Tray with tag_uid]
    → UID match 1 → OK (known binding)
    → UID match 0, deterministic candidates 1 → OK (auto_register_metadata_match)
    → UID match 0, deterministic candidates >1 → tiebreak or REFUSE → OK or CONFLICT
    → UID match 0, deterministic candidates 0 → Flow B or UNBOUND
    → UID match >1 → CONFLICT: DUPLICATE_UID
    → UID match 1 but expected/color mismatch → CONFLICT: MISMATCH or OK (autofix)

Spool location:
    Shelf/empty/unknown (and not New) → eligible for auto-selection
    New → never auto-selected
    AMS1_SlotX / AMS128_Slot1 / AMS129_Slot1 → bound to slot
    QUARANTINE → set by Guard
```

---

## 12. Identification: `\"\"` Treated as Registered?

**Conclusion: No incorrect treatment in core AppDaemon apps.**

- **ams_rfid_reconcile** — Uses _extract_spool_uid (canonicalize) and _unjson for Flow B; decoded empty is never treated as “has tag.”
- **ams_rfid_guard** — _get_tag_uid uses _json_text_to_str; empty string returns "" and `if tag_uid` is false.
- **ams_rfid_usage_sync** — _normalize_spool_uid uses canonicalizer or strip/replace; `'""'` becomes "".

**Recommendation:** Any script that checks `if extra.get("rfid_tag_uid"):` without decoding would wrongly treat stored `'""'` as registered. Core code does not do this; keep using decode/canonicalize everywhere.
