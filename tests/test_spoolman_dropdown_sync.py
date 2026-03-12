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

    def test_vendor_missing(self):
        assert _vendor({}) == ""

    def test_material_missing(self):
        assert _material({}) == ""

    def test_name_missing(self):
        assert _name({}) == ""

    def test_label_with_none_id(self):
        f = {"name": "PLA", "material": "PLA"}
        label = _label(f)
        assert label.startswith("?")

    def test_label_all_empty_parts(self):
        f = {"id": 7}
        label = _label(f)
        assert "7" in label

    def test_sort_key_ordering(self):
        f1 = _filament(1, name="A", material="PLA", vendor_name="Z")
        f2 = _filament(2, name="B", material="PLA", vendor_name="A")
        assert _sort_key(f2) < _sort_key(f1)


class TestIdInt:
    """_id_int helper edge cases."""

    def test_valid_int(self):
        from filament_iq.spoolman_dropdown_sync import _id_int
        assert _id_int({"id": 5}) == 5

    def test_none_id(self):
        from filament_iq.spoolman_dropdown_sync import _id_int
        assert _id_int({}) == 0

    def test_invalid_id(self):
        from filament_iq.spoolman_dropdown_sync import _id_int
        assert _id_int({"id": "not_a_number"}) == 0


class TestWaitThenRefresh:
    """_wait_then_refresh retry logic."""

    def test_entity_ready_refreshes_immediately(self):
        """Entity exists on first check → refresh runs."""
        app = _TestableDropdown(filaments=[_filament(1)])
        app._state_map[app.dropdown_entity] = "some_value"
        app._wait_then_refresh({})
        call = _get_set_options_call(app)
        assert call is not None

    def test_entity_not_ready_max_attempts(self):
        """After 10 failed attempts, refresh runs anyway."""
        app = _TestableDropdown(filaments=[_filament(1)])
        # Entity not in state map → returns None
        app._wait_then_refresh({"attempt": 10})
        call = _get_set_options_call(app)
        assert call is not None
        assert _has_log(app, "not ready after 10 attempts")


class TestOnRefreshEvent:
    """_on_refresh_event delegates to _run_refresh."""

    def test_event_triggers_refresh(self):
        app = _TestableDropdown(filaments=[_filament(1)])
        app._on_refresh_event("SPOOLMAN_REFRESH_FILAMENT_DROPDOWN", {}, {})
        call = _get_set_options_call(app)
        assert call is not None
        assert _has_log(app, "refresh requested")


class TestRefreshLockRetry:
    """Locked refresh schedules a retry."""

    def test_locked_schedules_retry(self):
        """If locked and no retry scheduled, sets retry flag."""
        app = _TestableDropdown(filaments=[_filament(1)])
        app._refresh_lock = True
        app._refresh_retry_scheduled = False
        app._run_refresh()
        assert app._refresh_retry_scheduled is True

    def test_locked_already_retrying_no_double(self):
        """If already retrying, doesn't schedule again."""
        app = _TestableDropdown(filaments=[_filament(1)])
        app._refresh_lock = True
        app._refresh_retry_scheduled = True
        app._run_refresh()
        # Still True, no crash
        assert app._refresh_retry_scheduled is True


class TestSetOptionsFailure:
    """set_options service call failure."""

    def test_set_options_error_logged(self):
        """If call_service raises, error logged, no crash."""
        filaments = [_filament(1)]
        app = _TestableDropdown(filaments=filaments)
        _orig = app.call_service
        def _fail(service, **kwargs):
            if service == "input_select/set_options":
                raise RuntimeError("service unavailable")
            return _orig(service, **kwargs)
        app.call_service = _fail
        app._run_refresh()
        assert _has_log(app, "set_options failed")


class TestNotifyErrorException:
    """_notify_error handles call_service failure."""

    def test_notify_error_call_fails(self):
        """If persistent_notification/create fails, warning logged."""
        app = _TestableDropdown()
        def _fail(service, **kwargs):
            raise RuntimeError("HA unreachable")
        app.call_service = _fail
        app._notify_error("test error")
        assert _has_log(app, "could not create notification")


class TestWaitThenRefresh:
    """_wait_then_refresh polling loop."""

    def test_entity_ready_runs_refresh(self):
        """If dropdown entity is available, refresh runs."""
        filaments = [_filament(1)]
        app = _TestableDropdown(filaments=filaments)
        app._state_map[app.dropdown_entity] = "-- Select filament --"
        app._wait_then_refresh()
        call = _get_set_options_call(app)
        assert call is not None

    def test_entity_not_ready_retries(self):
        """If entity is None, schedules retry."""
        app = _TestableDropdown()
        app._run_in_calls = []
        def track_run_in(cb, delay, **kw):
            app._run_in_calls.append({"callback": cb, "delay": delay, **kw})
        app.run_in = track_run_in
        app._wait_then_refresh({"attempt": 0})
        assert len(app._run_in_calls) == 1
        assert app._run_in_calls[0]["attempt"] == 1

    def test_max_attempts_forces_refresh(self):
        """After 10 attempts, runs refresh anyway."""
        filaments = [_filament(1)]
        app = _TestableDropdown(filaments=filaments)
        app._wait_then_refresh({"attempt": 10})
        assert _has_log(app, "entity not ready after 10 attempts")
        call = _get_set_options_call(app)
        assert call is not None


