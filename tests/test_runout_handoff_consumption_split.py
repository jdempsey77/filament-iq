#!/usr/bin/env python3
"""
test_runout_handoff_consumption_split.py — Regression tests for the runout
handoff consumption split fix.

When an AMS spool runs out mid-print and Bambu auto-switches to a continuation
slot, two coupled bugs lost consumption from the continuation spool and
over-credited the depleted spool:

  Bug A — ACTIVE_PRINT_RESTORED overwrote live _trays_used (e.g. {1, 3}) with
          the print-start disk snapshot ({1}) even on a continuous run that
          never rehydrated, so the continuation slot got no credit.
  Bug B — with _trays_used collapsed to {1}, the depleted slot fell through to
          3mf_depleted and claimed the full slicer estimate.
  Bug C — _persist_active_print never ran after a mid-print tray switch, so the
          disk snapshot was always print-start-stale.
  Bug D — a phantom external slot (spool_id=0) with leftover active_time from an
          AMS handoff artifact wrongly entered attribution.

These tests cover the four fixes:
  1. trays_used restore gated on _rehydrated (continuous run not overwritten)
  2. rehydration unions disk trays_used with the live set
  3. phantom slot (spool_id=0) filtered before _detect_runout_split
  4. _persist_active_print runs when a NEW slot joins _trays_used
  5. _detect_runout_split sees the full {depleted, finishing} set on a
     continuous-run runout
  6. the depleted slot does not claim the full slicer estimate in a multi-slot
     finish (3MF_DEPLETED_MULTI_SLOT_GUARD safety net)
"""

import json
import os
import sys
import types

import pytest

# Bootstrap fake hassapi (mirrors the other suites)
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
    _default_state_map,
    _has_log,
    _active_tray_state,
    _rfid_tag_uid_for_slots,
)


