#!/usr/bin/env python3
"""
Tests for FilamentIQBase.fiq_enabled() / fiq_notify() / dry_run — the master
pause switch gate.
Run: python -m pytest tests/test_base_fiq_gate.py -v
"""

import inspect
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

    def test_jerry_mobile_push_goes_through_notify_jerry_helper(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "on"})
        app.fiq_notify("Print Complete", "msg", push=True, push_service="jerry_mobile",
                       tag="fiq-print-result")
        call = app._service_calls[0]
        assert call["service"] == "script/notify_jerry"
        assert call["tag"] == "fiq-print-result"
        assert call["group"] == "filament-iq"
        assert call["url"] == "/lovelace-stage/printer"
        assert call["level"] == "active"
        assert call["recipients"] == "jerry"

    def test_household_service_keeps_household_recipients(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "on"})
        app.fiq_notify("T", "m", push=True, push_service="household_mobile")
        assert app._service_calls[0]["recipients"] == "household"

    def test_unknown_service_keeps_legacy_direct_call_recipient_unchanged(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "on"})
        app.fiq_notify("T", "m", push=True, push_service="mobile_app_YOUR_DEVICE")
        assert app._service_calls[0]["service"] == "notify/mobile_app_YOUR_DEVICE"

    def test_helper_push_paused_sends_nothing(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "off"})
        app.fiq_notify("T", "m", push=True, push_service="jerry_mobile")
        assert app._service_calls == []

    def test_never_critical(self):
        app = _TestableBase(state_map={"input_boolean.filament_iq_enabled": "on"})
        app.fiq_notify("T", "m", push=True, push_service="jerry_mobile")
        assert "critical" not in repr(app._service_calls[0]).lower()

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


# ── registry harnesses ──────────────────────────────────────────────

class _TestableRegistryApp(FilamentIQBase):
    """FilamentIQBase with mocked I/O and a real on-disk registry (tmp_path)."""

    def __init__(self, args=None, state_map=None, data_dir=None, name="test_registry_app"):
        a = dict(args or {})
        if data_dir is not None:
            a["data_dir"] = data_dir
        super().__init__(None, name, None, a, None, None, None)
        self.name = name
        self._log_calls = []
        self._service_calls = []
        self._state_map = state_map or {}
        self._listen_state_calls = []

    def log(self, msg, level="INFO"):
        self._log_calls.append((level, msg))

    def get_state(self, entity_id, attribute=None):
        return self._state_map.get(entity_id, "")

    def call_service(self, service, **kwargs):
        self._service_calls.append({"service": service, **kwargs})

    def listen_state(self, callback, entity_id, **kwargs):
        self._listen_state_calls.append({"callback": callback, "entity_id": entity_id, **kwargs})


class _TestableAppWithInit(_TestableRegistryApp):
    """Defines its own initialize(), so __init_subclass__ wraps it with the
    bootstrap hook exactly like a real concrete app (AmsRfidGuard, etc.)."""

    def __init__(self, args=None, state_map=None, data_dir=None, name="test_registry_app"):
        super().__init__(args=args, state_map=state_map, data_dir=data_dir, name=name)
        self.initialize_called = False

    def initialize(self):
        self.initialize_called = True


# ── notification registry ───────────────────────────────────────────