class TestOnRefreshEvent:
    """_on_refresh_event triggers refresh."""

    def test_event_triggers_refresh(self):
        filaments = [_filament(1)]
        app = _TestableDropdown(filaments=filaments)
        app._on_refresh_event("FILAMENT_IQ_REFRESH_DROPDOWN", {}, {})
        assert _has_log(app, "refresh requested")
        call = _get_set_options_call(app)
        assert call is not None


class TestFetchFilamentsReal:
    """_fetch_filaments with mocked urllib."""

    def test_http_error(self):
        from unittest import mock
        import urllib.error
        app = _TestableDropdown()
        # Use a real SpoolmanDropdownSync._fetch_filaments (not the override)
        app._fetch_filaments = SpoolmanDropdownSync._fetch_filaments.__get__(app)
        with mock.patch("urllib.request.urlopen") as m:
            m.side_effect = urllib.error.HTTPError(
                "http://fake:7912/api/v1/filament", 500, "Internal Server Error", {}, None
            )
            with pytest.raises(RuntimeError, match="HTTP 500"):
                app._fetch_filaments()

    def test_url_error(self):
        from unittest import mock
        import urllib.error
        app = _TestableDropdown()
        app._fetch_filaments = SpoolmanDropdownSync._fetch_filaments.__get__(app)
        with mock.patch("urllib.request.urlopen") as m:
            m.side_effect = urllib.error.URLError("Connection refused")
            with pytest.raises(RuntimeError, match="URL error"):
                app._fetch_filaments()

    def test_invalid_json(self):
        from unittest import mock
        app = _TestableDropdown()
        app._fetch_filaments = SpoolmanDropdownSync._fetch_filaments.__get__(app)
        resp_mock = mock.MagicMock()
        resp_mock.read.return_value = b"not json"
        cm = mock.MagicMock()
        cm.__enter__ = mock.MagicMock(return_value=resp_mock)
        cm.__exit__ = mock.MagicMock(return_value=False)
        with mock.patch("urllib.request.urlopen", return_value=cm):
            with pytest.raises(RuntimeError, match="invalid JSON"):
                app._fetch_filaments()

    def test_unexpected_type(self):
        from unittest import mock
        app = _TestableDropdown()
        app._fetch_filaments = SpoolmanDropdownSync._fetch_filaments.__get__(app)
        resp_mock = mock.MagicMock()
        resp_mock.read.return_value = b'{"not": "a list"}'
        cm = mock.MagicMock()
        cm.__enter__ = mock.MagicMock(return_value=resp_mock)
        cm.__exit__ = mock.MagicMock(return_value=False)
        with mock.patch("urllib.request.urlopen", return_value=cm):
            with pytest.raises(RuntimeError, match="unexpected response type"):
                app._fetch_filaments()

    def test_success(self):
        from unittest import mock
        import json
        app = _TestableDropdown()
        app._fetch_filaments = SpoolmanDropdownSync._fetch_filaments.__get__(app)
        data = [{"id": 1, "name": "PLA", "material": "PLA", "vendor": {"name": "Bambu"}}]
        resp_mock = mock.MagicMock()
        resp_mock.read.return_value = json.dumps(data).encode()
        cm = mock.MagicMock()
        cm.__enter__ = mock.MagicMock(return_value=resp_mock)
        cm.__exit__ = mock.MagicMock(return_value=False)
        with mock.patch("urllib.request.urlopen", return_value=cm):
            result = app._fetch_filaments()
        assert len(result) == 1
        assert result[0]["id"] == 1


class TestRunRefreshFetchError:
    """_run_refresh when fetch raises."""

    def test_fetch_error_logged(self):
        app = _TestableDropdown(fetch_error=RuntimeError("connection refused"))
        app._run_refresh()
        assert _has_log(app, "fetch failed")
        assert app._refresh_lock is False


class TestSkipBadFilament:
    """_run_refresh skips filaments that raise in _label."""

    def test_bad_filament_skipped(self):
        filaments = [
            _filament(1, name="Good PLA"),
            {"id": 2},  # missing vendor/material → _label might fail
        ]
        app = _TestableDropdown(filaments=filaments)
        app._run_refresh()
        call = _get_set_options_call(app)
        assert call is not None
