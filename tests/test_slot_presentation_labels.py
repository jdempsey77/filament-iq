"""
Tests for slot_presentation.py — SlotPresentationState vocabulary.

Validates:
  1. Every state constant in slot_presentation has a SLOT_PRESENTATION_LABELS entry.
  2. No orphan labels without a matching state constant.
  3. classify_slot_presentation handles all known UNBOUND_* and STATUS_* values.
"""

import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Module-level import
# ---------------------------------------------------------------------------

from apps.filament_iq import slot_presentation as sp


# ---------------------------------------------------------------------------
# Helper: collect all public string constants from the module
# ---------------------------------------------------------------------------

def _all_state_constants():
    """Return the set of names that look like state constants (all-caps str attrs)."""
    result = {}
    for name, value in vars(sp).items():
        if (
            name.isupper()
            and isinstance(value, str)
            and not name.startswith("_")
            # Exclude module-private duplicated constants (lowercase-prefixed duplicates)
            and name
            not in {
                "SLOT_PRESENTATION_LABELS",
            }
        ):
            result[name] = value
    return result


# ---------------------------------------------------------------------------
# 1. Every state constant has a label
# ---------------------------------------------------------------------------


def test_every_state_has_a_label():
    """Every public ALLCAPS string constant in slot_presentation is in SLOT_PRESENTATION_LABELS."""
    constants = _all_state_constants()
    assert constants, "Expected state constants but found none"
    missing = {name: val for name, val in constants.items() if val not in sp.SLOT_PRESENTATION_LABELS}
    assert not missing, (
        f"State constants without SLOT_PRESENTATION_LABELS entry: {missing}"
    )


# ---------------------------------------------------------------------------
# 2. No orphan labels
# ---------------------------------------------------------------------------


def test_no_orphan_labels():
    """Every key in SLOT_PRESENTATION_LABELS has a matching state constant value."""
    constant_values = set(_all_state_constants().values())
    orphans = {key for key in sp.SLOT_PRESENTATION_LABELS if key not in constant_values}
    assert not orphans, (
        f"SLOT_PRESENTATION_LABELS keys without matching state constant: {orphans}"
    )


# ---------------------------------------------------------------------------
# 3. classify_slot_presentation covers all known values
# ---------------------------------------------------------------------------

# All UNBOUND_* and STATUS_* known to the reconciler (sourced from ams_rfid_reconcile.py)
_KNOWN_UNBOUND_REASONS = [
    "UNBOUND_TRAY_EMPTY",
    "UNBOUND_TRAY_UNAVAILABLE",
    "UNBOUND_TAG_UID_NO_MATCH",
    "UNBOUND_TAG_UID_AMBIGUOUS",
    "UNBOUND_TAG_UID_INELIGIBLE_LOCATION_NEW",
    "UNBOUND_NO_TAG_UID",
    "UNBOUND_NO_RFID_TAG_ALL_ZERO",
    "UNBOUND_SELECTED_UID_MISMATCH",
    "UNBOUND_HELPER_SPOOL_NOT_FOUND",
    "UNBOUND_SPOOLMAN_LOOKUP_FAILED",
    "UNBOUND_HELPER_RFID_MISMATCH",
    "UNBOUND_HELPER_MATERIAL_MISMATCH",
    "UNBOUND_ERROR",
    "NONRFID_NO_MATCH_CONFIDENT",      # UNBOUND_NONRFID_NO_MATCH
    "LOW_CONFIDENCE_GENERIC_TRAY",     # UNBOUND_LOW_CONFIDENCE
    "RFID_NOT_REFRESHED_TRY_UNLOAD_LOAD",  # UNBOUND_RFID_NOT_REFRESHED
    "AMBIGUOUS_SIG_RFID",
    "AMBIGUOUS_SIG_NONRFID",
    "NO_CANDIDATE",
    "FORCE_ACCEPTED",
]

