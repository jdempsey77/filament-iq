#!/usr/bin/env python3
"""
Serial-delta binding invalidation on printer hardware swap (v1.11.0).

Covers all 10 scenarios from the implementation spec:
  1. Fresh install / empty persisted serial -> no quarantine, serial recorded
  2. Same serial across restart -> no quarantine, no writes
  3. Changed serial, normal slots -> quarantine; spool_id/expected preserved
  4. Changed serial + one FORCE_ACCEPTED slot -> that slot skipped
  5. Post-quarantine reconcile, RFID match -> slot heals (reason cleared)
  6. Post-quarantine reconcile, different spool -> normal mismatch path
  7. Missing helper entity -> detection disabled, no crash
  8. Missing helper -> no infinite loop (serial never written when disabled)
  9. Serial normalization (case/whitespace) -> no false quarantine
 10. All physical slots covered (1-8 incl external slot 8 + HT slots 5/6/7)

Reuses the deterministic harness from test_ams_rfid_reconcile.py.
"""

import pytest

from test_ams_rfid_reconcile import (
    TestableReconcile as Reconcile,
    FakeSpoolman,
    _spool,
    _bambu_filament,
    _tray_entity,
)
from filament_iq.ams_rfid_reconcile import (
    PRINTER_SERIAL_CHANGED,
    UNBOUND_HELPER_RFID_MISMATCH,
)

SERIAL_ENTITY = "input_text.filament_iq_last_printer_serial"

