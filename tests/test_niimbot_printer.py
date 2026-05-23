"""
test_niimbot_printer.py — Unit tests for NiimbotPrinter (Phase 1).

Phase 1: spool_id written directly to helper, no profile lookup.
"""

import sys
import types
from unittest import mock

import pytest

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

from filament_iq.niimbot_printer import NiimbotPrinter, HELPER_ENTITY


SAMPLE_SPOOL = {
    "id": 21,
    "filament": {
        "id": 5,
        "name": "PLA Basic Light Gray",
        "material": "PLA",
        "color_hex": "808080",
        "vendor": {"id": 1, "name": "Bambu Lab"},
    },
}


class TestableNiimbotPrinter(NiimbotPrinter):
    def __init__(self, args=None):
        a = {"spoolman_url": "http://fake:7912", "dry_run": False}
        a.update(args or {})
        super().__init__(None, "test_niimbot", None, a, None, None, None)
        self.spoolman_url = a["spoolman_url"]
        self.dry_run = bool(a.get("dry_run", False))
        self.profiles_client = None
        self._log_calls = []
        self._set_state_calls = []
        self._events_fired = []

    def initialize(self):
        pass

    def log(self, msg, level="INFO"):
        self._log_calls.append((msg, level))

    def listen_event(self, *a, **kw):
        pass

    def set_state(self, entity, **kwargs):
        self._set_state_calls.append({"entity": entity, **kwargs})

    def fire_event(self, event_name, **kwargs):
        self._events_fired.append({"event": event_name, **kwargs})

    def _validate_config(self, *a, **kw):
        pass


def _fire(app, spool_id):
    app._on_print_niimbot_event("filament_iq_print_niimbot_label", {"spool_id": spool_id}, {})


# ── Tests ─────────────────────────────────────────────────────────────

def test_niimbot_writes_spool_id_not_profile_id():
    """Phase 1: helper receives spool_id string, no profile lookup."""
    app = TestableNiimbotPrinter()
    app._fetch_spool = mock.Mock(return_value=SAMPLE_SPOOL)

    _fire(app, 21)

    assert app._fetch_spool.called
    assert len(app._set_state_calls) == 1
    call = app._set_state_calls[0]
    assert call["entity"] == HELPER_ENTITY
    assert call["state"] == "21"

    # Must not have written any integer (old profile_id behavior)
    for c in app._set_state_calls:
        assert not isinstance(c.get("state"), int), (
            f"set_state wrote an integer — old profile_id behavior: {c}"
        )

    result_events = [e for e in app._events_fired if e["event"] == "filament_iq_niimbot_label_result"]
    assert len(result_events) == 1
    assert result_events[0]["success"] is True


def test_niimbot_skips_invalid_spool_id():
    """spool_id=0 must not touch the helper."""
    app = TestableNiimbotPrinter()
    app._fetch_spool = mock.Mock()

    _fire(app, 0)

    assert not app._set_state_calls, "set_state must not be called for spool_id=0"
    result_events = [e for e in app._events_fired if e["event"] == "filament_iq_niimbot_label_result"]
    assert len(result_events) == 1
    assert result_events[0]["success"] is False


def test_niimbot_fetch_failure_fires_result():
    """When _fetch_spool returns None, result event is fired with success=False."""
    app = TestableNiimbotPrinter()
    app._fetch_spool = mock.Mock(return_value=None)

    _fire(app, 21)

    assert not app._set_state_calls, "set_state must not be called when spool fetch fails"
    result_events = [e for e in app._events_fired if e["event"] == "filament_iq_niimbot_label_result"]
    assert len(result_events) == 1
    assert result_events[0]["success"] is False


def test_niimbot_dry_run_does_not_call_set_state():
    """dry_run=True must log but not call set_state."""
    app = TestableNiimbotPrinter(args={"dry_run": True})
    app._fetch_spool = mock.Mock(return_value=SAMPLE_SPOOL)

    _fire(app, 21)

    assert not app._set_state_calls, "set_state must not be called in dry_run mode"
    assert any("DRY_RUN" in msg for msg, _ in app._log_calls)
    result_events = [e for e in app._events_fired if e["event"] == "filament_iq_niimbot_label_result"]
    assert result_events[0]["success"] is True