_KNOWN_STATUSES = [
    "OK",
    "OK: FIXED_EXPECTED",
    "OK_NON_RFID_REGISTERED",
    "NON_RFID_REGISTERED",
    "RFID_IDENTITY_STUCK",
    "CONFLICT: DUPLICATE_UID",
    "CONFLICT: missing_canonical_location",
    "CONFLICT: AMBIGUOUS_METADATA_NO_UNREGISTERED",
    "PENDING_RFID_READ",
    "CONFLICT: MISMATCH",
    # UNBOUND statuses (as unbound_reason will be set too)
    "UNBOUND: ACTION_REQUIRED",
    "UNBOUND: no_tag",
    "UNBOUND: TRAY_UNAVAILABLE",
    "",
]


@pytest.mark.parametrize("unbound_reason", _KNOWN_UNBOUND_REASONS)
def test_classify_covers_unbound_reasons(unbound_reason):
    """classify_slot_presentation returns a valid label key for all known UNBOUND_* values."""
    result = sp.classify_slot_presentation(unbound_reason=unbound_reason, status="")
    assert result in sp.SLOT_PRESENTATION_LABELS, (
        f"classify_slot_presentation(unbound_reason={unbound_reason!r}, status='') "
        f"returned {result!r} which is not in SLOT_PRESENTATION_LABELS"
    )


@pytest.mark.parametrize("status", _KNOWN_STATUSES)
def test_classify_covers_statuses(status):
    """classify_slot_presentation returns a valid label key for all known STATUS_* values."""
    result = sp.classify_slot_presentation(unbound_reason="", status=status)
    assert result in sp.SLOT_PRESENTATION_LABELS, (
        f"classify_slot_presentation(unbound_reason='', status={status!r}) "
        f"returned {result!r} which is not in SLOT_PRESENTATION_LABELS"
    )


# ---------------------------------------------------------------------------
# 4. Spot-check specific dispatch mappings
# ---------------------------------------------------------------------------


def test_dispatch_empty():
    assert sp.classify_slot_presentation("UNBOUND_TRAY_EMPTY", "") == sp.EMPTY


def test_dispatch_tray_unavailable():
    assert sp.classify_slot_presentation("UNBOUND_TRAY_UNAVAILABLE", "") == sp.TRAY_UNAVAILABLE


def test_dispatch_force_accepted():
    # FORCE_ACCEPTED checked before BOUND status check
    assert sp.classify_slot_presentation("FORCE_ACCEPTED", "OK") == sp.OK_FORCE_ACCEPTED


def test_dispatch_bound_ok():
    assert sp.classify_slot_presentation("", "OK") == sp.BOUND


def test_dispatch_bound_ok_fixed():
    assert sp.classify_slot_presentation("", "OK: FIXED_EXPECTED") == sp.BOUND


def test_dispatch_bound_nonrfid():
    assert sp.classify_slot_presentation("", "OK_NON_RFID_REGISTERED") == sp.BOUND


def test_dispatch_bound_nonrfid_registered():
    assert sp.classify_slot_presentation("", "NON_RFID_REGISTERED") == sp.BOUND


def test_dispatch_ambiguous_rfid():
    assert sp.classify_slot_presentation("AMBIGUOUS_SIG_RFID", "") == sp.NEEDS_BIND_AMBIGUOUS_RFID


def test_dispatch_ambiguous_nonrfid():
    assert sp.classify_slot_presentation("AMBIGUOUS_SIG_NONRFID", "") == sp.NEEDS_BIND_AMBIGUOUS_NONRFID


def test_dispatch_uid_ambiguous():
    assert sp.classify_slot_presentation("UNBOUND_TAG_UID_AMBIGUOUS", "") == sp.NEEDS_BIND_AMBIGUOUS_RFID


def test_dispatch_new_rfid():
    assert sp.classify_slot_presentation("UNBOUND_TAG_UID_NO_MATCH", "") == sp.NEEDS_BIND_NEW_RFID


def test_dispatch_location_new():
    assert sp.classify_slot_presentation("UNBOUND_TAG_UID_INELIGIBLE_LOCATION_NEW", "") == sp.NEEDS_BIND_LOCATION_NEW


def test_dispatch_nonrfid_no_tag():
    assert sp.classify_slot_presentation("UNBOUND_NO_RFID_TAG_ALL_ZERO", "") == sp.NEEDS_BIND_NONRFID