def _make_app(state_map=None, args=None):
    a = {"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True}
    a.update(args or {})
    return _TestableUsageSync(state_map=state_map, args=a)


_THREEMF_ONE_FILAMENT = [
    {"index": 0, "used_g": 160.72, "color_hex": "161616", "material": "pla"}
]


# ── Change 1: trays_used restore gated on _rehydrated ─────────────────

def test_continuous_run_does_not_overwrite_live_trays_used(tmp_path):
    """Continuous run (_rehydrated=False): disk restore must NOT replace the
    live _trays_used. The print-start snapshot {3} must not clobber the live
    {3, 4} accumulated through a mid-print runout switch (Bug A)."""
    state_map = {
        "input_text.ams_slot_3_spool_id": "65",
        "input_text.ams_slot_4_spool_id": "0",
    }
    app = _make_app(state_map=state_map, args={"data_dir": str(tmp_path)})
    app._job_key = "cont_run"
    app._start_snapshot = {3: 839.0}
    app._last_processed_job_key = ""
    app.threemf_enabled = True
    app._threemf_data = None           # forces disk recovery
    app._rehydrated = False            # continuous run
    app._trays_used = {3, 4}           # live, continuation slot 4 tracked
    app._spool_id_snapshot = {3: 65, 4: 58}

    # Disk snapshot reflects print-start only: {3}
    app._active_print_file.write_text(json.dumps({
        "job_key": "cont_run",
        "trays_used": [3],
        "spool_id_snapshot": {"3": 65, "4": 58},
        "threemf_data": _THREEMF_ONE_FILAMENT,
    }))

    captured = {}

    def _fake_finish(status):
        captured["trays"] = set(app._trays_used)

    app._do_finish = _fake_finish
    app._on_print_finish("finish")

    assert captured["trays"] == {3, 4}, (
        "live _trays_used must survive disk restore on a continuous run"
    )
    # 3MF payload recovery is independent of the trays_used gate
    assert app._threemf_data == _THREEMF_ONE_FILAMENT


def test_rehydration_unions_trays_used_from_disk(tmp_path):
    """Rehydration path: disk trays_used is UNIONed with the live set, not
    replaced, so neither source loses slots."""
    state_map = {
        "input_text.ams_slot_1_spool_id": "10",
        "input_text.ams_slot_3_spool_id": "20",
        "input_text.ams_slot_5_spool_id": "30",
    }
    app = _make_app(state_map=state_map, args={"data_dir": str(tmp_path)})
    app._state_map[app._print_status_entity] = "running"
    app._job_key = "rehy_test"          # preserved (not from helper)
    app._trays_used = {5}               # live, observed before rehydrate

    # Disk snapshot carries {1, 3}
    app._active_print_file.write_text(json.dumps({
        "job_key": "rehy_test",
        "trays_used": [1, 3],
        "spool_id_snapshot": {"1": 10, "3": 20},
        "threemf_data": None,
    }))

    app._rehydrate_print_state()

    assert app._trays_used == {1, 3, 5}, (
        "rehydration must union disk and live trays_used"
    )


# ── Change 3: phantom slot (spool_id=0) filter ────────────────────────

def test_phantom_slot_filtered_before_attribution():
    """A slot with no bound spool (spool_id=0, absent from the snapshot) and
    leftover active_time from an AMS handoff is filtered out before runout
    detection / attribution, and never receives a write (Bug D)."""
    import datetime
    state_map = {
        "input_text.ams_slot_3_spool_id": "65",
        "input_text.ams_slot_8_spool_id": "0",   # unbound external slot
        "sensor.p1s_tray_3_fuel_gauge_remaining": "799",
    }
    app = _make_app(state_map=state_map)
    app._job_key = "phantom_test"
    app._trays_used = {3, 8}
    # Slot 8 has a stray gauge entry so it survives active_slots narrowing,
    # exercising the dedicated phantom filter rather than the narrow step.
    app._start_snapshot = {3: 839.0, 8: 12.0}
    app._spool_id_snapshot = {3: 65}             # slot 8 not bound at start
    app.threemf_enabled = False
    app._threemf_data = None

    # Slot 3 is a real RFID spool with a delta; slot 8 is the artifact.
    app._state_map.update(_rfid_tag_uid_for_slots(app, [3]))
    now = datetime.datetime.utcnow()
    app._tray_active_times = {
        8: [{"start": now - datetime.timedelta(seconds=544.9), "end": now}],
    }

    app._do_finish("finish")

    assert _has_log(app, "PHANTOM_SLOT_FILTERED slot=8")
    phantom_logs = [
        (m, lvl) for m, lvl in app._log_calls
        if "PHANTOM_SLOT_FILTERED slot=8" in m
    ]
    assert phantom_logs[0][1] == "WARNING"
    assert "active_time=" in phantom_logs[0][0]

    # Slot 8 must not be written; slot 3 still processed normally.
    written_spools = {c["spool_id"] for c in app._use_calls}
    assert 0 not in written_spools
    assert 65 in written_spools


def test_phantom_filter_keeps_depleted_slot_via_snapshot_fallback():
    """A depleted slot whose live helper reads 0 but is present in the
    print-start spool_id snapshot is NOT a phantom — it stays in attribution."""
    state_map = {
        "input_text.ams_slot_3_spool_id": "65",
        "input_text.ams_slot_4_spool_id": "0",   # live helper cleared on depletion
    }
    app = _make_app(state_map=state_map)
    app._job_key = "depleted_keep"
    app._trays_used = {3, 4}
    app._start_snapshot = {3: 839.0}
    app._spool_id_snapshot = {3: 65, 4: 58}       # slot 4 WAS bound at start
    app.threemf_enabled = False
    app._threemf_data = None

    app._do_finish("finish")

    assert not _has_log(app, "PHANTOM_SLOT_FILTERED slot=4")


# ── Change 2: persist on new-slot add ─────────────────────────────────

def test_persist_called_when_new_slot_added():
    """_on_active_tray_change persists exactly once per NEW slot, and not when
    an already-tracked slot re-activates (Bug C)."""
    app = _TestableUsageSync(state_map=_default_state_map({2: 5}))
    app._state_map.update(_active_tray_state(app, 0, 1))   # slot 2
    app._print_active = True
    app._job_key = "persist_test"
    app._spool_id_snapshot = {2: 5}

    calls = []
    app._persist_active_print = lambda *a, **k: calls.append(1)

    # New slot 2 → one persist
    app._on_active_tray_change(
        app._active_tray_entity, "state", "none", "Generic PLA", {}
    )
    assert 2 in app._trays_used
    assert len(calls) == 1

    # Same slot re-activates → no additional persist
    app._on_active_tray_change(
        app._active_tray_entity, "state", "Generic PLA", "Generic PLA", {}
    )
    assert len(calls) == 1

    # Switch to a NEW slot 4 → second persist
    app._state_map["input_text.ams_slot_4_spool_id"] = "10"
    app._state_map.update(_active_tray_state(app, 0, 3, "Overture Matte PLA"))
    app._on_active_tray_change(
        app._active_tray_entity, "state", "Generic PLA", "Overture Matte PLA", {}
    )
    assert 4 in app._trays_used
    assert len(calls) == 2


# ── Changes 1+3+4 together: end-to-end continuous-run runout ──────────

def _setup_continuous_runout(tmp_path):
    """Continuous run: slot 4 depletes, slot 3 finishes. Disk snapshot only
    carried print-start {3}; live tracking accumulated {3, 4}."""
    state_map = {
        "input_text.ams_slot_3_spool_id": "65",
        "input_text.ams_slot_4_spool_id": "0",   # reconciler cleared on depletion
    }
    app = _make_app(state_map=state_map, args={"data_dir": str(tmp_path)})
    app._job_key = "runout_e2e"
    app._start_snapshot = {3: 839.0}
    app._last_processed_job_key = ""
    app.threemf_enabled = True
    app._threemf_data = None                      # forces disk recovery
    app._rehydrated = False                       # continuous run
    app._trays_used = {3, 4}                       # live set
    app._spool_id_snapshot = {3: 65, 4: 58}

    app._active_print_file.write_text(json.dumps({
        "job_key": "runout_e2e",
        "trays_used": [3],                        # print-start snapshot only
        "spool_id_snapshot": {"3": 65, "4": 58},
        "threemf_data": _THREEMF_ONE_FILAMENT,
    }))

    # Slot 4 depleted (Empty), non-RFID
    entity_4 = app._tray_entity_by_slot.get(4)
    app._state_map[entity_4] = "Empty"
    app._state_map[f"{entity_4}::tag_uid"] = "0000000000000000"

    # Slot 3 finishing, non-RFID, color/material match for the 3MF entry
    entity_3 = app._tray_entity_by_slot.get(3)
    app._state_map[entity_3] = "loaded"
    app._state_map[f"{entity_3}::tag_uid"] = "0000000000000000"
    app._state_map[f"{entity_3}::color"] = "#161616FF"
    app._state_map[f"{entity_3}::type"] = "PLA"

    # Spool 58 has 100g remaining → depleted share = 100g
    app._spoolman_get_override = lambda path: (
        {"remaining_weight": 100.0, "id": 58} if "58" in path
        else {"remaining_weight": 500.0}
    )
    app._use_remaining_override = {58: 0.0, 65: 439.28}
    return app


def test_detect_runout_split_sees_full_slot_set(tmp_path):
    """On a continuous-run runout, _detect_runout_split must receive the full
    {3, 4} set — proving the disk restore did not collapse it to {3}."""
    app = _setup_continuous_runout(tmp_path)

    seen = {}
    _orig = app._detect_runout_split

    def _spy(matched, cache):
        seen["trays"] = set(app._trays_used)
        return _orig(matched, cache)

    app._detect_runout_split = _spy
    app._on_print_finish("finish")

    assert seen.get("trays") == {3, 4}
    assert _has_log(app, "RUNOUT_SPLIT_DETECTED")


def test_depleted_slot_not_credited_full_slicer_estimate(tmp_path):
    """Multi-slot finish: the depleted slot receives its split share (100g),
    NOT the full 160.72g slicer estimate (Bug B). Total stays conserved."""
    app = _setup_continuous_runout(tmp_path)
    app._on_print_finish("finish")

    assert len(app._use_calls) == 2, f"expected 2 writes, got {app._use_calls}"
    depleted = [c for c in app._use_calls if c["spool_id"] == 58]
    finishing = [c for c in app._use_calls if c["spool_id"] == 65]
    assert len(depleted) == 1 and len(finishing) == 1

    # Depleted slot must NOT get the full estimate.
    assert abs(depleted[0]["use_weight"] - 100.0) < 0.1
    assert depleted[0]["use_weight"] < 160.72
    assert abs(finishing[0]["use_weight"] - 60.72) < 0.1
    total = depleted[0]["use_weight"] + finishing[0]["use_weight"]
    assert abs(total - 160.72) < 0.1


# ── Change 4: 3mf_depleted multi-slot guard log ──────────────────────

def test_3mf_depleted_multi_slot_guard_logs():
    """Safety net: if a 3mf_depleted decision still arises in a multi-slot
    finish context, the guard surfaces it at WARNING level."""
    state_map = {
        "input_text.ams_slot_3_spool_id": "65",
        "input_text.ams_slot_4_spool_id": "58",
    }
    app = _make_app(state_map=state_map)
    app._job_key = "guard_test"
    app._trays_used = {3, 4}
    app._start_snapshot = {}
    app._spool_id_snapshot = {3: 65, 4: 58}
    app.threemf_enabled = True
    app._threemf_data = _THREEMF_ONE_FILAMENT

    # Slot 3 is Empty + non-RFID and is the sole 3MF match → 3mf_depleted.
    entity_3 = app._tray_entity_by_slot.get(3)
    app._state_map[entity_3] = "Empty"
    app._state_map[f"{entity_3}::tag_uid"] = "0000000000000000"
    app._state_map[f"{entity_3}::color"] = "#161616FF"
    app._state_map[f"{entity_3}::type"] = "PLA"

    # Slot 4 is loaded + non-RFID, no 3MF match → it is NOT empty, so the
    # runout split finds no second depleted slot and leaves the match intact.
    entity_4 = app._tray_entity_by_slot.get(4)
    app._state_map[entity_4] = "loaded"
    app._state_map[f"{entity_4}::tag_uid"] = "0000000000000000"

    app._do_finish("finish")

    assert _has_log(app, "3MF_DEPLETED_MULTI_SLOT_DETECTED slot=3")
    guard_logs = [
        (m, lvl) for m, lvl in app._log_calls
        if "3MF_DEPLETED_MULTI_SLOT_DETECTED" in m
    ]
    assert guard_logs[0][1] == "WARNING"
