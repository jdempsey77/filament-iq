#!/usr/bin/env python3
"""
test_runout_tracker.py — Unit tests for RunoutTracker.

Tests mid-print runout detection, boolean state management, and startup priming.
"""

import os
import sys
import types

import pytest

# Bootstrap fake hassapi before importing module (no appdaemon dep)
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

from filament_iq.base import build_slot_mappings
from filament_iq.runout_tracker import RunoutTracker

_TEST_PREFIX = "p1s_01p00c5a3101668"
_DEFAULT_ARGS = {
    "printer_serial": "01p00c5a3101668",
    "printer_model": "p1s",
}

_AMS_UNITS_WITH_EXTERNAL = [
    {"type": "ams_2_pro", "ams_index": 0, "slots": [1, 2, 3, 4]},
    {"type": "ams_ht", "ams_index": 128, "slots": [5]},
    {"type": "ams_ht", "ams_index": 129, "slots": [6]},
    {"type": "ams_ht", "ams_index": 130, "slots": [7]},
    {"type": "external", "slots": [8]},
]


def _tray_entity(slot, ams_units=None):
    tray_entity_by_slot, _, _, _ = build_slot_mappings(_TEST_PREFIX, ams_units)
    return tray_entity_by_slot[slot]


class _TestableRunoutTracker(RunoutTracker):
    """RunoutTracker with injected state map and captured side effects."""

    def __init__(self, state_map=None, args=None):
        a = dict(_DEFAULT_ARGS)
        a.update(args or {})
        super().__init__(None, "test_runout", None, a, None, None, None)
        self._state_map = dict(state_map or {})
        self._log_calls = []
        self._service_calls = []
        self._run_in_calls = []

        ams_units = a.get("ams_units")
        (
            self._tray_entity_by_slot,
            _,
            _,
            _,
        ) = build_slot_mappings(_TEST_PREFIX, ams_units)

        self._print_status_entity = f"sensor.{_TEST_PREFIX}_print_status"
        self._in_print = False
        self._all_slots = sorted(self._tray_entity_by_slot.keys())

        for slot in self._all_slots:
            self._state_map.setdefault(f"input_boolean.ams_slot_{slot}_ran_out", "off")

    def initialize(self):
        pass

    def log(self, msg, level="INFO"):
        self._log_calls.append((msg, level))

    def call_service(self, service, **kwargs):
        self._service_calls.append({"service": service, **kwargs})

    def listen_state(self, *a, **kw):
        pass

    def run_in(self, callback, delay, **kw):
        self._run_in_calls.append({"callback": callback, "delay": delay, **kw})
        return f"timer_{len(self._run_in_calls)}"

    def get_state(self, entity_id, attribute=None):
        if attribute:
            key = f"{entity_id}::{attribute}"
            if key in self._state_map:
                return self._state_map[key]
        return self._state_map.get(entity_id)

    def set_state(self, entity_id, state, attributes=None):
        self._state_map[entity_id] = state


def _has_log(app, substring):
    return any(substring in msg for msg, _ in app._log_calls)


def _turned_on(app):
    return {c["entity_id"] for c in app._service_calls if c["service"] == "input_boolean/turn_on"}


def _turned_off(app):
    return {c["entity_id"] for c in app._service_calls if c["service"] == "input_boolean/turn_off"}


def _make_tray_new(empty=True):
    return {"state": "Empty" if empty else "PLA", "attributes": {"empty": empty}}


# ── Test 1: runout sets boolean while printing ────────────────────────

def test_runout_sets_boolean():
    app = _TestableRunoutTracker()
    app._in_print = True
    entity = _tray_entity(1)
    app._on_tray_state_change(entity, "all", _make_tray_new(False), _make_tray_new(True), {})
    assert "input_boolean.ams_slot_1_ran_out" in _turned_on(app)
    assert _has_log(app, "RUNOUT_DETECTED slot=1")


# ── Test 2: no runout outside print ──────────────────────────────────

def test_no_runout_outside_print():
    app = _TestableRunoutTracker()
    app._in_print = False
    entity = _tray_entity(1)
    app._on_tray_state_change(entity, "all", _make_tray_new(False), _make_tray_new(True), {})
    assert not _turned_on(app)
    assert not _turned_off(app)


# ── Test 3: clear on finish ───────────────────────────────────────────

def test_clear_on_finish():
    app = _TestableRunoutTracker()
    app._in_print = True
    app._on_print_status_change(app._print_status_entity, None, "running", "finish", {})
    assert app._in_print is False
    assert _has_log(app, "RUNOUT_CLEARED reason=finish")
    for slot in app._all_slots:
        assert f"input_boolean.ams_slot_{slot}_ran_out" in _turned_off(app)


# ── Test 4: clear on cancelled / canceled ────────────────────────────