def test_dispatch_no_tag_uid():
    assert sp.classify_slot_presentation("UNBOUND_NO_TAG_UID", "") == sp.NEEDS_BIND_NONRFID


def test_dispatch_nonrfid_no_match():
    assert sp.classify_slot_presentation("NONRFID_NO_MATCH_CONFIDENT", "") == sp.NEEDS_BIND_NONRFID


def test_dispatch_no_candidate():
    assert sp.classify_slot_presentation("NO_CANDIDATE", "") == sp.NEEDS_BIND_NONRFID


def test_dispatch_low_confidence():
    assert sp.classify_slot_presentation("LOW_CONFIDENCE_GENERIC_TRAY", "") == sp.NEEDS_BIND_LOW_CONFIDENCE


def test_dispatch_rfid_not_refreshed():
    assert sp.classify_slot_presentation("RFID_NOT_REFRESHED_TRY_UNLOAD_LOAD", "") == sp.NEEDS_RESEAT


def test_dispatch_selected_uid_mismatch():
    assert sp.classify_slot_presentation("UNBOUND_SELECTED_UID_MISMATCH", "") == sp.NEEDS_RESEAT


def test_dispatch_helper_stale():
    assert sp.classify_slot_presentation("UNBOUND_HELPER_SPOOL_NOT_FOUND", "") == sp.HELPER_STALE


def test_dispatch_spoolman_error():
    assert sp.classify_slot_presentation("UNBOUND_SPOOLMAN_LOOKUP_FAILED", "") == sp.SPOOLMAN_ERROR


def test_dispatch_rfid_mismatch():
    assert sp.classify_slot_presentation("UNBOUND_HELPER_RFID_MISMATCH", "") == sp.CONFLICT_RFID_MISMATCH


def test_dispatch_material_mismatch():
    assert sp.classify_slot_presentation("UNBOUND_HELPER_MATERIAL_MISMATCH", "") == sp.CONFLICT_MATERIAL_MISMATCH


def test_dispatch_error():
    assert sp.classify_slot_presentation("UNBOUND_ERROR", "") == sp.ERROR


def test_dispatch_rfid_identity_stuck():
    assert sp.classify_slot_presentation("", "RFID_IDENTITY_STUCK") == sp.NEEDS_RESEAT


def test_dispatch_duplicate_uid_status():
    assert sp.classify_slot_presentation("", "CONFLICT: DUPLICATE_UID") == sp.CONFLICT_DUPLICATE_UID


def test_dispatch_conflict_location():
    assert sp.classify_slot_presentation("", "CONFLICT: missing_canonical_location") == sp.CONFLICT_LOCATION


def test_dispatch_conflict_ambiguous_metadata():
    assert sp.classify_slot_presentation("", "CONFLICT: AMBIGUOUS_METADATA_NO_UNREGISTERED") == sp.NEEDS_BIND_AMBIGUOUS_RFID


def test_dispatch_pending_rfid():
    assert sp.classify_slot_presentation("", "PENDING_RFID_READ") == sp.PENDING_RFID


def test_dispatch_unknown_fallthrough():
    assert sp.classify_slot_presentation("SOME_UNKNOWN_REASON", "SOME_UNKNOWN_STATUS") == sp.UNKNOWN


def test_label_dataclass_frozen():
    """PresentationLabel is frozen (immutable)."""
    label = sp.PresentationLabel("test")
    with pytest.raises((AttributeError, TypeError)):
        label.primary = "changed"


def test_label_count():
    """Exactly 20 states in SLOT_PRESENTATION_LABELS."""
    assert len(sp.SLOT_PRESENTATION_LABELS) == 20


def test_presentation_label_with_action():
    """Labels with actions have non-None action strings."""
    label = sp.SLOT_PRESENTATION_LABELS[sp.NEEDS_BIND_NEW_RFID]
    assert label.primary == "New spool detected"
    assert label.action == "Tap to add to Spoolman"


def test_presentation_label_without_action():
    """Labels without actions have None action."""
    label = sp.SLOT_PRESENTATION_LABELS[sp.EMPTY]
    assert label.primary == "Empty"
    assert label.action is None