# Production 8-slot topology (matches apps.yaml): AMS 2 Pro 1-4, HT 5/6/7, external 8.
EIGHT_SLOT_AMS_UNITS = [
    {"type": "ams_2_pro", "ams_index": 0, "slots": [1, 2, 3, 4]},
    {"type": "ams_ht", "ams_index": 128, "slots": [5]},
    {"type": "ams_ht", "ams_index": 129, "slots": [6]},
    {"type": "ams_ht", "ams_index": 130, "slots": [7]},
    {"type": "external", "slots": [8]},
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _serial_state(value):
    """state_map entries (plain + ::all) for the persisted-serial helper."""
    return {
        SERIAL_ENTITY: value,
        f"{SERIAL_ENTITY}::all": {"state": value, "attributes": {}},
    }


def _build_app(persisted_serial, *, slots=None, current_serial="01p00c5a3101668",
               ams_units=None, include_serial_helper=True):
    """Build a TestableReconcile with serial-delta detection wired up.

    slots: dict[int -> dict(spool_id, expected, reason)] of slot helper seed values.
    """
    args = {
        "printer_serial": current_serial,
        "spoolman_url": "http://192.0.2.1:7912",
    }
    if ams_units is not None:
        args["ams_units"] = ams_units
    state_map = {}
    if include_serial_helper:
        state_map.update(_serial_state(persisted_serial))
    for slot, vals in (slots or {}).items():
        state_map[f"input_text.ams_slot_{slot}_spool_id"] = str(vals.get("spool_id", "0"))
        state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = str(vals.get("expected", "0"))
        state_map[f"input_text.ams_slot_{slot}_unbound_reason"] = str(vals.get("reason", ""))
        state_map[f"input_text.ams_slot_{slot}_status"] = str(vals.get("status", ""))
    sm = FakeSpoolman([], [])
    r = Reconcile(sm, state_map, args=args)
    r._serial_detection_enabled = True
    r._last_printer_serial_entity = SERIAL_ENTITY
    return r


def _writes_for(r, entity_id):
    return [w for w in r._helper_writes if w.get("entity_id") == entity_id]


def _logged(r, needle):
    return any(needle in msg for msg, _level in r._log_calls)


# ── 1. Fresh install ──────────────────────────────────────────────────────────

def test_s1_fresh_install_records_serial_no_quarantine():
    r = _build_app("", slots={1: {"spool_id": 5, "expected": 5}})
    r._maybe_quarantine_for_serial_change()
    assert _logged(r, "SERIAL_DELTA_FRESH_INSTALL")
    serial_writes = _writes_for(r, SERIAL_ENTITY)
    assert serial_writes and serial_writes[-1]["value"] == "01p00c5a3101668"
    # No slot was quarantined.
    assert not _writes_for(r, "input_text.ams_slot_1_status")
    assert not _logged(r, "SERIAL_QUARANTINE_APPLIED")


# ── 2. Same serial across restart ─────────────────────────────────────────────

def test_s2_same_serial_no_writes():
    r = _build_app("01p00c5a3101668", slots={1: {"spool_id": 5, "expected": 5}})
    r._maybe_quarantine_for_serial_change()
    assert _logged(r, "SERIAL_DELTA_NONE")
    assert r._helper_writes == []  # nothing written at all


# ── 3. Changed serial, normal slots ───────────────────────────────────────────

def test_s3_changed_serial_quarantines_preserving_ids():
    slots = {1: {"spool_id": 11, "expected": 11}, 2: {"spool_id": 22, "expected": 22}}
    r = _build_app("OLDSERIAL999", slots=slots)
    r._maybe_quarantine_for_serial_change()
    assert _logged(r, "SERIAL_DELTA_DETECTED")
    for slot, sid in ((1, 11), (2, 22)):
        status_writes = _writes_for(r, f"input_text.ams_slot_{slot}_status")
        assert any(w["value"] == "UNBOUND: PRINTER_SERIAL_CHANGED" for w in status_writes)
        reason_writes = _writes_for(r, f"input_text.ams_slot_{slot}_unbound_reason")
        assert any(w["value"] == PRINTER_SERIAL_CHANGED for w in reason_writes)
        # spool_id / expected_spool_id PRESERVED — never zeroed.
        assert not _writes_for(r, f"input_text.ams_slot_{slot}_spool_id")
        assert not _writes_for(r, f"input_text.ams_slot_{slot}_expected_spool_id")
    # Serial persisted AFTER quarantine.
    serial_writes = _writes_for(r, SERIAL_ENTITY)
    assert serial_writes and serial_writes[-1]["value"] == "01p00c5a3101668"
    assert _logged(r, "SERIAL_DELTA_PERSISTED")


# ── 4. Changed serial with a FORCE_ACCEPTED slot ──────────────────────────────

def test_s4_force_accepted_slot_exempt():
    slots = {
        1: {"spool_id": 11, "expected": 11},
        3: {"spool_id": 33, "expected": 33, "reason": "FORCE_ACCEPTED"},
    }
    r = _build_app("OLDSERIAL999", slots=slots)
    r._maybe_quarantine_for_serial_change()
    # Slot 3 exempt: no status write, skip logged.
    assert not _writes_for(r, "input_text.ams_slot_3_status")
    assert _logged(r, "SERIAL_QUARANTINE_SKIP_FORCE_ACCEPTED slot=3")
    # Slot 1 quarantined.
    assert any(
        w["value"] == "UNBOUND: PRINTER_SERIAL_CHANGED"
        for w in _writes_for(r, "input_text.ams_slot_1_status")
    )
    assert _logged(r, "slots_skipped_force_accepted=1")


# ── 7. Missing helper -> detection disabled ───────────────────────────────────

def test_s7_missing_helper_disables_detection():
    r = _build_app("", include_serial_helper=False)
    r._serial_detection_enabled = None  # force _init_serial_detection to set it
    r._init_serial_detection()
    assert r._serial_detection_enabled is False
    assert _logged(r, "SERIAL_HELPER_MISSING")


# ── 8. Missing helper -> no infinite loop (serial never written) ──────────────

def test_s8_disabled_detection_never_writes_serial():
    r = _build_app("", include_serial_helper=False)
    r._init_serial_detection()
    assert r._serial_detection_enabled is False
    # Replicate the _run_reconcile_startup call-site guard: disabled => never called.
    if r._serial_detection_enabled:
        r._maybe_quarantine_for_serial_change()
    assert _writes_for(r, SERIAL_ENTITY) == []
    assert not _logged(r, "SERIAL_DELTA_FRESH_INSTALL")


# ── 9. Serial normalization (case + whitespace) ───────────────────────────────

def test_s9_normalization_no_false_quarantine():
    r = _build_app("  01P00C5A3101668  ", slots={1: {"spool_id": 11, "expected": 11}})
    r._maybe_quarantine_for_serial_change()
    assert _logged(r, "SERIAL_DELTA_NONE")
    assert not _logged(r, "SERIAL_QUARANTINE_APPLIED")
    assert not _writes_for(r, "input_text.ams_slot_1_status")


# ── 10. All 8 physical slots covered ──────────────────────────────────────────

def test_s10_all_eight_slots_quarantined():
    slots = {n: {"spool_id": n * 10, "expected": n * 10} for n in range(1, 9)}
    r = _build_app("OLDSERIAL999", slots=slots, ams_units=EIGHT_SLOT_AMS_UNITS)
    assert r._physical_ams_slots == (1, 2, 3, 4, 5, 6, 7, 8)
    r._maybe_quarantine_for_serial_change()
    for slot in range(1, 9):
        assert any(
            w["value"] == "UNBOUND: PRINTER_SERIAL_CHANGED"
            for w in _writes_for(r, f"input_text.ams_slot_{slot}_status")
        ), f"slot {slot} not quarantined"
    assert _logged(r, "slots_quarantined=8")


# ── 5 & 6. Post-quarantine reconcile self-heal ────────────────────────────────

def _reconcile_state_map(slot, tray_tag, spool_helper, spool_other=None):
    """Build a single-slot reconcile state_map with slot pre-quarantined."""
    attrs = {"tag_uid": tray_tag, "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
             "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
    state_map = {}
    for s in range(1, 7):
        state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
        state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
        state_map[f"input_text.ams_slot_{s}_status"] = ""
        state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
        state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""
        if s == slot:
            state_map[_tray_entity(s)] = {"attributes": attrs, "state": "valid"}
            state_map[f"{_tray_entity(s)}::all"] = {"attributes": attrs, "state": "valid"}
        else:
            empty_a = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "",
                       "tray_weight": 0, "remain": 0}
            state_map[_tray_entity(s)] = {"attributes": empty_a, "state": "empty"}
            state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_a, "state": "empty"}
    # Pre-quarantine slot under test.
    state_map[f"input_text.ams_slot_{slot}_spool_id"] = str(spool_helper)
    state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = str(spool_helper)
    state_map[f"input_text.ams_slot_{slot}_unbound_reason"] = PRINTER_SERIAL_CHANGED
    state_map[f"input_text.ams_slot_{slot}_status"] = "UNBOUND: PRINTER_SERIAL_CHANGED"
    return state_map