@pytest.mark.parametrize("status", ["cancelled", "canceled"])
def test_clear_on_cancelled(status):
    app = _TestableRunoutTracker()
    app._in_print = True
    app._on_print_status_change(app._print_status_entity, None, "running", status, {})
    assert app._in_print is False
    assert _has_log(app, f"RUNOUT_CLEARED reason={status}")
    for slot in app._all_slots:
        assert f"input_boolean.ams_slot_{slot}_ran_out" in _turned_off(app)


# ── Test 5: clear on failed and error ────────────────────────────────

@pytest.mark.parametrize("status", ["failed", "error"])
def test_clear_on_failed_error(status):
    app = _TestableRunoutTracker()
    app._in_print = True
    app._on_print_status_change(app._print_status_entity, None, "running", status, {})
    assert app._in_print is False
    assert _has_log(app, f"RUNOUT_CLEARED reason={status}")
    for slot in app._all_slots:
        assert f"input_boolean.ams_slot_{slot}_ran_out" in _turned_off(app)


# ── Test 6: blip does not clear ──────────────────────────────────────

def test_blip_does_not_clear():
    app = _TestableRunoutTracker()
    app._in_print = True
    # Simulate blip: running → idle → running
    app._on_print_status_change(app._print_status_entity, None, "running", "idle", {})
    # idle is not a terminal state, so no clear, _in_print stays True
    assert app._in_print is True
    assert not _turned_off(app)
    # Back to running: also not a terminal, and old is not running so sets _in_print=True again
    app._on_print_status_change(app._print_status_entity, None, "idle", "running", {})
    assert app._in_print is True
    assert not _turned_off(app)


# ── Test 7: startup prime (mid-print, slot 3 already empty) ──────────

def test_startup_mid_print_prime():
    slot3_entity = _tray_entity(3, _AMS_UNITS_WITH_EXTERNAL)
    state_map = {
        f"sensor.{_TEST_PREFIX}_print_status": "running",
        f"{slot3_entity}::all": {"state": "Empty", "attributes": {"empty": True}},
    }
    app = _TestableRunoutTracker(
        state_map=state_map,
        args={"ams_units": _AMS_UNITS_WITH_EXTERNAL},
    )
    app._startup_init({})

    assert app._in_print is True
    on = _turned_on(app)
    assert "input_boolean.ams_slot_3_ran_out" in on
    for slot in [1, 2, 4, 5, 6, 7, 8]:
        assert f"input_boolean.ams_slot_{slot}_ran_out" not in on
    assert _has_log(app, "STARTUP_PRIME slot=3")


# ── Test 8: startup not-in-print clears all ──────────────────────────

def test_startup_not_in_print_clears():
    state_map = {
        f"sensor.{_TEST_PREFIX}_print_status": "idle",
        "input_boolean.ams_slot_1_ran_out": "on",
        "input_boolean.ams_slot_2_ran_out": "on",
    }
    app = _TestableRunoutTracker(state_map=state_map)
    app._startup_init({})

    assert app._in_print is False
    assert _has_log(app, "STARTUP_CLEARED")
    off = _turned_off(app)
    for slot in app._all_slots:
        assert f"input_boolean.ams_slot_{slot}_ran_out" in off


# ── Test 9: multiple runouts same print ──────────────────────────────

def test_multiple_runouts_same_print():
    app = _TestableRunoutTracker(args={"ams_units": _AMS_UNITS_WITH_EXTERNAL})
    app._in_print = True

    entity2 = _tray_entity(2, _AMS_UNITS_WITH_EXTERNAL)
    entity5 = _tray_entity(5, _AMS_UNITS_WITH_EXTERNAL)

    app._on_tray_state_change(entity2, "all", _make_tray_new(False), _make_tray_new(True), {})
    app._on_tray_state_change(entity5, "all", _make_tray_new(False), _make_tray_new(True), {})

    on = _turned_on(app)
    assert "input_boolean.ams_slot_2_ran_out" in on
    assert "input_boolean.ams_slot_5_ran_out" in on
    for slot in [1, 3, 4, 6, 7, 8]:
        assert f"input_boolean.ams_slot_{slot}_ran_out" not in on


# ── Test 10: external slot runout ────────────────────────────────────

def test_external_slot_runout():
    app = _TestableRunoutTracker(args={"ams_units": _AMS_UNITS_WITH_EXTERNAL})
    app._in_print = True

    external_entity = _tray_entity(8, _AMS_UNITS_WITH_EXTERNAL)
    app._on_tray_state_change(
        external_entity, "all", _make_tray_new(False), _make_tray_new(True), {}
    )

    on = _turned_on(app)
    assert "input_boolean.ams_slot_8_ran_out" in on
    assert _has_log(app, "RUNOUT_DETECTED slot=8")
