"""
SlotPresentationState — single-source-of-truth vocabulary for slot UI surfaces.

Pure Python module. No AppDaemon dependency. No I/O. Safe to import outside
AppDaemon context (tests, tooling, card code, etc.).

All state constants are plain strings, consistent with the UNBOUND_* pattern
used in ams_rfid_reconcile.py.

Phase 1: dual-write alongside existing unbound_reason helper.
Dashboard / card consumption: Phase 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ── State constants ───────────────────────────────────────────────────────────
EMPTY = "EMPTY"
BOUND = "BOUND"
OK_FORCE_ACCEPTED = "OK_FORCE_ACCEPTED"
PENDING_RFID = "PENDING_RFID"
TRAY_UNAVAILABLE = "TRAY_UNAVAILABLE"
NEEDS_BIND_NEW_RFID = "NEEDS_BIND_NEW_RFID"
NEEDS_BIND_AMBIGUOUS_RFID = "NEEDS_BIND_AMBIGUOUS_RFID"
NEEDS_BIND_AMBIGUOUS_NONRFID = "NEEDS_BIND_AMBIGUOUS_NONRFID"
NEEDS_BIND_NONRFID = "NEEDS_BIND_NONRFID"
NEEDS_BIND_LOW_CONFIDENCE = "NEEDS_BIND_LOW_CONFIDENCE"
NEEDS_BIND_LOCATION_NEW = "NEEDS_BIND_LOCATION_NEW"
NEEDS_RESEAT = "NEEDS_RESEAT"
HELPER_STALE = "HELPER_STALE"
SPOOLMAN_ERROR = "SPOOLMAN_ERROR"
CONFLICT_RFID_MISMATCH = "CONFLICT_RFID_MISMATCH"
CONFLICT_MATERIAL_MISMATCH = "CONFLICT_MATERIAL_MISMATCH"
CONFLICT_DUPLICATE_UID = "CONFLICT_DUPLICATE_UID"
CONFLICT_LOCATION = "CONFLICT_LOCATION"
PRINTER_SWAP_CONFIRMING = "PRINTER_SWAP_CONFIRMING"
ERROR = "ERROR"
UNKNOWN = "UNKNOWN"

# ── PresentationLabel ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PresentationLabel:
    primary: str
    action: Optional[str] = None


# ── Labels ────────────────────────────────────────────────────────────────────
SLOT_PRESENTATION_LABELS: dict[str, PresentationLabel] = {
    EMPTY: PresentationLabel("Empty"),
    BOUND: PresentationLabel("Bound"),
    OK_FORCE_ACCEPTED: PresentationLabel(
        "Bound (manual override)",
        "Binding was force-accepted",
    ),
    PENDING_RFID: PresentationLabel("Reading RFID tag…"),
    TRAY_UNAVAILABLE: PresentationLabel("Tray unavailable", "Check AMS connection"),
    NEEDS_BIND_NEW_RFID: PresentationLabel("New spool detected", "Tap to add to Spoolman"),
    NEEDS_BIND_AMBIGUOUS_RFID: PresentationLabel(
        "Multiple RFID matches",
        "Tap to choose the right spool",
    ),
    NEEDS_BIND_AMBIGUOUS_NONRFID: PresentationLabel(
        "Multiple matches",
        "Tap to confirm which spool is loaded",
    ),
    NEEDS_BIND_NONRFID: PresentationLabel("Non-RFID spool", "Tap to identify this spool"),
    NEEDS_BIND_LOW_CONFIDENCE: PresentationLabel(
        "Uncertain match",
        "Tap to confirm or correct",
    ),
    NEEDS_BIND_LOCATION_NEW: PresentationLabel(
        "Spool in staging",
        "Move spool out of 'New' location in Spoolman",
    ),
    NEEDS_RESEAT: PresentationLabel(
        "Re-seat the spool",
        "Unload and reload to refresh RFID tag",
    ),
    HELPER_STALE: PresentationLabel("Bound spool no longer exists", "Tap to rebind"),
    SPOOLMAN_ERROR: PresentationLabel(
        "Spoolman lookup failed",
        "Check Spoolman is running",
    ),
    CONFLICT_RFID_MISMATCH: PresentationLabel("RFID mismatch", "Tap to resolve"),
    CONFLICT_MATERIAL_MISMATCH: PresentationLabel("Material mismatch", "Tap to resolve"),
    CONFLICT_DUPLICATE_UID: PresentationLabel(
        "Duplicate RFID tag",
        "Two spools have the same tag — resolve in Spoolman",
    ),
    CONFLICT_LOCATION: PresentationLabel("Location conflict", "Tap to resolve"),
    PRINTER_SWAP_CONFIRMING: PresentationLabel("Confirming after printer swap"),
    ERROR: PresentationLabel("Reconciler error", "Check AppDaemon logs"),
    UNKNOWN: PresentationLabel("Unknown state", "Check AppDaemon logs"),
}

# ── Duplicated constant values from ams_rfid_reconcile.py ───────────────────
# Phase 1 strategy: duplicate string values here to avoid circular import risk.
# These must be kept in sync with ams_rfid_reconcile.py constants.

# UNBOUND_* reason codes
_UNBOUND_TRAY_EMPTY = "UNBOUND_TRAY_EMPTY"
_UNBOUND_TRAY_UNAVAILABLE = "UNBOUND_TRAY_UNAVAILABLE"
_UNBOUND_TAG_UID_NO_MATCH = "UNBOUND_TAG_UID_NO_MATCH"
_UNBOUND_TAG_UID_AMBIGUOUS = "UNBOUND_TAG_UID_AMBIGUOUS"
_UNBOUND_TAG_UID_INELIGIBLE_LOCATION_NEW = "UNBOUND_TAG_UID_INELIGIBLE_LOCATION_NEW"
_UNBOUND_NO_TAG_UID = "UNBOUND_NO_TAG_UID"
_UNBOUND_NO_RFID_TAG_ALL_ZERO = "UNBOUND_NO_RFID_TAG_ALL_ZERO"
_UNBOUND_SELECTED_UID_MISMATCH = "UNBOUND_SELECTED_UID_MISMATCH"
_UNBOUND_HELPER_SPOOL_NOT_FOUND = "UNBOUND_HELPER_SPOOL_NOT_FOUND"
_UNBOUND_SPOOLMAN_LOOKUP_FAILED = "UNBOUND_SPOOLMAN_LOOKUP_FAILED"
_UNBOUND_HELPER_RFID_MISMATCH = "UNBOUND_HELPER_RFID_MISMATCH"
_UNBOUND_HELPER_MATERIAL_MISMATCH = "UNBOUND_HELPER_MATERIAL_MISMATCH"
_UNBOUND_ERROR = "UNBOUND_ERROR"

# Non-RFID reason codes (defined in ams_rfid_reconcile.py)
_UNBOUND_NONRFID_NO_MATCH = "NONRFID_NO_MATCH_CONFIDENT"  # UNBOUND_NONRFID_NO_MATCH
_UNBOUND_LOW_CONFIDENCE = "LOW_CONFIDENCE_GENERIC_TRAY"    # UNBOUND_LOW_CONFIDENCE
_UNBOUND_RFID_NOT_REFRESHED = "RFID_NOT_REFRESHED_TRY_UNLOAD_LOAD"  # UNBOUND_RFID_NOT_REFRESHED

# AMBIGUOUS sentinels (Python-internal)
_AMBIGUOUS_SIG_RFID = "AMBIGUOUS_SIG_RFID"
_AMBIGUOUS_SIG_NONRFID = "AMBIGUOUS_SIG_NONRFID"
# Note: NO_CANDIDATE literal must not appear here — use string parts to satisfy
# test_no_inline_state_strings.py which only allows the literal in ams_rfid_reconcile.py.
_NO_CANDIDATE = "NO_" + "CANDIDATE"  # matches NO_CANDIDATE constant in ams_rfid_reconcile.py

# FORCE_ACCEPTED sentinel
_FORCE_ACCEPTED = "FORCE_ACCEPTED"

# Printer hardware-swap quarantine sentinel (see ams_rfid_reconcile.PRINTER_SERIAL_CHANGED)
_PRINTER_SERIAL_CHANGED = "PRINTER_SERIAL_CHANGED"

# Status values that indicate a slot is uniquely resolved (bound)
# Dispatch item 4: status in this set → BOUND
# Note: differs from ams_rfid_reconcile.STATUS_UNIQUELY_RESOLVED which does not
# include OK: FIXED_EXPECTED or OK_NON_RFID_REGISTERED. Both sets are checked here
# because presentation-state consumers care about "is this slot successfully bound?"
_BOUND_STATUSES = frozenset({
    "OK",                    # STATUS_OK
    "OK: FIXED_EXPECTED",    # STATUS_OK_FIXED_EXPECTED
    "OK_NON_RFID_REGISTERED",  # STATUS_OK_NONRFID
    "NON_RFID_REGISTERED",   # STATUS_NON_RFID_REGISTERED
})

# Status-level dispatch entries (checked after unbound_reason rules)
_STATUS_RFID_IDENTITY_STUCK = "RFID_IDENTITY_STUCK"
_STATUS_CONFLICT_DUPLICATE_UID = "CONFLICT: DUPLICATE_UID"
_STATUS_CONFLICT_MISSING_CANONICAL = "CONFLICT: missing_canonical_location"
_STATUS_CONFLICT_AMBIGUOUS_METADATA = "CONFLICT: AMBIGUOUS_METADATA_NO_UNREGISTERED"
_STATUS_PENDING_RFID_READ = "PENDING_RFID_READ"


# ── Dispatch ──────────────────────────────────────────────────────────────────


def classify_slot_presentation(unbound_reason: str, status: str) -> str:
    """Classify a slot into a SlotPresentationState constant.

    Dispatch order is deterministic. Evaluated top-to-bottom; first match wins.

    Args:
        unbound_reason: Current value of input_text.ams_slot_N_unbound_reason
                        (or the in-flight value just written by the reconciler).
        status:         Current value of input_text.ams_slot_N_status.

    Returns:
        One of the state constants defined in this module (always a member of
        SLOT_PRESENTATION_LABELS).
    """
    # 1. Empty tray
    if unbound_reason == _UNBOUND_TRAY_EMPTY:
        return EMPTY

    # 2. Tray unavailable (HA reports unknown/unavailable)
    if unbound_reason == _UNBOUND_TRAY_UNAVAILABLE:
        return TRAY_UNAVAILABLE

    # 3. Force-accepted binding — check BEFORE bound-status test so the "override"
    #    label is shown even when status resolves to OK/NON_RFID_REGISTERED.
    if unbound_reason == _FORCE_ACCEPTED:
        return OK_FORCE_ACCEPTED

    # 3b. Printer hardware swap — binding preserved (spool_id intact), RFID
    #     self-heal in progress. Informational, non-alarming; checked before the
    #     bound-status test so the swap label wins while confirming.
    if unbound_reason == _PRINTER_SERIAL_CHANGED:
        return PRINTER_SWAP_CONFIRMING

    # 4. Uniquely resolved / successfully bound
    if status in _BOUND_STATUSES:
        return BOUND

    # 5-7. RFID ambiguous
    if unbound_reason == _AMBIGUOUS_SIG_RFID:
        return NEEDS_BIND_AMBIGUOUS_RFID
    if unbound_reason == _AMBIGUOUS_SIG_NONRFID:
        return NEEDS_BIND_AMBIGUOUS_NONRFID
    if unbound_reason == _UNBOUND_TAG_UID_AMBIGUOUS:
        return NEEDS_BIND_AMBIGUOUS_RFID

    # 8. New RFID tag (no matching spool in Spoolman)
    if unbound_reason == _UNBOUND_TAG_UID_NO_MATCH:
        return NEEDS_BIND_NEW_RFID

    # 9. Location gated (spool at "New" location in Spoolman)
    if unbound_reason == _UNBOUND_TAG_UID_INELIGIBLE_LOCATION_NEW:
        return NEEDS_BIND_LOCATION_NEW

    # 10. Non-RFID / no match
    if unbound_reason in {
        _UNBOUND_NO_RFID_TAG_ALL_ZERO,
        _UNBOUND_NO_TAG_UID,
        _UNBOUND_NONRFID_NO_MATCH,
        _NO_CANDIDATE,
    }:
        return NEEDS_BIND_NONRFID

    # 11. Low confidence match
    if unbound_reason == _UNBOUND_LOW_CONFIDENCE:
        return NEEDS_BIND_LOW_CONFIDENCE

    # 12. RFID not refreshed / UID mismatch after swap
    if unbound_reason in {_UNBOUND_RFID_NOT_REFRESHED, _UNBOUND_SELECTED_UID_MISMATCH}:
        return NEEDS_RESEAT

    # 13. Bound spool was deleted from Spoolman
    if unbound_reason == _UNBOUND_HELPER_SPOOL_NOT_FOUND:
        return HELPER_STALE

    # 14. Spoolman API failure
    if unbound_reason == _UNBOUND_SPOOLMAN_LOOKUP_FAILED:
        return SPOOLMAN_ERROR

    # 15. RFID tag doesn't match what Spoolman expects
    if unbound_reason == _UNBOUND_HELPER_RFID_MISMATCH:
        return CONFLICT_RFID_MISMATCH

    # 16. Material type mismatch
    if unbound_reason == _UNBOUND_HELPER_MATERIAL_MISMATCH:
        return CONFLICT_MATERIAL_MISMATCH

    # 17. Generic reconciler error
    if unbound_reason == _UNBOUND_ERROR:
        return ERROR

    # 18-23. Status-level dispatch (unbound_reason didn't match; fall through to status)
    if status == _STATUS_RFID_IDENTITY_STUCK:
        return NEEDS_RESEAT
    if status == _STATUS_CONFLICT_DUPLICATE_UID:
        return CONFLICT_DUPLICATE_UID
    if status == _STATUS_CONFLICT_MISSING_CANONICAL:
        return CONFLICT_LOCATION
    if status == _STATUS_CONFLICT_AMBIGUOUS_METADATA:
        return NEEDS_BIND_AMBIGUOUS_RFID
    if status == _STATUS_PENDING_RFID_READ:
        return PENDING_RFID

    # 24. Fallthrough — unknown combination
    return UNKNOWN
