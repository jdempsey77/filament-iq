#!/usr/bin/env python3
"""
test_multi_spool_runout.py — Tests for multi-spool runout split fix.

When a non-RFID spool depletes mid-print and Bambu auto-swaps to a second
slot, consumption must be split: depleted slot gets spoolman_remaining,
finishing slot gets total_3mf_g - depleted_share.
"""

import json
import os
import sys
import tempfile
import types

import pytest

# Bootstrap fake hassapi
if "hassapi" not in sys.modules:
    _hassapi = types.ModuleType("hassapi")

    class _FakeHass:
        def __init__(self, ad=None, name=None, logger=None, args=None,
                     config=None, app_config=None, global_vars=None):
            self.args = args or {}

        def log(self, msg, level="INFO"):
            pass

    _hassapi.Hass = _FakeHass
    sys.modules["hassapi"] = _hassapi

_APPS = os.path.join(os.path.dirname(__file__), "..", "appdaemon", "apps")
if _APPS not in sys.path:
    sys.path.insert(0, _APPS)

from test_ams_print_usage_sync import (
    _TestableUsageSync,
    _has_log,
    _rfid_tag_uid_for_slots,
)
from conftest import SpoolmanRecorder
from filament_iq.consumption_engine import SlotInput, decide_consumption
from filament_iq.ams_print_usage_sync import ACTIVE_PRINT_FILE


# ── helpers ──────────────────────────────────────────────────────────

