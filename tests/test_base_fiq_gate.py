#!/usr/bin/env python3
"""
Tests for FilamentIQBase.fiq_enabled() / fiq_notify() / dry_run — the master
pause switch gate.
Run: python -m pytest tests/test_base_fiq_gate.py -v
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

from filament_iq.base import FilamentIQBase


# ── test harness ──────────────────────────────────────────────────────

class _TestableBase(FilamentIQBase):
    """FilamentIQBase with mocked state/service I/O."""

    def __init__(self, args=None, state_map=None):
        super().__init__(None, "test_base", None, args or {}, None, None, None)
        self._log_calls = []
        self._service_calls = []
        self._state_map = state_map or {}

    def log(self, msg, level="INFO"):
        self._log_calls.append((level, msg))

    def get_state(self, entity_id, attribute=None):
        return self._state_map.get(entity_id, "")

    def call_service(self, service, **kwargs):
        self._service_calls.append({"service": service, **kwargs})


class _RaisingGetStateBase(_TestableBase):
    def get_state(self, entity_id, attribute=None):
        raise RuntimeError("simulated HA connection error")


# ── fiq_enabled() ────────────────────────────────────────────────────

class TestFiqEnabled:

    def test_on_state_is_enabled(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "on"})
        assert app.fiq_enabled() is True

    def test_off_state_is_disabled(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "off"})
        assert app.fiq_enabled() is False

    def test_missing_entity_fails_open(self):
        """Entity absent from state -> treated as enabled (fail open)."""
        app = _TestableBase()
        assert app.fiq_enabled() is True

    def test_unknown_state_fails_open(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "unknown"})
        assert app.fiq_enabled() is True

    def test_unavailable_state_fails_open(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "unavailable"})
        assert app.fiq_enabled() is True

    def test_get_state_exception_fails_open(self):
        app = _RaisingGetStateBase()
        assert app.fiq_enabled() is True

    def test_fail_open_warns_exactly_once(self):
        """A typo'd/missing entity logs the fail-open warning once per instance, not per call."""
        app = _TestableBase()
        for _ in range(5):
            app.fiq_enabled()
        warnings = [m for lvl, m in app._log_calls if lvl == "WARNING"]
        assert len(warnings) == 1

    def test_enabled_entity_override(self):
        app = _TestableBase(
            args={"enabled_entity": "input_boolean.custom_switch"},
            state_map={"input_boolean.custom_switch": "off"},
        )
        assert app.fiq_enabled() is False


# ── fiq_notify() ─────────────────────────────────────────────────────

class TestFiqNotify:

    def test_enabled_creates_persistent_notification(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "on"})
        app.fiq_notify("Title", "Message", notification_id="nid_1")
        assert len(app._service_calls) == 1
        call = app._service_calls[0]
        assert call["service"] == "persistent_notification/create"
        assert call["title"] == "Title"
        assert call["message"] == "Message"
        assert call["notification_id"] == "nid_1"

    def test_paused_suppresses_persistent_notification(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "off"})
        app.fiq_notify("Title", "Message", notification_id="nid_1")
        assert app._service_calls == []
        assert any("FIQ_PAUSED" in m for _, m in app._log_calls)

    def test_paused_suppresses_push(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "off"})
        app.notify_service = "mobile_app_jerry"
        app.fiq_notify("Title", "Message", push=True)
        assert app._service_calls == []

    def test_push_only_no_notification_id_no_persistent_notification(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "on"})
        app.fiq_notify("Title", "Message", push=True, push_service="mobile_app_jerry")
        assert len(app._service_calls) == 1
        call = app._service_calls[0]
        assert call["service"] == "notify/mobile_app_jerry"

    def test_persistent_and_push_both_sent(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "on"})
        app.fiq_notify(
            "Title", "Message",
            notification_id="nid_1", push=True, push_service="mobile_app_jerry",
        )
        services = [c["service"] for c in app._service_calls]
        assert "persistent_notification/create" in services
        assert "notify/mobile_app_jerry" in services

    def test_push_falls_back_to_notify_service_attr(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "on"})
        app.notify_service = "mobile_app_default"
        app.fiq_notify("Title", "Message", push=True)
        assert app._service_calls[0]["service"] == "notify/mobile_app_default"

    def test_missing_entity_fails_open_and_notifies(self):
        app = _TestableBase()
        app.fiq_notify("Title", "Message", notification_id="nid_1")
        assert len(app._service_calls) == 1


# ── dry_run property ─────────────────────────────────────────────────

class TestDryRunProperty:

    def test_default_false_when_enabled(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "on"})
        assert app.dry_run is False

    def test_explicit_dry_run_true(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "on"})
        app.dry_run = True
        assert app.dry_run is True

    def test_paused_forces_dry_run_true(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "off"})
        assert app.dry_run is True

    def test_explicit_dry_run_survives_pause(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "off"})
        app.dry_run = True
        assert app.dry_run is True

    def test_setter_stores_underlying_cfg_flag(self):
        """Legacy assignment style (self.dry_run = x) must keep working, e.g. for
        older test harnesses that set it directly rather than via _dry_run_cfg."""
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "on"})
        app.dry_run = True
        assert app._dry_run_cfg is True
