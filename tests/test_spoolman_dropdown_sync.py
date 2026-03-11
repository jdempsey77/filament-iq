#!/usr/bin/env python3
"""
Tests for spoolman_dropdown_sync — filament dropdown population.
Run: python -m pytest tests/test_spoolman_dropdown_sync.py -v
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

from filament_iq.spoolman_dropdown_sync import (
    SpoolmanDropdownSync,
    PLACEHOLDER,
    _label,
    _sort_key,
    _vendor,
    _material,
    _name,
)


# ── test harness ──────────────────────────────────────────────────────

_DEFAULT_TEST_ARGS = {
    "printer_serial": "01p00c5a3101668",
    "printer_model": "p1s",
    "spoolman_url": "http://192.0.2.1:7912",
    "enabled": True,
    "dropdown_entity": "input_select.spoolman_new_spool_filament",
}


class _TestableDropdown(SpoolmanDropdownSync):
    """SpoolmanDropdownSync with mocked I/O."""

    def __init__(self, args=None, filaments=None, fetch_error=None):
        a = dict(_DEFAULT_TEST_ARGS)
        a.update(args or {})
        super().__init__(None, "test_dropdown", None, a, None, None, None)
        self._mock_filaments = filaments or []
        self._fetch_error = fetch_error
        self._log_calls = []
        self._service_calls = []
        self._state_map = {}

        # Initialize state (normally done in initialize())
        self.enabled = bool(a.get("enabled", True))
        self.spoolman_base_url = str(a.get("spoolman_url", "")).rstrip("/")
        self.filament_url = f"{self.spoolman_base_url}/api/v1/filament"
        self.dropdown_entity = str(a.get("dropdown_entity", "")).strip()
        self._refresh_lock = False
        self._refresh_retry_scheduled = False

    def initialize(self):
        pass

    def log(self, msg, level="INFO"):
        self._log_calls.append((msg, level))

    def call_service(self, service, **kwargs):
        self._service_calls.append({"service": service, **kwargs})

    def listen_event(self, *a, **kw):
        pass

    def run_in(self, callback, delay, **kw):
        pass

    def get_state(self, entity_id, attribute=None):
        return self._state_map.get(entity_id)

    def _fetch_filaments(self):
        """Mock: return configured filaments or raise."""
        if self._fetch_error:
            raise self._fetch_error
        return self._mock_filaments


def _has_log(app, substring):
    return any(substring in msg for msg, _ in app._log_calls)


def _filament(fid, name="PLA Basic", material="PLA", vendor_name="Bambu Lab"):
    """Build a minimal Spoolman filament dict."""
    return {
        "id": fid,
        "name": name,
        "material": material,
        "vendor": {"name": vendor_name},
    }


def _get_set_options_call(app):
    """Extract the set_options service call, or None."""
    for c in app._service_calls:
        if c["service"] == "input_select/set_options":
            return c
    return None


# ── R2 #3: Dropdown Sync tests ───────────────────────────────────────

class TestDropdownSyncsOnRefresh:
    """Dropdown populated correctly when refresh runs."""

    def test_single_filament_synced(self):
        """One filament → dropdown has placeholder + 1 option."""
        filaments = [_filament(1, name="PLA Basic", material="PLA")]
        app = _TestableDropdown(filaments=filaments)
        app._run_refresh()

        call = _get_set_options_call(app)
        assert call is not None
        options = call["options"]
        assert options[0] == PLACEHOLDER
        assert len(options) == 2
        assert "1 - " in options[1]

    def test_multiple_filaments_sorted(self):
        """Multiple filaments → sorted by vendor/material/name."""
        filaments = [
            _filament(2, name="PETG", material="PETG", vendor_name="Overture"),
            _filament(1, name="PLA Basic", material="PLA", vendor_name="Bambu Lab"),
        ]
        app = _TestableDropdown(filaments=filaments)
        app._run_refresh()

        call = _get_set_options_call(app)
        options = call["options"]
        assert len(options) == 3
        # Bambu Lab sorts before Overture
        assert "Bambu Lab" in options[1]
        assert "Overture" in options[2]


class TestDropdownAddsAndRemoves:
    """Dropdown reflects current Spoolman state on each refresh."""

    def test_new_filament_appears(self):
        """Adding a filament to Spoolman → it appears in dropdown."""
        app = _TestableDropdown(filaments=[_filament(1)])
        app._run_refresh()
        call1 = _get_set_options_call(app)
        assert len(call1["options"]) == 2

        # Add second filament and refresh
        app._mock_filaments = [_filament(1), _filament(2, name="PETG")]
        app._service_calls.clear()
        app._run_refresh()
        call2 = _get_set_options_call(app)
        assert len(call2["options"]) == 3

    def test_removed_filament_disappears(self):
        """Removing a filament from Spoolman → it disappears from dropdown."""
        app = _TestableDropdown(filaments=[_filament(1), _filament(2)])
        app._run_refresh()
        call1 = _get_set_options_call(app)
        assert len(call1["options"]) == 3

        # Remove one filament
        app._mock_filaments = [_filament(1)]
        app._service_calls.clear()
        app._run_refresh()
        call2 = _get_set_options_call(app)
        assert len(call2["options"]) == 2


class TestDropdownSpoolmanUnavailable:
    """Dropdown handles Spoolman failure gracefully."""

    def test_fetch_error_no_crash(self):
        """Spoolman unreachable → error logged, no set_options call, no crash."""
        app = _TestableDropdown(fetch_error=RuntimeError("Connection refused"))
        app._run_refresh()
        assert _has_log(app, "fetch failed")
        assert _get_set_options_call(app) is None

    def test_fetch_error_sends_notification(self):
        """Spoolman error → persistent_notification created."""
        app = _TestableDropdown(fetch_error=RuntimeError("Connection refused"))
        app._run_refresh()
        notif = [c for c in app._service_calls
                 if c["service"] == "persistent_notification/create"]
        assert len(notif) == 1


class TestDropdownRefreshLock:
    """Concurrent refresh is blocked by lock."""

    def test_locked_refresh_dropped(self):
        """If refresh already running, second call is dropped."""
        app = _TestableDropdown(filaments=[_filament(1)])
        app._refresh_lock = True
        app._run_refresh()
        assert _get_set_options_call(app) is None
        assert _has_log(app, "already running")


class TestLabelHelpers:
    """_label, _vendor, _material, _name helper functions."""

    def test_label_with_vendor_material_name(self):
        f = _filament(5, name="Matte PLA", material="PLA", vendor_name="Overture")
        assert _label(f) == "5 - Overture – PLA – Matte PLA"

    def test_label_missing_vendor(self):
        f = {"id": 3, "name": "Basic", "material": "PLA"}
        assert _label(f) == "3 - PLA – Basic"

    def test_vendor_from_dict(self):
        assert _vendor({"vendor": {"name": "Bambu Lab"}}) == "Bambu Lab"

    def test_vendor_from_string(self):
        assert _vendor({"vendor_name": "Overture"}) == "Overture"