def _make_app(state_map=None, args=None):
    a = {"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True}
    a.update(args or {})
    return _TestableUsageSync(state_map=state_map, args=a)


# ── Fix 1: spool_id_snapshot key coercion ────────────────────────────

def test_spool_id_snapshot_key_coercion():
    """active_print.json string keys coerced to int on load."""
    app = _make_app()
    app._job_key = "coerce_test"
    with tempfile.TemporaryDirectory() as td:
        ap_file = os.path.join(td, "active_print.json")
        import filament_iq.ams_print_usage_sync as mod
        orig = mod.ACTIVE_PRINT_FILE
        try:
            mod.ACTIVE_PRINT_FILE = type(orig)(ap_file)
            data = {
                "job_key": "coerce_test",
                "trays_used": [3, 4],
                "spool_id_snapshot": {"3": 65, "4": 58},
                "threemf_data": None,
            }
            mod.ACTIVE_PRINT_FILE.write_text(json.dumps(data))
            result = app._load_active_print("coerce_test")
            assert result is not None
            assert result["spool_id_snapshot"][4] == 58
            assert result["spool_id_snapshot"][3] == 65
            assert isinstance(list(result["spool_id_snapshot"].keys())[0], int)
        finally:
            mod.ACTIVE_PRINT_FILE = orig


# ── Fix 2: spool_id_snapshot fallback ────────────────────────────────

def test_spool_id_snapshot_fallback_when_live_helper_zero():
    """Live helper returns 0 → snapshot consulted → slot not skipped."""
    state_map = {
        "input_text.ams_slot_3_spool_id": "65",
        "input_text.ams_slot_4_spool_id": "0",  # cleared by reconciler
    }
    app = _make_app(state_map=state_map)
    app._spool_id_snapshot = {3: 65, 4: 58}
    app._trays_used = {3, 4}
    app._start_snapshot = {3: 839.0}
    app._end_snapshot = {3: 700.0}
    app.threemf_enabled = True

    # Slot 4 entity state for tray_empty check
    entity_4 = app._tray_entity_by_slot.get(4)
    if entity_4:
        app._state_map[entity_4] = "Empty"

    threemf_matched = {3: (160.72, "single_filament_force"), 4: (100.0, "runout_split_depleted")}
    inputs = app._collect_print_inputs(
        trays_used={3, 4},
        start_snapshot=app._start_snapshot,
        end_snapshot=app._end_snapshot,
        threemf_matched_slots=threemf_matched,
        spools_cache={},
    )
    slot_4_inputs = [i for i in inputs if i.slot == 4]
    assert len(slot_4_inputs) == 1, "slot 4 must not be skipped"
    assert slot_4_inputs[0].spool_id == 58
    assert _has_log(app, "SPOOL_ID_FROM_SNAPSHOT")


# ── Fix 3: start_snapshot intersection relaxed ───────────────────────

def test_active_slots_includes_snapshot_slots():
    """Non-RFID slot in spool_id_snapshot but not start_snapshot is admitted."""
    state_map = {
        "input_text.ams_slot_3_spool_id": "65",
        "input_text.ams_slot_4_spool_id": "58",
    }
    app = _make_app(state_map=state_map)
    app._trays_used = {3, 4}
    app._start_snapshot = {3: 839.0}  # slot 4 absent — non-RFID no gauge
    app._spool_id_snapshot = {3: 65, 4: 58}
    app._end_snapshot = {3: 700.0}
    app._job_key = "test_snap"
    app.threemf_enabled = False
    app._threemf_data = None

    app._do_finish("finish")

    # Verify slot 4 was not dropped
    assert not _has_log(app, "USAGE_SKIP reason=NO_ACTIVE_SLOTS")
    # Check active_slots log shows both slots
    narrowed_logs = [m for m, _ in app._log_calls if "ACTIVE_SLOTS_NARROWED" in m]
    if narrowed_logs:
        # If narrowed, both should still be in the result
        assert "4" in narrowed_logs[0] or not narrowed_logs


def test_active_slots_rfid_guard_preserved():
    """RFID slot missing from start_snapshot but in spool_id_snapshot is admitted."""
    state_map = {
        "input_text.ams_slot_1_spool_id": "10",
        "input_text.ams_slot_2_spool_id": "11",
    }
    app = _make_app(state_map=state_map)
    app._trays_used = {1, 2}
    app._start_snapshot = {1: 500.0}  # slot 2 absent
    app._spool_id_snapshot = {1: 10, 2: 11}
    app._end_snapshot = {1: 400.0}
    app._job_key = "rfid_guard"
    app.threemf_enabled = False
    app._threemf_data = None

    app._do_finish("finish")

    # Slot 2 should be admitted (it's in snapshot)
    assert not _has_log(app, "USAGE_SKIP reason=NO_ACTIVE_SLOTS")


# ── Fix 4: _detect_runout_split ──────────────────────────────────────

def test_no_runout_split_for_single_slot():
    """Single-slot print: _detect_runout_split is a no-op."""
    app = _make_app()
    app._trays_used = {3}
    app._spool_id_snapshot = {3: 65}
    matched = {3: (160.0, "single_filament_force")}
    result = app._detect_runout_split(matched, {})
    assert result == matched
    assert not _has_log(app, "RUNOUT_SPLIT_DETECTED")


def test_no_runout_split_when_depleted_slot_unbound():
    """Depleted slot with no spool_id (not in snapshot): no split."""
    app = _make_app()
    app._trays_used = {3, 4}
    app._spool_id_snapshot = {3: 65}  # slot 4 NOT in snapshot
    entity_4 = app._tray_entity_by_slot.get(4)
    if entity_4:
        app._state_map[entity_4] = "Empty"
    matched = {3: (160.0, "single_filament_force")}
    result = app._detect_runout_split(matched, {})
    assert result == matched
    assert not _has_log(app, "RUNOUT_SPLIT_DETECTED")


def test_runout_split_remaining_based():
    """Core test: depleted slot gets spoolman_remaining, finishing gets the rest."""
    state_map = {
        "input_text.ams_slot_3_spool_id": "65",
        "input_text.ams_slot_4_spool_id": "0",
    }
    app = _make_app(state_map=state_map)
    app._trays_used = {3, 4}
    app._spool_id_snapshot = {3: 65, 4: 58}

    entity_4 = app._tray_entity_by_slot.get(4)
    if entity_4:
        app._state_map[entity_4] = "Empty"

    spools_cache = {58: {"remaining_weight": 100.0, "id": 58}}
    matched = {3: (160.72, "single_filament_force")}

    result = app._detect_runout_split(matched, spools_cache)

    assert _has_log(app, "RUNOUT_SPLIT_DETECTED")
    assert result[3] == (60.72, "runout_split")
    assert result[4] == (100.0, "runout_split_depleted")


def test_runout_split_clamps_when_remaining_exceeds_total():
    """spoolman_remaining > total_3mf_g: depleted gets total, finishing gets 0."""
    state_map = {
        "input_text.ams_slot_3_spool_id": "65",
        "input_text.ams_slot_4_spool_id": "0",
    }
    app = _make_app(state_map=state_map)
    app._trays_used = {3, 4}
    app._spool_id_snapshot = {3: 65, 4: 58}

    entity_4 = app._tray_entity_by_slot.get(4)
    if entity_4:
        app._state_map[entity_4] = "Empty"

    spools_cache = {58: {"remaining_weight": 200.0, "id": 58}}
    matched = {3: (160.72, "single_filament_force")}

    result = app._detect_runout_split(matched, spools_cache)

    assert result[4][0] == 160.72  # clamped to total
    assert result[3][0] == 0.0     # finishing gets nothing


def test_runout_split_when_remaining_already_zero():
    """spoolman_remaining=0: depleted gets 0, finishing gets all."""
    state_map = {
        "input_text.ams_slot_3_spool_id": "65",
        "input_text.ams_slot_4_spool_id": "0",
    }
    app = _make_app(state_map=state_map)
    app._trays_used = {3, 4}
    app._spool_id_snapshot = {3: 65, 4: 58}

    entity_4 = app._tray_entity_by_slot.get(4)
    if entity_4:
        app._state_map[entity_4] = "Empty"

    spools_cache = {58: {"remaining_weight": 0.0, "id": 58}}
    matched = {3: (160.72, "single_filament_force")}

    result = app._detect_runout_split(matched, spools_cache)

    assert result[4][0] == 0.0
    assert result[3][0] == 160.72


def test_runout_split_engine_no_overcount():
    """Depleted slot: threemf_used_g == spoolman_remaining → max() is no-op."""
    inp = SlotInput(
        slot=4,
        spool_id=58,
        is_rfid=False,
        tray_empty=True,
        tray_active_seconds=3600.0,
        start_g=None,
        end_g=None,
        threemf_used_g=100.0,
        threemf_method="runout_split_depleted",
        spoolman_remaining=100.0,  # equal to threemf_used_g
    )
    decisions = decide_consumption([inp], min_consumption_g=2.0, max_consumption_g=1000.0)
    assert len(decisions) == 1
    d = decisions[0]
    assert d.method == "3mf_depleted"
    assert d.consumption_g == 100.0  # NOT 200.0 (the overcount)


def test_runout_split_end_to_end():
    """Full pipeline: slot 4 depletes, slot 3 finishes. Both get writes."""
    state_map = {
        "input_text.ams_slot_3_spool_id": "65",
        "input_text.ams_slot_4_spool_id": "0",  # reconciler cleared
    }
    app = _make_app(state_map=state_map)
    app._trays_used = {3, 4}
    app._start_snapshot = {3: 839.0}  # slot 4 absent (non-RFID)
    app._spool_id_snapshot = {3: 65, 4: 58}
    app._job_key = "desk_organizer_test"
    app.threemf_enabled = True
    app._threemf_data = [{"index": 0, "used_g": 160.72, "color_hex": "161616", "material": "pla"}]

    # Slot 4 is Empty (depleted), non-RFID
    entity_4 = app._tray_entity_by_slot.get(4)
    if entity_4:
        app._state_map[entity_4] = "Empty"
        app._state_map[f"{entity_4}::tag_uid"] = "0000000000000000"

    # Slot 3 is not empty, non-RFID
    entity_3 = app._tray_entity_by_slot.get(3)
    if entity_3:
        app._state_map[entity_3] = "loaded"
        app._state_map[f"{entity_3}::tag_uid"] = "0000000000000000"
        app._state_map[f"{entity_3}::color"] = "#161616FF"
        app._state_map[f"{entity_3}::type"] = "PLA"

    # Mock Spoolman: spool 58 has 100g remaining
    app._spoolman_get_override = lambda path: (
        {"remaining_weight": 100.0, "id": 58} if "58" in path else {"remaining_weight": 500.0}
    )
    # Use remaining override for post-write response
    app._use_remaining_override = {58: 0.0, 65: 439.28}

    app._do_finish("finish")

    # Verify two /use writes
    assert len(app._use_calls) == 2, f"expected 2 writes, got {len(app._use_calls)}: {app._use_calls}"

    slot4_writes = [c for c in app._use_calls if c["spool_id"] == 58]
    slot3_writes = [c for c in app._use_calls if c["spool_id"] == 65]

    assert len(slot4_writes) == 1, "slot 4 (depleted) must get a write"
    assert len(slot3_writes) == 1, "slot 3 (finishing) must get a write"
    assert abs(slot4_writes[0]["use_weight"] - 100.0) < 0.1
    assert abs(slot3_writes[0]["use_weight"] - 60.72) < 0.1

    total = slot4_writes[0]["use_weight"] + slot3_writes[0]["use_weight"]
    assert abs(total - 160.72) < 0.1, f"total must equal 3MF total: {total}"

    assert _has_log(app, "RUNOUT_SPLIT_DETECTED")
    assert _has_log(app, "SPOOL_ID_FROM_SNAPSHOT")


def test_skip_runout_split_multiple_depleted():
    """Multiple depleted slots: skip split, log warning."""
    state_map = {
        "input_text.ams_slot_3_spool_id": "65",
        "input_text.ams_slot_4_spool_id": "0",
        "input_text.ams_slot_5_spool_id": "0",
    }
    app = _make_app(state_map=state_map)
    app._trays_used = {3, 4, 5}
    app._spool_id_snapshot = {3: 65, 4: 58, 5: 29}

    for slot in (4, 5):
        entity = app._tray_entity_by_slot.get(slot)
        if entity:
            app._state_map[entity] = "Empty"

    matched = {3: (160.72, "single_filament_force")}
    result = app._detect_runout_split(matched, {})

    assert result == matched  # unchanged
    assert _has_log(app, "RUNOUT_SPLIT_SKIP reason=multiple_depleted_slots")


# ── Bug 14: Runout split RFID finishing slot on rehydrated print ─────

def test_runout_split_rfid_finishing_slot_rehydrated_print():
    """Bug 14: On rehydrated prints, RFID finishing slot must use finishing_share,
    not the stale RFID delta (start_g ≈ end_g → 0.0g → BELOW_MIN).

    Scenario: slot 2 (non-RFID) depleted mid-print, slot 3 (RFID) finished.
    3MF matched to slot 3 only. _detect_runout_split computes:
      depleted_share=32.03g for slot 2, finishing_share=149.38g for slot 3.
    Without the fix, slot 3's finishing_share is discarded by RFID suppression.
    """
    state_map = {
        "input_text.ams_slot_2_spool_id": "0",  # reconciler cleared after depletion
        "input_text.ams_slot_3_spool_id": "72",
    }
    app = _make_app(state_map=state_map)

    # Rehydrated print: start snapshot from fuel gauges mid-print (stale for slot 3)
    app._rehydrated = True
    app._trays_used = {2, 3}
    app._start_snapshot = {2: 32.0, 3: 1000.0}  # slot 3 reads ~1000 mid-print (stale)
    app._end_snapshot = {2: 0.0, 3: 1000.0}  # slot 3 unchanged — delta ≈ 0
    app._spool_id_snapshot = {2: 76, 3: 72}
    app._job_key = "runout_rfid_rehydrated_001"
    app.threemf_enabled = True
    app._threemf_data = [{"index": 0, "used_g": 181.41, "color_hex": "afb1ae", "material": "pla"}]
    app._print_active = True
    app._print_start_time = __import__("time").time() - 7200

    # Slot 2: non-RFID, Empty (depleted)
    entity_2 = app._tray_entity_by_slot.get(2)
    if entity_2:
        app._state_map[entity_2] = "Empty"
        app._state_map[f"{entity_2}::tag_uid"] = "0000000000000000"

    # Slot 3: RFID, loaded (finishing spool)
    entity_3 = app._tray_entity_by_slot.get(3)
    if entity_3:
        app._state_map[entity_3] = "loaded"
        app._state_map.update(_rfid_tag_uid_for_slots(app, [3]))
        app._state_map[f"{entity_3}::color"] = "#AFB1AEFF"
        app._state_map[f"{entity_3}::type"] = "PLA"

    # Mock Spoolman: spool 76 has 32.03g remaining (about to deplete)
    app._spoolman_get_override = lambda path: (
        {"remaining_weight": 32.03, "id": 76} if "76" in path
        else {"remaining_weight": 850.0, "id": 72}
    )

    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(76, remaining=0.0)
    recorder.set_use_response(72, remaining=700.62)

    app._do_finish("finish")

    # Slot 2 (depleted): should get depleted_share
    recorder.assert_used(76, 32.03, tolerance=1.0)

    # Slot 3 (RFID finishing): should get finishing_share, NOT 0.0g from stale RFID delta
    recorder.assert_used(72, 149.38, tolerance=1.0)

    # Neither slot should be no_evidence
    assert not any(
        "USAGE_NO_EVIDENCE" in msg and "slot=3" in msg
        for msg, _ in app._log_calls
    ), "slot 3 must not be no_evidence — finishing_share should be used"

    assert _has_log(app, "RUNOUT_SPLIT_DETECTED")