class TestNotificationRegistry:

    def test_suppressed_notification_not_registered(self, tmp_path):
        app = _TestableRegistryApp(
            state_map={"input_boolean.filament_iq_enabled": "off"},
            data_dir=str(tmp_path),
        )
        app.fiq_notify("Title", "Msg", notification_id="nid_1")
        app._fiq_ensure_registry_loaded()
        assert app._fiq_registry == []
        assert app._service_calls == []

    def test_created_notification_is_registered(self, tmp_path):
        app = _TestableRegistryApp(
            state_map={"input_boolean.filament_iq_enabled": "on"},
            data_dir=str(tmp_path),
        )
        app.fiq_notify("Title", "Msg", notification_id="nid_1")
        assert "nid_1" in app._fiq_registry

    def test_fiq_dismiss_removes_from_registry_and_deletes(self, tmp_path):
        app = _TestableRegistryApp(
            state_map={"input_boolean.filament_iq_enabled": "on"},
            data_dir=str(tmp_path),
        )
        app.fiq_notify("Title", "Msg", notification_id="nid_1")
        app.fiq_dismiss("nid_1")
        assert "nid_1" not in app._fiq_registry
        deletes = [c for c in app._service_calls if c["service"] == "persistent_notification/dismiss"]
        assert any(c["notification_id"] == "nid_1" for c in deletes)

    def test_fiq_dismiss_all_deletes_exactly_registered_ids(self, tmp_path):
        app = _TestableRegistryApp(
            state_map={"input_boolean.filament_iq_enabled": "on"},
            data_dir=str(tmp_path),
        )
        app.fiq_notify("T1", "M1", notification_id="nid_1")
        app.fiq_notify("T2", "M2", notification_id="nid_2")
        app.fiq_dismiss_all()
        deleted_ids = {
            c["notification_id"] for c in app._service_calls
            if c["service"] == "persistent_notification/dismiss"
        }
        assert deleted_ids == {"nid_1", "nid_2"}
        assert app._fiq_registry == []

    def test_registry_survives_simulated_restart(self, tmp_path):
        data_dir = str(tmp_path)
        app1 = _TestableRegistryApp(
            state_map={"input_boolean.filament_iq_enabled": "on"}, data_dir=data_dir,
        )
        app1.fiq_notify("T", "M", notification_id="nid_restart")

        # Simulate restart: brand-new instance, same name/data_dir, nothing
        # carried over in memory.
        app2 = _TestableRegistryApp(
            state_map={"input_boolean.filament_iq_enabled": "on"}, data_dir=data_dir,
        )
        app2._fiq_load_registry()
        assert "nid_restart" in app2._fiq_registry

    def test_create_restart_then_pause_still_clears(self, tmp_path):
        """Proves on-disk persistence, not in-memory state: create a
        notification, simulate a restart (new instance), then pause — it
        still clears."""
        data_dir = str(tmp_path)
        app1 = _TestableRegistryApp(
            state_map={"input_boolean.filament_iq_enabled": "on"}, data_dir=data_dir,
        )
        app1.fiq_notify("T", "M", notification_id="nid_persist")

        app2 = _TestableRegistryApp(
            state_map={"input_boolean.filament_iq_enabled": "on"}, data_dir=data_dir,
        )
        app2._fiq_on_pause_state_change(
            "input_boolean.filament_iq_enabled", "state", "on", "off", {}
        )
        deleted_ids = {
            c["notification_id"] for c in app2._service_calls
            if c["service"] == "persistent_notification/dismiss"
        }
        assert "nid_persist" in deleted_ids

    def test_initialize_with_switch_already_off_dismisses_everything(self, tmp_path):
        data_dir = str(tmp_path)
        app1 = _TestableAppWithInit(
            args={}, state_map={"input_boolean.filament_iq_enabled": "on"}, data_dir=data_dir,
        )
        app1.fiq_notify("T", "M", notification_id="nid_startup")

        app2 = _TestableAppWithInit(
            args={}, state_map={"input_boolean.filament_iq_enabled": "off"}, data_dir=data_dir,
        )
        app2.initialize()
        assert app2.initialize_called is True
        deleted_ids = {
            c["notification_id"] for c in app2._service_calls
            if c["service"] == "persistent_notification/dismiss"
        }
        assert "nid_startup" in deleted_ids
        assert app2._fiq_registry == []

    def test_initialize_registers_pause_listener(self, tmp_path):
        app = _TestableAppWithInit(
            args={}, state_map={"input_boolean.filament_iq_enabled": "on"}, data_dir=str(tmp_path),
        )
        app.initialize()
        assert len(app._listen_state_calls) == 1
        assert app._listen_state_calls[0]["entity_id"] == "input_boolean.filament_iq_enabled"

    def test_corrupt_registry_file_logs_warning_and_starts_empty(self, tmp_path):
        app = _TestableRegistryApp(data_dir=str(tmp_path))
        path = app._fiq_registry_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{not valid json")
        app._fiq_load_registry()
        assert app._fiq_registry == []
        assert any("FIQ_REGISTRY_CORRUPT" in m for _, m in app._log_calls)

    def test_corrupt_registry_file_does_not_crash_bootstrap(self, tmp_path):
        data_dir = str(tmp_path)
        app = _TestableAppWithInit(
            args={}, state_map={"input_boolean.filament_iq_enabled": "on"}, data_dir=data_dir,
        )
        path = app._fiq_registry_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("not json at all {{{")
        app.initialize()  # must not raise
        assert app.initialize_called is True
        assert app._fiq_registry == []

    def test_registry_cap_drops_oldest_first(self, tmp_path):
        app = _TestableRegistryApp(
            state_map={"input_boolean.filament_iq_enabled": "on"}, data_dir=str(tmp_path),
        )
        for i in range(205):
            app.fiq_notify("T", "M", notification_id=f"nid_{i}")
        assert len(app._fiq_registry) == 200
        assert "nid_0" not in app._fiq_registry
        assert "nid_4" not in app._fiq_registry
        assert "nid_5" in app._fiq_registry
        assert "nid_204" in app._fiq_registry

    def test_off_transition_handler_idempotent(self, tmp_path):
        app = _TestableRegistryApp(
            state_map={"input_boolean.filament_iq_enabled": "on"}, data_dir=str(tmp_path),
        )
        app.fiq_notify("T", "M", notification_id="nid_1")
        for _ in range(3):
            app._fiq_on_pause_state_change(
                "input_boolean.filament_iq_enabled", "state", "on", "off", {}
            )
        assert app._fiq_registry == []
        deletes_for_nid1 = [
            c for c in app._service_calls
            if c["service"] == "persistent_notification/dismiss" and c["notification_id"] == "nid_1"
        ]
        assert len(deletes_for_nid1) == 1

    def test_on_transition_does_not_dismiss(self, tmp_path):
        app = _TestableRegistryApp(
            state_map={"input_boolean.filament_iq_enabled": "on"}, data_dir=str(tmp_path),
        )
        app._fiq_registry = ["nid_untouched"]
        app._fiq_on_pause_state_change(
            "input_boolean.filament_iq_enabled", "state", "off", "on", {}
        )
        assert app._fiq_registry == ["nid_untouched"]
        assert app._service_calls == []


# ── initialize() wrapper robustness ─────────────────────────────────

class _TestableInitializeRaises(_TestableRegistryApp):
    """Own initialize() always raises, to prove the pause listener still
    registers (it runs inside _fiq_bootstrap, before this is ever called)."""

    def initialize(self):
        raise RuntimeError("subclass initialize blew up")


class _TestableCountingInit(_TestableRegistryApp):
    """Counts how many times its own initialize() body actually runs."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.initialize_call_count = 0

    def initialize(self):
        self.initialize_call_count += 1


class TestInitializeWrapperRobustness:

    def test_orig_initialize_always_called_even_if_bootstrap_blows_up(self, tmp_path):
        """Simulate an exception escaping every internal try/except inside
        _fiq_bootstrap (e.g. a bug in the exception-handling path itself) —
        the subclass's own initialize() must still run."""
        app = _TestableAppWithInit(
            args={}, state_map={"input_boolean.filament_iq_enabled": "on"}, data_dir=str(tmp_path),
        )

        def _boom():
            raise RuntimeError("bootstrap itself is broken")

        app._fiq_bootstrap = _boom
        app.initialize()
        assert app.initialize_called is True

    def test_orig_initialize_always_called_when_log_also_raises(self, tmp_path):
        """Even the fallback log() call in the wrapper's own except-handler
        can raise (e.g. AppDaemon not fully up yet) — still must not block
        the subclass's initialize()."""
        app = _TestableAppWithInit(
            args={}, state_map={"input_boolean.filament_iq_enabled": "on"}, data_dir=str(tmp_path),
        )

        def _boom():
            raise RuntimeError("bootstrap broken")

        def _log_boom(msg, level="INFO"):
            raise RuntimeError("logging broken too")

        app._fiq_bootstrap = _boom
        app.log = _log_boom
        app.initialize()  # must not raise
        assert app.initialize_called is True

    def test_pause_listener_registered_even_if_subclass_initialize_raises(self, tmp_path):
        app = _TestableInitializeRaises(
            args={}, state_map={"input_boolean.filament_iq_enabled": "on"}, data_dir=str(tmp_path),
        )
        with pytest.raises(RuntimeError, match="subclass initialize blew up"):
            app.initialize()
        assert len(app._listen_state_calls) == 1
        assert app._listen_state_calls[0]["entity_id"] == "input_boolean.filament_iq_enabled"

    def test_sentinel_marks_wrapped_initialize(self):
        assert getattr(_TestableAppWithInit.__dict__["initialize"], "_fiq_is_wrapped", False) is True

    def test_wrapped_initialize_signature_is_self_only(self):
        """AppDaemon introspects initialize()'s signature before calling it
        and rejects anything other than exactly (self) — no *args/**kwargs
        passthrough, since AppDaemon always calls it with zero arguments.
        Regression test: an earlier version of the wrapper used
        (self, *a, **kw) and every app failed to start in production with
        AppDaemon's BadInitializeMethod error, even though every local test
        passed (the test harnesses call .initialize() directly and never
        exercise AppDaemon's own signature validation)."""
        sig = inspect.signature(_TestableAppWithInit.__dict__["initialize"])
        params = list(sig.parameters.values())
        assert len(params) == 1
        assert params[0].name == "self"
        assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD

    def test_sentinel_prevents_double_wrap_on_reinit_subclass_hook(self, tmp_path):
        """Simulate __init_subclass__ re-firing on an already-wrapped class
        (e.g. a partial module reload calling the hook again on the same
        method object) — bootstrap must not stack a second call per
        initialize()."""
        # Re-trigger the hook directly against the already-wrapped class.
        FilamentIQBase.__init_subclass__.__func__(_TestableCountingInit)

        app = _TestableCountingInit(
            args={}, state_map={"input_boolean.filament_iq_enabled": "on"}, data_dir=str(tmp_path),
        )
        bootstrap_calls = []
        real_bootstrap = app._fiq_bootstrap

        def _counting_bootstrap():
            bootstrap_calls.append(1)
            real_bootstrap()

        app._fiq_bootstrap = _counting_bootstrap
        app.initialize()
        assert len(bootstrap_calls) == 1
        assert app.initialize_call_count == 1