def test_s5_post_quarantine_rfid_match_heals():
    """New printer reads the SAME spool's RFID -> truth guard passes, slot heals
    away from PRINTER_SERIAL_CHANGED (no sticky quarantine, no mismatch)."""
    slot = 1
    tag = "A6EC1BDE00000100"
    spool_helper = _spool(10, rfid_tag_uid=tag, location="Shelf", material="PLA", color_hex="ff0000")
    sm = FakeSpoolman([spool_helper], [_bambu_filament()])
    state_map = _reconcile_state_map(slot, tag, 10)
    r = Reconcile(sm, state_map, args={"printer_serial": "01p00c5a3101668",
                                               "spoolman_url": "http://192.0.2.1:7912"})
    r._run_reconcile("test")
    reason_writes = _writes_for(r, f"input_text.ams_slot_{slot}_unbound_reason")
    # Reason must NOT remain sticky on PRINTER_SERIAL_CHANGED.
    assert all(w["value"] != PRINTER_SERIAL_CHANGED for w in reason_writes)
    # No RFID truth-guard mismatch (identities match).
    assert all(w["value"] != UNBOUND_HELPER_RFID_MISMATCH for w in reason_writes)
    # Binding preserved (spool 10 stays bound; never cleared to 0).
    spool_writes = _writes_for(r, f"input_text.ams_slot_{slot}_spool_id")
    assert all(w["value"] != "0" for w in spool_writes)


def test_s6_post_quarantine_different_spool_normal_mismatch():
    """New printer holds a DIFFERENT spool -> normal mismatch path (quarantine
    does not sticky-suppress real anomalies); truth-guard PUSH is suppressed."""
    slot = 1
    tray_tag = "A6EC1BDE00000100"
    helper_tag = "DEADBEEF00000100"
    spool_helper = _spool(10, rfid_tag_uid=helper_tag, location="Shelf", material="PLA", color_hex="ff0000")
    spool_other = _spool(20, rfid_tag_uid=tray_tag, location="Shelf", material="PLA", color_hex="ff0000")
    sm = FakeSpoolman([spool_helper, spool_other], [_bambu_filament()])
    state_map = _reconcile_state_map(slot, tray_tag, 10)
    r = Reconcile(sm, state_map, args={"printer_serial": "01p00c5a3101668",
                                               "spoolman_url": "http://192.0.2.1:7912"})
    r._run_reconcile("test")
    reason_writes = _writes_for(r, f"input_text.ams_slot_{slot}_unbound_reason")
    # Slot left the quarantine state and took the real mismatch path.
    assert any(w["value"] == UNBOUND_HELPER_RFID_MISMATCH for w in reason_writes)
    assert all(w["value"] != PRINTER_SERIAL_CHANGED for w in reason_writes)
    # The truth-guard PUSH notification was suppressed during the swap window.
    assert _logged(r, "TRUTH_GUARD_NOTIFY_SUPPRESSED_SERIAL_SWAP")
    # Stale helper 10 cleared, resolution rebinds to the correct spool 20.
    spool_writes = _writes_for(r, f"input_text.ams_slot_{slot}_spool_id")
    assert any(w["value"] == "0" for w in spool_writes)
    assert spool_writes[-1]["value"] == "20"
