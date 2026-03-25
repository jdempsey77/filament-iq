#!/usr/bin/env python3
"""
test_ams_print_usage_sync.py — Integration tests for AmsPrintUsageSync lifecycle.

Tests AppDaemon-integrated behavior: print start/end state capture, fuel gauge
reading, 3MF fetch scheduling, active_print.json three-write lifecycle, RFID
reconciler, dedup persistence, and _collect_print_inputs().

Does NOT test:
  - Decision logic → see test_consumption_engine.py
  - Spoolman write execution → see test_spoolman_writes.py
  - Notification content → see test_spoolman_writes.py
"""

import json
import os
import pathlib
import sys
import tempfile
import threading
import types
from unittest import mock

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

from collections import OrderedDict

from filament_iq.ams_print_usage_sync import AmsPrintUsageSync
from filament_iq.base import build_slot_mappings


# ── test harness ──────────────────────────────────────────────────────

# Default test config: no hardcoded IPs/serials; use placeholder values
_DEFAULT_TEST_ARGS = {
    "printer_serial": "01p00c5a3101668",
    "printer_model": "p1s",
    "spoolman_url": "http://192.0.2.1:7912",
}


class _TestableUsageSync(AmsPrintUsageSync):
    """AmsPrintUsageSync with injected state map and captured side effects."""

    def __init__(self, state_map=None, args=None):
        a = dict(_DEFAULT_TEST_ARGS)
        a.update(args or {})
        super().__init__(None, "test_usage", None, a, None, None, None)
        self._state_map = state_map or {}
        self._log_calls = []
        self._use_calls = []
        self._service_calls = []
        self._use_fail_spool_ids = set()
        self._use_remaining_override = {}  # {spool_id: remaining_weight} for mock responses
        self._run_in_calls = []
        self._cancelled_timers = []

        # Build slot mappings from config (no hardcoded entities)
        prefix = self._build_entity_prefix()
        ams_units = a.get("ams_units")
        (
            self._tray_entity_by_slot,
            self._slot_by_tray_entity,
            self._ams_tray_to_slot,
            _,
        ) = build_slot_mappings(prefix, ams_units)
        self._active_tray_entity = f"sensor.{prefix}_active_tray"
        self._print_status_entity = f"sensor.{prefix}_print_status"
        self._task_name_entity = f"sensor.{prefix}_task_name"
        self._print_weight_entity = f"sensor.{prefix}_print_weight"
        self._trays_used_entity = str(
            a.get("trays_used_entity", "input_text.filament_iq_trays_used_this_print")
        ).strip()

        self.enabled = bool(a.get("enabled", True))
        self.spoolman_base_url = str(
            a.get("spoolman_url", a.get("spoolman_base_url", "http://192.0.2.1:7912"))
        ).rstrip("/")
        self.dry_run = bool(a.get("dry_run", False))
        self.min_consumption_g = float(a.get("min_consumption_g", 2))
        self.max_consumption_g = float(a.get("max_consumption_g", 1000))
        self.min_tray_active_seconds = float(a.get("min_tray_active_seconds", 10))
        self.auto_empty_spools = bool(a.get("auto_empty_spools", False))
        self.auto_archive_depleted_spools = bool(a.get("auto_archive_depleted_spools", False))
        self.notify_service = str(a.get("notify_service", "mobile_app_jd_pixel_10_pro_xl"))
        self._seen_job_keys = OrderedDict()
        self._trays_used = set()
        self._tray_active_times = {}
        self._current_active_slot = None
        self._print_active = False
        self._rehydrated = False
        self._threemf_data = None
        self._threemf_filename = None
        self.threemf_enabled = False
        self._spool_id_snapshot = {}

        # Lifecycle phase flags (must mirror ams_print_usage_sync.py init)
        self._lifecycle_phase1 = bool(a.get("lifecycle_phase1_enabled", False))
        self._job_key = ""
        self._start_snapshot = {}
        self._fuel_gauge_pattern = str(
            a.get("fuel_gauge_pattern", "sensor.p1s_tray_{slot}_fuel_gauge_remaining")
        ).strip()
        self._ams_remaining_pattern = str(
            a.get("ams_remaining_pattern", "sensor.ams_slot_{slot}_remaining_g")
        ).strip()
        self._print_active_entity = str(
            a.get("print_active_entity", "input_boolean.filament_iq_print_active")
        ).strip()
        self._job_key_entity = str(
            a.get("job_key_entity", "input_text.filament_iq_active_job_key")
        ).strip()
        self._start_json_entity = str(
            a.get("start_json_entity", "input_text.filament_iq_start_json")
        ).strip()
        self._lifecycle_phase2 = bool(a.get("lifecycle_phase2_enabled", False))
        self._last_processed_job_key = ""
        self._end_snapshot = {}
        self._lifecycle_phase3 = bool(a.get("lifecycle_phase3_enabled", False))
        self._startup_suppress_until = None
        self._needs_reconcile_entity = str(
            a.get("needs_reconcile_entity", "input_boolean.filament_iq_needs_reconcile")
        ).strip()

        # RFID weight reconciler
        self._weight_reconcile_enabled = bool(
            a.get("weight_reconcile_enabled", True)
        )

        # R2 #8: attrs from real initialize() that were missing from harness
        self.printer_ip = str(a.get("printer_ip", "192.0.2.99"))
        self.printer_ftps_port = int(a.get("printer_ftps_port", 990))
        self.access_code_entity = str(
            a.get("access_code_entity", "input_text.bambu_printer_access_code")
        )
        self.threemf_fetch_method = str(
            a.get("threemf_fetch_method", "native")
        ).strip().lower()
        self.spoolman_sensor_prefix = str(
            a.get("spoolman_sensor_prefix", "sensor.spoolman_spool_")
        ).strip()

        # R2 #7: capture _spoolman_patch calls instead of real HTTP
        self._patch_calls = []

    def initialize(self):
        pass

    def listen_event(self, *a, **kw):
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

    def cancel_timer(self, handle):
        self._cancelled_timers.append(handle)

    def get_state(self, entity_id, attribute=None):
        if attribute:
            key = f"{entity_id}::{attribute}"
            if key in self._state_map:
                return self._state_map[key]
        return self._state_map.get(entity_id, "")

    def _spoolman_use(self, spool_id, use_weight_g):
        if spool_id in self._use_fail_spool_ids:
            self.log(
                f"USAGE_PATCH_FAILED spool_id={spool_id} "
                f"use_weight={use_weight_g:.1f} error=simulated",
                level="ERROR",
            )
            return None
        self._use_calls.append({
            "spool_id": spool_id,
            "use_weight": use_weight_g,
        })
        # Return mock Spoolman response with post-write remaining weight
        remaining = self._use_remaining_override.get(spool_id, 100.0)
        return {"id": spool_id, "remaining_weight": remaining}

    def _spoolman_patch(self, spool_id, data):
        """R2 #7: Mock — capture patch calls instead of real HTTP."""
        self._patch_calls.append({"spool_id": spool_id, "data": data})
        return {"id": spool_id, **data}

    def _spoolman_get(self, path):
        """Mock: avoid real HTTP in tests. Return non-depleted spool."""
        if hasattr(self, "_spoolman_get_override") and self._spoolman_get_override is not None:
            return self._spoolman_get_override(path)
        return {"remaining_weight": 100}

    def _persist_seen_job_keys(self):
        """R2 #9: Mock — no real file I/O in tests."""
        pass


def _has_log(app, substring):
    return any(substring in msg for msg, _ in app._log_calls)


def _default_state_map(spool_bindings=None):
    """State map with spool_id helpers for given slots."""
    sm = {}
    bindings = spool_bindings or {4: 10}
    for slot, sid in bindings.items():
        sm[f"input_text.ams_slot_{slot}_spool_id"] = str(sid)
    return sm


# ── active tray tracking tests ────────────────────────────────────────


def _rfid_tag_uid_for_slots(app, slots):
    """Add tag_uid to state_map for given slots so _is_rfid_slot returns True."""
    result = {}
    for slot in slots:
        entity = app._tray_entity_by_slot.get(slot)
        if entity:
            result[f"{entity}::tag_uid"] = "C7D26F7B00000100"
    return result


def _active_tray_state(app, ams_index, tray_index, name="Generic PLA"):
    """Build state_map entries for the active_tray sensor."""
    e = app._active_tray_entity
    return {
        e: name,
        f"{e}::ams_index": ams_index,
        f"{e}::tray_index": tray_index,
    }


def test_resolve_active_tray_slot_ams_pro():
    """ams_index=0, tray_index=2 → slot 3."""
    app = _TestableUsageSync(state_map=_default_state_map())
    app._state_map.update(_active_tray_state(app, 0, 2))
    assert app._resolve_active_tray_slot() == 3


def test_resolve_active_tray_slot_ht1():
    """ams_index=128, tray_index=0 → slot 5 (HT 1)."""
    app = _TestableUsageSync(state_map=_default_state_map())
    app._state_map.update(_active_tray_state(app, 128, 0))
    assert app._resolve_active_tray_slot() == 5


def test_resolve_active_tray_slot_ht2():
    """ams_index=129, tray_index=0 → slot 6 (HT 2)."""
    app = _TestableUsageSync(state_map=_default_state_map())
    app._state_map.update(_active_tray_state(app, 129, 0))
    assert app._resolve_active_tray_slot() == 6


def test_resolve_active_tray_slot_ht3():
    """ams_index=130, tray_index=0 → slot 7 (HT 3)."""
    app = _TestableUsageSync(state_map=_default_state_map())
    app._state_map.update(_active_tray_state(app, 130, 0))
    assert app._resolve_active_tray_slot() == 7


def test_resolve_active_tray_slot_none_attrs():
    """Missing attributes → None."""
    app = _TestableUsageSync(state_map={})
    app._state_map[app._active_tray_entity] = "none"
    assert app._resolve_active_tray_slot() is None


def test_seed_active_trays_ht_slot():
    """_seed_active_trays picks up HT slot 5 from active_tray sensor."""
    app = _TestableUsageSync(state_map=_default_state_map({5: 30}))
    app._state_map.update(_active_tray_state(app, 128, 0, "Generic PETG"))
    app._print_active = True
    app._seed_active_trays()
    assert 5 in app._trays_used
    assert app._current_active_slot == 5
    assert _has_log(app, "TRAY_TRACKING_SEED slot=5")


def test_on_active_tray_change_records_slot():
    """Simulating active_tray state change records the slot."""
    app = _TestableUsageSync(state_map=_default_state_map({2: 5}))
    app._state_map.update(_active_tray_state(app, 0, 1))
    app._print_active = True

    app._on_active_tray_change(
        app._active_tray_entity, "state", "none", "Generic PLA", {}
    )
    assert 2 in app._trays_used
    assert app._current_active_slot == 2


def test_on_active_tray_change_closes_previous():
    """Switching trays closes the previous segment and opens a new one."""
    app = _TestableUsageSync(state_map=_default_state_map({2: 5, 4: 10}))
    app._state_map.update(_active_tray_state(app, 0, 1))
    app._print_active = True

    # First tray activates
    app._on_active_tray_change(
        app._active_tray_entity, "state", "none", "Generic PLA", {}
    )
    assert app._current_active_slot == 2

    # Switch to slot 4 (ams_index=0, tray_index=3)
    app._state_map.update(_active_tray_state(app, 0, 3, "Overture Matte PLA"))
    app._on_active_tray_change(
        app._active_tray_entity, "state", "Generic PLA", "Overture Matte PLA", {}
    )
    assert app._current_active_slot == 4
    assert app._trays_used == {2, 4}
    # Slot 2 segment should be closed
    assert app._tray_active_times[2][0]["end"] is not None


def test_on_active_tray_change_none_closes_segment():
    """Tray going to 'none' closes current segment."""
    app = _TestableUsageSync(state_map=_default_state_map({2: 5}))
    app._state_map.update(_active_tray_state(app, 0, 1))
    app._print_active = True

    app._on_active_tray_change(
        app._active_tray_entity, "state", "none", "Generic PLA", {}
    )
    assert app._current_active_slot == 2

    # State goes to none — update state_map to reflect no attributes
    e = app._active_tray_entity
    app._state_map[e] = "none"
    app._state_map.pop(f"{e}::ams_index", None)
    app._state_map.pop(f"{e}::tray_index", None)

    app._on_active_tray_change(
        e, "state", "Generic PLA", "none", {}
    )
    assert app._current_active_slot is None
    assert app._tray_active_times[2][0]["end"] is not None


def test_on_active_tray_change_ignored_when_not_printing():
    """Active tray changes are ignored when _print_active is False."""
    app = _TestableUsageSync(state_map=_default_state_map())
    app._state_map.update(_active_tray_state(app, 0, 1))
    app._print_active = False

    app._on_active_tray_change(
        app._active_tray_entity, "state", "none", "Generic PLA", {}
    )
    assert len(app._trays_used) == 0
    assert app._current_active_slot is None




# ── F5: unique job key tests ─────────────────────────────────────────


def test_job_key_includes_timestamp():
    """Two prints of same file get different job keys (timestamp appended)."""
    import time
    app = _TestableUsageSync(
        state_map=_default_state_map({4: 10}),
        args={"lifecycle_phase1_enabled": True},
    )
    app._state_map[app._task_name_entity] = "test_model.3mf"
    app._on_print_start()
    key1 = app._job_key

    # Advance time by at least 1 second to ensure different timestamp
    time.sleep(1.1)
    app._on_print_start()
    key2 = app._job_key

    assert key1 != key2, f"job keys should differ: {key1} == {key2}"
    assert key1.startswith("test_model.3mf_")
    assert key2.startswith("test_model.3mf_")


# ── F7: tray duration filter tests ───────────────────────────────────


def test_brief_tray_activation_filtered():
    """Tray active for < 10s is excluded from trays_used."""
    import datetime
    app = _TestableUsageSync(state_map=_default_state_map({2: 5, 4: 10}))

    now = datetime.datetime.utcnow()
    # Slot 2: brief activation (3 seconds)
    app._tray_active_times[2] = [
        {"start": now - datetime.timedelta(seconds=3), "end": now},
    ]
    # Slot 4: long activation (60 seconds)
    app._tray_active_times[4] = [
        {"start": now - datetime.timedelta(seconds=60), "end": now},
    ]

    result = app._filter_trays_by_duration({2, 4})
    assert result == {4}, f"expected only slot 4, got {result}"


def test_tray_with_no_segments_kept():
    """Tray seeded at start (no segments) is always kept."""
    app = _TestableUsageSync(state_map=_default_state_map({2: 5}))
    app._tray_active_times = {}  # no timing data at all

    result = app._filter_trays_by_duration({2})
    assert result == {2}, "slot with no segments should be kept"


def test_tray_multiple_segments_summed():
    """Multiple short segments that sum above threshold are kept."""
    import datetime
    app = _TestableUsageSync(state_map=_default_state_map({3: 52}))

    now = datetime.datetime.utcnow()
    # Three segments: 4s + 4s + 4s = 12s (above 10s threshold)
    app._tray_active_times[3] = [
        {"start": now - datetime.timedelta(seconds=12), "end": now - datetime.timedelta(seconds=8)},
        {"start": now - datetime.timedelta(seconds=7), "end": now - datetime.timedelta(seconds=3)},
        {"start": now - datetime.timedelta(seconds=2), "end": now + datetime.timedelta(seconds=2)},
    ]

    result = app._filter_trays_by_duration({3})
    assert result == {3}, f"12s total should pass 10s threshold, got {result}"


# ── spool_id change during print — no notification ──────────────────


def test_spool_id_change_during_print_no_notification():
    """_on_spool_id_change during active print logs DEBUG only, no notification or needs_reconcile."""
    app = _TestableUsageSync(
        state_map=_default_state_map({4: 16}),
        args={"lifecycle_phase3_enabled": True},
    )
    app._print_active = True
    app._startup_suppress_until = None

    app._on_spool_id_change(
        "input_text.ams_slot_4_spool_id", "state", "16", "0", {}
    )

    # Should log at DEBUG level, not WARNING
    assert _has_log(app, "SPOOL_ID_CHANGED_DURING_PRINT")
    debug_logs = [(m, l) for m, l in app._log_calls if "SPOOL_ID_CHANGED" in m]
    assert all(l == "DEBUG" for _, l in debug_logs), f"expected DEBUG level, got {debug_logs}"
    # No service calls (no notification, no needs_reconcile)
    assert len(app._service_calls) == 0, f"expected no service calls, got {app._service_calls}"


# ── F2: non-blocking finish wait tests ──────────────────────────────


def test_finish_no_blocking_sleep():
    """_on_print_finish does not call time.sleep — calls _do_finish synchronously."""
    from unittest.mock import patch
    app = _TestableUsageSync(
        state_map=_default_state_map({4: 10}),
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    app._state_map.update(_rfid_tag_uid_for_slots(app, [4]))
    app._job_key = "test_no_sleep_001"
    app._start_snapshot = {4: 420.0}
    app._trays_used = {4}
    app._print_active = True
    app.threemf_enabled = True
    app._threemf_data = None  # 3MF not in memory
    app._state_map[app._fuel_gauge_pattern.format(slot=4)] = "370.0"

    with patch("time.sleep", side_effect=AssertionError("time.sleep must not be called")):
        app._on_print_finish("finish")

    # _do_finish called synchronously (no run_in, no sleep)
    assert _has_log(app, "PRINT_FINISH_CAPTURED"), \
        "expected _do_finish to run synchronously"


def test_finish_recovers_3mf_from_disk():
    """_on_print_finish recovers 3MF from disk when not in memory."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        import filament_iq.ams_print_usage_sync as mod
        orig = mod.ACTIVE_PRINT_FILE
        try:
            ap_file = pathlib.Path(tmp_dir) / "active_print.json"
            mod.ACTIVE_PRINT_FILE = ap_file
            threemf = [{"index": 0, "used_g": 8.0, "color_hex": "00ae42", "material": "pla"}]
            ap_file.write_text(json.dumps({
                "job_key": "test_recover_001",
                "start_snapshot": {"4": 420.0},
                "threemf_data": threemf,
            }))

            app = _TestableUsageSync(
                state_map=_default_state_map({4: 10}),
                args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
            )
            app._state_map.update(_rfid_tag_uid_for_slots(app, [4]))
            app._job_key = "test_recover_001"
            app._start_snapshot = {4: 420.0}
            app._trays_used = {4}
            app._print_active = True
            app.threemf_enabled = True
            app._threemf_data = None

            # Mock _do_finish to capture the recovered data before it gets cleared
            recovered_data = []
            original_do_finish = app._do_finish
            def capture_do_finish(status):
                recovered_data.append(app._threemf_data)
                original_do_finish(status)
            app._do_finish = capture_do_finish
            app._state_map[app._fuel_gauge_pattern.format(slot=4)] = "370.0"

            app._on_print_finish("finish")

            assert recovered_data[0] == threemf, "3MF data should be recovered before _do_finish"
            assert _has_log(app, "3MF_RECOVERED_FROM_DISK")
            assert _has_log(app, "PRINT_FINISH_CAPTURED")
        finally:
            mod.ACTIVE_PRINT_FILE = orig


def test_finish_proceeds_without_threemf():
    """No 3MF in memory or on disk → proceeds with RFID-only, logs warning."""
    app = _TestableUsageSync(
        state_map=_default_state_map({4: 10}),
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    app._state_map.update(_rfid_tag_uid_for_slots(app, [4]))
    app._job_key = "test_no_3mf_001"
    app._start_snapshot = {4: 420.0}
    app._trays_used = {4}
    app._print_active = True
    app.threemf_enabled = True
    app._threemf_data = None
    app._state_map[app._fuel_gauge_pattern.format(slot=4)] = "370.0"

    app._on_print_finish("finish")

    assert _has_log(app, "3MF_UNAVAILABLE_AT_FINISH")
    assert _has_log(app, "PRINT_FINISH_CAPTURED")
    # RFID delta should have been processed
    assert len(app._use_calls) == 1
    assert app._use_calls[0]["spool_id"] == 10
    assert abs(app._use_calls[0]["use_weight"] - 50.0) < 0.01


def test_finish_dedup_prevents_double_processing():
    """Second finish for same job_key is dedup-skipped."""
    app = _TestableUsageSync(
        state_map=_default_state_map({4: 10}),
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    app._state_map.update(_rfid_tag_uid_for_slots(app, [4]))
    app._job_key = "test_dedup_001"
    app._start_snapshot = {4: 420.0}
    app._trays_used = {4}
    app._print_active = True
    app.threemf_enabled = False
    app._state_map[app._fuel_gauge_pattern.format(slot=4)] = "370.0"

    # First finish processes normally
    app._on_print_finish("finish")
    assert _has_log(app, "PRINT_FINISH_CAPTURED")

    # _on_print_end clears job_key; re-set for second call (simulates rehydrate)
    app._job_key = "test_dedup_001"
    app._start_snapshot = {4: 420.0}
    app._trays_used = {4}
    app._print_active = True
    app._log_calls.clear()
    app._on_print_finish("finish")
    assert _has_log(app, "DEDUP_SKIP") or _has_log(app, "PRINT_FINISH_DEDUP_SKIP")


# ── F1: smart empty guard — use POST-write remaining ─────────────────


def test_spoolman_use_returns_spool_data():
    """_spoolman_use mock returns dict with remaining_weight, not bool."""
    app = _TestableUsageSync(state_map=_default_state_map({4: 10}))
    result = app._spoolman_use(10, 5.0)
    assert isinstance(result, dict), f"expected dict, got {type(result)}"
    assert "remaining_weight" in result
    assert result["id"] == 10


def test_spoolman_use_returns_none_on_failure():
    """_spoolman_use returns None when spool_id is in fail set."""
    app = _TestableUsageSync(state_map=_default_state_map({4: 10}))
    app._use_fail_spool_ids.add(10)
    result = app._spoolman_use(10, 5.0)
    assert result is None, f"expected None on failure, got {result}"


# ── Fix: Depleted spool tracking (fuel gauge >= 0) ─────────────────

def test_fuel_gauge_zero_returns_zero():
    """_read_fuel_gauge with fg=0.0 returns 0.0, not -1.0. 0g is a valid depleted spool."""
    app = _TestableUsageSync(state_map={})
    # Set fuel gauge entity for slot 4 to 0.0
    fg_entity = app._fuel_gauge_pattern.format(slot=4)
    app._state_map[fg_entity] = "0.0"
    result = app._read_fuel_gauge(4)
    assert result == 0.0, f"expected 0.0, got {result}"


def test_fuel_gauge_unavailable_still_returns_negative():
    """_read_fuel_gauge with unavailable state returns -1.0 (unchanged behavior)."""
    app = _TestableUsageSync(state_map={})
    fg_entity = app._fuel_gauge_pattern.format(slot=4)
    app._state_map[fg_entity] = "unavailable"
    result = app._read_fuel_gauge(4)
    assert result == -1.0, f"expected -1.0 for unavailable, got {result}"


def test_fuel_gauge_near_empty_negative_accepted():
    """_read_fuel_gauge with fg=-2 returns -2.0 (near-empty RFID tolerance)."""
    app = _TestableUsageSync(state_map={})
    fg_entity = app._fuel_gauge_pattern.format(slot=4)
    app._state_map[fg_entity] = "-2.0"
    result = app._read_fuel_gauge(4)
    assert result == -2.0, f"expected -2.0 for near-empty, got {result}"


def test_fuel_gauge_minus_5_accepted():
    """_read_fuel_gauge with fg=-5 returns -5.0 (boundary of tolerance)."""
    app = _TestableUsageSync(state_map={})
    fg_entity = app._fuel_gauge_pattern.format(slot=4)
    app._state_map[fg_entity] = "-5.0"
    result = app._read_fuel_gauge(4)
    assert result == -5.0, f"expected -5.0 at boundary, got {result}"


def test_fuel_gauge_below_tolerance_falls_back():
    """_read_fuel_gauge with fg=-6 falls back to ams_remaining."""
    app = _TestableUsageSync(state_map={})
    fg_entity = app._fuel_gauge_pattern.format(slot=4)
    app._state_map[fg_entity] = "-6.0"
    ams_entity = app._ams_remaining_pattern.format(slot=4)
    app._state_map[ams_entity] = "15.0"
    result = app._read_fuel_gauge(4)
    assert result == 15.0, f"expected 15.0 from ams fallback, got {result}"


def test_end_snapshot_includes_zero_gram_slot():
    """Spool at 0g fuel gauge is included in end snapshot (previously excluded)."""
    app = _TestableUsageSync(state_map={})
    app._start_snapshot = {1: 800.0, 3: 40.0}
    # Slot 1: 785g remaining, Slot 3: 0g (depleted)
    fg1 = app._fuel_gauge_pattern.format(slot=1)
    fg3 = app._fuel_gauge_pattern.format(slot=3)
    app._state_map[fg1] = "785.0"
    app._state_map[fg3] = "0.0"
    snapshot = app._build_end_snapshot()
    assert snapshot == {1: 785.0, 3: 0.0}, f"expected slot 3 at 0.0, got {snapshot}"


def test_seed_slot_not_reseeded_at_zero():
    """Slot seeded at 0g should not be re-seeded — 0g is a valid locked value."""
    app = _TestableUsageSync(state_map={})
    app._lifecycle_phase1 = True
    app._start_snapshot = {4: 0.0}
    fg4 = app._fuel_gauge_pattern.format(slot=4)
    app._state_map[fg4] = "800.0"
    app._seed_slot_start_grams(4)
    assert app._start_snapshot[4] == 0.0, "0g seed should be locked, not overwritten"


# ── Fix: Rehydrate post-restart tracking ───────────────────────────

def test_rehydrate_sets_rehydrated_flag():
    """_rehydrate_print_state sets _rehydrated=True. _trays_used starts empty, populated by events."""
    app = _TestableUsageSync(state_map={})
    # Simulate mid-print state
    app._state_map[app._print_status_entity] = "running"
    app._state_map[app._start_json_entity] = '{"1": 1000.0, "3": 40.0}'
    app._state_map[app._task_name_entity] = "Crates - All Sizes"
    app._lifecycle_phase1 = True
    app._rehydrate_print_state()
    assert app._rehydrated is True, "_rehydrated flag not set"
    assert app._print_active is True
    assert _has_log(app, "REHYDRATE_FLAG_SET")
    # _trays_used should NOT contain all start_snapshot keys — only active tray (if any)
    assert app._trays_used != {1, 3}, "_trays_used must not be set from start_snapshot keys"


def test_duration_filter_skipped_on_rehydrate():
    """When _rehydrated=True, duration filter returns all trays unfiltered."""
    import datetime
    app = _TestableUsageSync(state_map={})
    app._rehydrated = True
    # Slot 3 has only 1.8s — would normally be filtered
    app._tray_active_times = {
        3: [{"start": datetime.datetime(2026, 3, 12, 3, 26, 33),
             "end": datetime.datetime(2026, 3, 12, 3, 26, 34, 800000)}],
        1: [{"start": datetime.datetime(2026, 3, 12, 3, 26, 33),
             "end": datetime.datetime(2026, 3, 12, 8, 13, 0)}],
    }
    result = app._filter_trays_by_duration({1, 3})
    assert result == {1, 3}, f"rehydrated print should skip filter, got {result}"
    assert _has_log(app, "TRAY_FILTER_SKIPPED")


def test_rehydrated_flag_reset_on_print_start():
    """Print start transition resets _rehydrated to False."""
    app = _TestableUsageSync(state_map={})
    app._rehydrated = True
    # Simulate print start transition
    app._state_map[app._print_status_entity] = "running"
    app._on_print_status_change(
        app._print_status_entity, None, "idle", "running", {}
    )
    assert app._rehydrated is False, "_rehydrated should reset on print start"


def test_duration_filter_still_runs_without_rehydrate():
    """When _rehydrated=False, duration filter works normally (regression check)."""
    import datetime
    app = _TestableUsageSync(state_map={})
    app._rehydrated = False
    # Slot 3 has only 1.8s — should be filtered out
    app._tray_active_times = {
        3: [{"start": datetime.datetime(2026, 3, 12, 3, 26, 33),
             "end": datetime.datetime(2026, 3, 12, 3, 26, 34, 800000)}],
        1: [{"start": datetime.datetime(2026, 3, 12, 3, 26, 33),
             "end": datetime.datetime(2026, 3, 12, 8, 13, 0)}],
    }
    result = app._filter_trays_by_duration({1, 3})
    assert result == {1}, f"non-rehydrated print should filter slot 3, got {result}"
    assert not _has_log(app, "TRAY_FILTER_SKIPPED")


# ── Fix: FTPS retry window tests ────────────────────────────────────

def test_3mf_initial_fetch_delay_10s():
    """3MF background fetch should be scheduled with 10s delay, not 5s."""
    app = _TestableUsageSync(
        state_map=_default_state_map({3: 39}),
        args={"lifecycle_phase2_enabled": True},
    )
    app.threemf_enabled = True
    # Simulate print start: old=idle, new=running
    app._on_print_status_change(None, None, "idle", "running", {})
    # Find the run_in call for _fetch_3mf_background
    fetch_calls = [c for c in app._run_in_calls
                   if c["callback"].__name__ == "_fetch_3mf_background"]
    assert len(fetch_calls) >= 1, "expected _fetch_3mf_background to be scheduled"
    assert fetch_calls[0]["delay"] == 10, (
        f"expected 10s initial delay, got {fetch_calls[0]['delay']}s"
    )


def test_3mf_fetch_retry_scheduled_from_callback():
    """Error result delivered to _on_3mf_fetched schedules retry via run_in."""
    app = _TestableUsageSync(
        state_map=_default_state_map({3: 39}),
        args={"lifecycle_phase2_enabled": True},
    )
    app.threemf_enabled = True
    app._job_key = "test_job"
    # Simulate attempt 3 error result from background thread
    result = {
        "job_key": "test_job",
        "attempt": 3,
        "status": "error",
        "error": "Connection refused",
        "filaments": None,
        "filename": None,
        "found_dir": None,
        "file_count": 0,
        "task_name": "benchy",
        "file_list": [],
        "timing": {"total": 0.5},
    }
    app._on_3mf_fetched({"fetch_result": result})
    # Should schedule attempt 4 with 90s delay
    retry_calls = [c for c in app._run_in_calls
                   if c.get("attempt") == 4]
    assert len(retry_calls) == 1, (
        f"expected attempt 4 to be scheduled; "
        f"run_in_calls={app._run_in_calls}"
    )
    assert retry_calls[0]["delay"] == 90
    assert _has_log(app, "3MF_FETCH_ERROR")


def test_3mf_fetch_runs_in_thread():
    """_fetch_3mf_background spawns a daemon thread, not blocking event loop."""
    app = _TestableUsageSync(
        state_map={
            "sensor.p1s_01p00c5a3101668_task_name": "benchy",
        },
        args={"lifecycle_phase2_enabled": True, "printer_access_code": "12345678"},
    )
    app.threemf_enabled = True
    app.threemf_fetch_method = "native"
    app.printer_ip = "192.0.2.1"
    app._job_key = "test_job"
    threads_before = set(threading.enumerate())
    with mock.patch("threading.Thread") as mock_thread:
        mock_thread.return_value = mock.MagicMock()
        app._fetch_3mf_background({"attempt": 1})
        mock_thread.assert_called_once()
        call_kwargs = mock_thread.call_args
        assert call_kwargs.kwargs["daemon"] is True
        assert call_kwargs.kwargs["target"] == app._fetch_3mf_thread


def test_3mf_fetch_stale_job_key_discarded():
    """If job_key changes during fetch, result is discarded."""
    app = _TestableUsageSync()
    app._job_key = "new_print_456"
    result = {
        "job_key": "old_print_123",
        "attempt": 1,
        "status": "success",
        "filaments": [{"index": 0, "used_g": 10.0, "color_hex": "ff0000", "material": "pla"}],
        "filename": "test.3mf",
        "found_dir": "/cache",
        "file_count": 5,
        "task_name": "test",
        "file_list": [],
        "timing": {"total": 3.0},
    }
    app._on_3mf_fetched({"fetch_result": result})
    assert app._threemf_data is None
    assert _has_log(app, "3MF_FETCH_STALE")


def test_3mf_fetch_result_persisted_on_success():
    """Successful fetch updates _threemf_data and calls _persist_active_print."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            import filament_iq.ams_print_usage_sync as mod
            mod.ACTIVE_PRINT_FILE = pathlib.Path(tmp_dir) / "active_print.json"

            app = _TestableUsageSync(
                state_map={
                    "sensor.p1s_01p00c5a3101668_task_name": "benchy",
                },
                args={"lifecycle_phase1_enabled": True},
            )
            app._job_key = "success_test"
            filaments = [
                {"index": 0, "used_g": 12.5, "color_hex": "00ae42", "material": "pla"},
            ]
            result = {
                "job_key": "success_test",
                "attempt": 1,
                "status": "success",
                "filaments": filaments,
                "filename": "benchy.gcode.3mf",
                "found_dir": "/cache",
                "file_count": 42,
                "task_name": "benchy",
                "file_list": [],
                "timing": {"connect": 1.2, "list": 0.8, "download": 2.1, "parse": 0.1, "total": 4.2},
            }
            app._on_3mf_fetched({"fetch_result": result})
            assert app._threemf_data == filaments
            assert app._threemf_filename == "benchy.gcode.3mf"
            assert _has_log(app, "3MF_PARSED")
            assert _has_log(app, "3MF_TIMING")
            assert _has_log(app, "total=4.2s")
            assert _has_log(app, "ACTIVE_PRINT_PERSISTED")
        finally:
            orig = getattr(mod, '_ACTIVE_PRINT_FILE_ORIG', None)
            if orig:
                mod.ACTIVE_PRINT_FILE = orig


# ── Atomic write tests for _persist_seen_job_keys ────────────────────

def test_atomic_write_uses_replace():
    """_persist_seen_job_keys must use os.replace for atomic write."""
    from filament_iq.ams_print_usage_sync import AmsPrintUsageSync, SEEN_JOBS_PATH

    app = _TestableUsageSync()
    app._seen_job_keys = OrderedDict([("job_a", True), ("job_b", True)])

    with mock.patch("filament_iq.ams_print_usage_sync.os.replace") as mock_replace, \
         mock.patch("builtins.open", mock.mock_open()), \
         mock.patch("filament_iq.ams_print_usage_sync.os.makedirs"):
        AmsPrintUsageSync._persist_seen_job_keys(app)
        mock_replace.assert_called_once_with(SEEN_JOBS_PATH + ".tmp", SEEN_JOBS_PATH)


def test_atomic_write_cleans_tmp_on_failure():
    """If os.replace fails, .tmp file must be cleaned up."""
    from filament_iq.ams_print_usage_sync import AmsPrintUsageSync, SEEN_JOBS_PATH

    app = _TestableUsageSync()
    app._seen_job_keys = OrderedDict([("job_a", True)])

    with mock.patch("filament_iq.ams_print_usage_sync.os.replace",
                     side_effect=OSError("disk full")), \
         mock.patch("builtins.open", mock.mock_open()), \
         mock.patch("filament_iq.ams_print_usage_sync.os.makedirs"), \
         mock.patch("filament_iq.ams_print_usage_sync.os.unlink") as mock_unlink:
        AmsPrintUsageSync._persist_seen_job_keys(app)
        mock_unlink.assert_called_once_with(SEEN_JOBS_PATH + ".tmp")
        assert _has_log(app, "PERSIST_JOB_KEYS_FAILED")


# ── R2 #4: _rehydrate_print_state tests ──────────────────────────────

def test_rehydrate_mid_print_restores_active():
    """If printer is 'running' on startup, _print_active set to True."""
    app = _TestableUsageSync(state_map={
        f"sensor.p1s_01p00c5a3101668_print_status": "running",
    })
    assert app._print_active is False
    app._rehydrate_print_state()
    assert app._print_active is True
    assert _has_log(app, "REHYDRATE_PRINT_ACTIVE status=running")


def test_rehydrate_idle_no_action():
    """If printer is idle on startup, no rehydration — _print_active stays False."""
    app = _TestableUsageSync(state_map={
        f"sensor.p1s_01p00c5a3101668_print_status": "idle",
    })
    app._rehydrate_print_state()
    assert app._print_active is False
    assert not _has_log(app, "REHYDRATE_PRINT_ACTIVE")


def test_rehydrate_paused_restores_active():
    """Paused printer is still mid-print — should rehydrate."""
    app = _TestableUsageSync(state_map={
        f"sensor.p1s_01p00c5a3101668_print_status": "pause",
    })
    app._rehydrate_print_state()
    assert app._print_active is True
    assert _has_log(app, "REHYDRATE_PRINT_ACTIVE status=pause")


def test_rehydrate_phase1_recovers_start_json():
    """Phase1: start_json recovered from HA helper on rehydrate."""
    app = _TestableUsageSync(
        args={"lifecycle_phase1_enabled": True},
        state_map={
            f"sensor.p1s_01p00c5a3101668_print_status": "running",
            "input_text.filament_iq_start_json": '{"1": 500.0, "2": 800.0}',
            f"sensor.p1s_01p00c5a3101668_task_name": "test_print",
        },
    )
    app._rehydrate_print_state()
    assert app._start_snapshot == {1: 500.0, 2: 800.0}
    assert _has_log(app, "REHYDRATE_START_SNAPSHOT_RECOVERED")


def test_rehydrate_phase1_corrupt_json_rebuilds():
    """Phase1: corrupt start_json helper → falls back to fuel gauge rebuild."""
    app = _TestableUsageSync(
        args={"lifecycle_phase1_enabled": True},
        state_map={
            f"sensor.p1s_01p00c5a3101668_print_status": "running",
            "input_text.filament_iq_start_json": "NOT_VALID_JSON{{{",
            f"sensor.p1s_01p00c5a3101668_task_name": "test_print",
        },
    )
    app._rehydrate_print_state()
    # Should have attempted rebuild (even if snapshot is empty due to missing gauges)
    assert _has_log(app, "REHYDRATE_START_SNAPSHOT_REBUILT") or _has_log(app, "Failed to recover start_json")


def test_rehydrate_empty_status_no_crash():
    """Empty/unavailable print_status → no rehydration, no crash."""
    app = _TestableUsageSync(state_map={
        f"sensor.p1s_01p00c5a3101668_print_status": "",
    })
    app._rehydrate_print_state()
    assert app._print_active is False


# ── R2 #5: _coerce_json_field tests ──────────────────────────────────

def test_coerce_json_field_valid_dict():
    """Valid dict field → returned as-is."""
    app = _TestableUsageSync()
    data = {"extra": {"rfid_tag_uid": "abc"}}
    result = app._coerce_json_field(data, "extra")
    assert result == {"rfid_tag_uid": "abc"}


def test_coerce_json_field_json_string():
    """JSON string field → parsed to dict."""
    app = _TestableUsageSync()
    data = {"extra": '{"rfid_tag_uid": "abc"}'}
    result = app._coerce_json_field(data, "extra")
    assert result == {"rfid_tag_uid": "abc"}


def test_coerce_json_field_none_returns_empty():
    """None value (missing key) → returns empty dict."""
    app = _TestableUsageSync()
    data = {}
    result = app._coerce_json_field(data, "extra")
    assert result == {}


def test_coerce_json_field_malformed_returns_none():
    """Malformed JSON string → returns None, logs error."""
    app = _TestableUsageSync()
    data = {"extra": "NOT{VALID}JSON"}
    result = app._coerce_json_field(data, "extra")
    assert result is None
    assert _has_log(app, "JSON_PARSE_ERROR")


def test_coerce_json_field_empty_string():
    """Empty string → returns empty dict."""
    app = _TestableUsageSync()
    data = {"extra": ""}
    result = app._coerce_json_field(data, "extra")
    assert result == {}


# ── RFID weight reconciler tests ─────────────────────────────────────

def _reconcile_app(slot=4, spool_id=10, remain=39, tray_weight=1000,
                   remain_enabled=True, spoolman_remaining=0.0):
    """Build a _TestableUsageSync configured for RFID weight reconcile tests."""
    sm = _default_state_map({slot: spool_id})
    app = _TestableUsageSync(state_map=sm)
    # Mark slot as RFID-capable
    app._state_map.update(_rfid_tag_uid_for_slots(app, [slot]))
    # Set tray attributes
    entity = app._tray_entity_by_slot[slot]
    app._state_map[f"{entity}::remain"] = remain
    app._state_map[f"{entity}::tray_weight"] = tray_weight
    app._state_map[f"{entity}::remain_enabled"] = remain_enabled
    # Mock _spoolman_get to return specific remaining_weight per spool
    def _get_override(path):
        if f"/api/v1/spool/{spool_id}" in path:
            return {"id": spool_id, "remaining_weight": spoolman_remaining}
        return {"remaining_weight": 100}
    app._spoolman_get_override = _get_override
    return app


def test_rfid_weight_reconcile_corrects_drift():
    """RFID says 390g, Spoolman says 500g → PATCH called with 390g (downward correction)."""
    app = _reconcile_app(remain=39, tray_weight=1000, spoolman_remaining=500.0)
    app._reconcile_rfid_weights()
    assert len(app._patch_calls) == 1
    assert app._patch_calls[0]["spool_id"] == 10
    assert app._patch_calls[0]["data"] == {"remaining_weight": 390.0}
    assert _has_log(app, "RFID_WEIGHT_RECONCILED slot=4 spool_id=10")
    assert _has_log(app, "rfid=390.0g spoolman_was=500.0g")


def test_rfid_weight_reconcile_skips_exact_match():
    """RFID says 390g, Spoolman says 390g → no PATCH called."""
    app = _reconcile_app(remain=39, tray_weight=1000, spoolman_remaining=390.0)
    app._reconcile_rfid_weights()
    assert len(app._patch_calls) == 0
    assert _has_log(app, "RFID_WEIGHT_MATCH slot=4 spool_id=10")


def test_rfid_weight_reconcile_skips_non_rfid():
    """Slot with remain_enabled=False → skip, no PATCH."""
    app = _reconcile_app(remain=39, tray_weight=1000, remain_enabled=False,
                         spoolman_remaining=0.0)
    app._reconcile_rfid_weights()
    assert len(app._patch_calls) == 0
    assert not _has_log(app, "RFID_WEIGHT_RECONCILED")


def test_rfid_weight_reconcile_skips_unbound():
    """spool_id=0 (unbound slot) → skip, no PATCH."""
    app = _reconcile_app(spool_id=0, spoolman_remaining=0.0)
    # Override state_map to return 0 for spool_id
    app._state_map["input_text.ams_slot_4_spool_id"] = "0"
    app._reconcile_rfid_weights()
    assert len(app._patch_calls) == 0
    assert not _has_log(app, "RFID_WEIGHT_RECONCILED")


def test_rfid_weight_reconcile_handles_spoolman_failure():
    """Spoolman GET returns None → skip gracefully, no crash."""
    app = _reconcile_app(remain=39, tray_weight=1000, spoolman_remaining=0.0)
    app._spoolman_get_override = lambda path: None
    app._reconcile_rfid_weights()
    assert len(app._patch_calls) == 0
    assert _has_log(app, "RFID_WEIGHT_RECONCILE_SKIP")
    assert _has_log(app, "spoolman_fetch_failed")


def test_rfid_weight_reconcile_respects_dry_run():
    """dry_run=True → no PATCH called, DRYRUN logged."""
    app = _reconcile_app(remain=39, tray_weight=1000, spoolman_remaining=500.0)
    app.dry_run = True
    app._reconcile_rfid_weights()
    assert len(app._patch_calls) == 0
    assert _has_log(app, "RFID_WEIGHT_RECONCILE_DRYRUN slot=4 spool_id=10")
    assert _has_log(app, "rfid=390.0g spoolman_was=500.0g")


def test_rfid_weight_reconcile_skips_zero_tray_weight():
    """tray_weight=0 → skip slot, no PATCH, no crash."""
    app = _reconcile_app(remain=50, tray_weight=0, spoolman_remaining=0.0)
    app._reconcile_rfid_weights()
    assert len(app._patch_calls) == 0
    assert not _has_log(app, "RFID_WEIGHT_RECONCILED")


def test_rfid_weight_reconcile_skips_invalid_remain_negative():
    """remain=-1 → skip slot, WARNING logged, no PATCH."""
    app = _reconcile_app(remain=-1, tray_weight=1000, spoolman_remaining=0.0)
    app._reconcile_rfid_weights()
    assert len(app._patch_calls) == 0
    assert _has_log(app, "RFID_WEIGHT_INVALID_REMAIN slot=4")


def test_rfid_weight_reconcile_skips_invalid_remain_none():
    """remain=None → skip slot, WARNING logged, no PATCH."""
    app = _reconcile_app(remain=39, tray_weight=1000, spoolman_remaining=0.0)
    # Override remain to None after _reconcile_app set it
    entity = app._tray_entity_by_slot[4]
    app._state_map[f"{entity}::remain"] = None
    app._reconcile_rfid_weights()
    assert len(app._patch_calls) == 0
    assert _has_log(app, "RFID_WEIGHT_INVALID_REMAIN slot=4")


def test_on_print_start_captures_snapshot():
    """_on_print_start writes job_key and start_snapshot."""
    app = _TestableUsageSync(
        state_map=_default_state_map({4: 10}),
        args={"lifecycle_phase1_enabled": True},
    )
    app._state_map[app._task_name_entity] = "test_model"
    app._print_active = True
    app._on_print_start()
    assert app._job_key.startswith("test_model_")
    assert isinstance(app._start_snapshot, dict)
    assert _has_log(app, "PRINT_START_CAPTURED")


def test_on_print_end_clears_state():
    """_on_print_end clears start_snapshot and job_key."""
    app = _TestableUsageSync(
        state_map=_default_state_map({4: 10}),
        args={"lifecycle_phase1_enabled": True},
    )
    app._job_key = "test_job"
    app._start_snapshot = {4: 420.0}
    app._on_print_end()
    assert app._start_snapshot == {}
    assert app._job_key == ""


def test_finish_synchronous_do_finish_called():
    """_on_print_finish calls _do_finish synchronously (no polling)."""
    app = _TestableUsageSync(
        state_map=_default_state_map({4: 10}),
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    app._job_key = "test_sync_001"
    app._start_snapshot = {4: 420.0}
    app.threemf_enabled = False
    app._do_finish = mock.MagicMock()
    app._on_print_finish("finish")
    app._do_finish.assert_called_once_with("finish")
    # No run_in calls for polling
    assert len(app._run_in_calls) == 0


def test_seed_slot_start_grams_write_once():
    """_seed_slot_start_grams only writes if slot not already in snapshot."""
    app = _TestableUsageSync(
        state_map=_default_state_map({4: 10}),
        args={"lifecycle_phase1_enabled": True},
    )
    app._start_snapshot = {4: 420.0}
    # Seed should not overwrite
    app._seed_slot_start_grams(4)
    assert app._start_snapshot[4] == 420.0


def test_finish_offline_state_warning():
    """Print finishing with offline status → 3MF suppressed, RFID delta proceeds."""
    app = _TestableUsageSync(
        state_map=_default_state_map({4: 10}),
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    app._state_map.update(_rfid_tag_uid_for_slots(app, [4]))
    app._job_key = "test_offline"
    app._start_snapshot = {4: 420.0}
    app._trays_used = {4}
    app._print_active = True
    app._state_map[app._fuel_gauge_pattern.format(slot=4)] = "370.0"
    app._do_finish("offline")
    assert _has_log(app, "3MF_SUPPRESSED_NON_SUCCESS")


# ── active_print.json persistence tests ──────────────────────────────

class TestActivePrintPersistence:

    def _make_app(self, tmp_dir, **extra_args):
        """Build a testable app with ACTIVE_PRINT_FILE pointed at tmp_dir."""
        import filament_iq.ams_print_usage_sync as mod
        self._orig_file = mod.ACTIVE_PRINT_FILE
        mod.ACTIVE_PRINT_FILE = pathlib.Path(tmp_dir) / "active_print.json"
        args = {
            "lifecycle_phase1_enabled": True,
            "lifecycle_phase2_enabled": True,
            "lifecycle_phase3_enabled": True,
        }
        args.update(extra_args)
        app = _TestableUsageSync(args=args)
        return app

    def _cleanup(self):
        import filament_iq.ams_print_usage_sync as mod
        if hasattr(self, "_orig_file"):
            mod.ACTIVE_PRINT_FILE = self._orig_file

    def test_active_print_persisted_on_start(self):
        """Print starts → active_print.json written with job_key + start_snapshot."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                import filament_iq.ams_print_usage_sync as mod
                ap_file = pathlib.Path(tmp_dir) / "active_print.json"
                mod.ACTIVE_PRINT_FILE = ap_file

                app = _TestableUsageSync(args={
                    "lifecycle_phase1_enabled": True,
                })
                app._job_key = "test_job_123"
                app._start_snapshot = {1: 500.0, 2: 300.0}
                app._threemf_data = None
                app._persist_active_print()

                assert ap_file.exists()
                data = json.loads(ap_file.read_text())
                assert data["job_key"] == "test_job_123"
                assert data["start_snapshot"] == {"1": 500.0, "2": 300.0}
                assert data["threemf_data"] is None
            finally:
                self._cleanup()

    def test_active_print_updated_after_3mf_parse(self):
        """3MF parsed → active_print.json updated with threemf_data."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                import filament_iq.ams_print_usage_sync as mod
                ap_file = pathlib.Path(tmp_dir) / "active_print.json"
                mod.ACTIVE_PRINT_FILE = ap_file

                app = _TestableUsageSync(args={
                    "lifecycle_phase1_enabled": True,
                })
                app._job_key = "test_job_456"
                app._start_snapshot = {1: 500.0}
                threemf = [{"index": 0, "used_g": 12.5, "color_hex": "FF0000", "material": "PLA"}]
                app._threemf_data = threemf
                app._persist_active_print()

                data = json.loads(ap_file.read_text())
                assert data["threemf_data"] == threemf
                assert data["job_key"] == "test_job_456"
            finally:
                self._cleanup()

    def test_active_print_restored_on_rehydrate(self):
        """active_print.json exists with matching job_key → threemf_data restored."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                import filament_iq.ams_print_usage_sync as mod
                ap_file = pathlib.Path(tmp_dir) / "active_print.json"
                mod.ACTIVE_PRINT_FILE = ap_file

                threemf = [{"index": 0, "used_g": 25.0, "color_hex": "00FF00", "material": "PETG"}]
                ap_file.write_text(json.dumps({
                    "job_key": "matching_key",
                    "start_snapshot": {"1": 400.0},
                    "threemf_data": threemf,
                }))

                app = _TestableUsageSync(args={
                    "lifecycle_phase1_enabled": True,
                })
                result = app._load_active_print("matching_key")
                assert result["threemf_data"] == threemf
                assert result["trays_used"] == set()
                assert result["spool_id_snapshot"] == {}
                assert _has_log(app, "ACTIVE_PRINT_RESTORED")
            finally:
                self._cleanup()

    def test_active_print_stale_job_key_ignored(self):
        """active_print.json has different job_key → returns None, STALE logged."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                import filament_iq.ams_print_usage_sync as mod
                ap_file = pathlib.Path(tmp_dir) / "active_print.json"
                mod.ACTIVE_PRINT_FILE = ap_file

                ap_file.write_text(json.dumps({
                    "job_key": "old_job",
                    "start_snapshot": {},
                    "threemf_data": [{"index": 0, "used_g": 10.0}],
                }))

                app = _TestableUsageSync(args={
                    "lifecycle_phase1_enabled": True,
                })
                result = app._load_active_print("new_job")
                assert result is None
                assert _has_log(app, "ACTIVE_PRINT_STALE")
            finally:
                self._cleanup()

    def test_active_print_missing_no_error(self):
        """active_print.json does not exist → returns None, no exception."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                import filament_iq.ams_print_usage_sync as mod
                mod.ACTIVE_PRINT_FILE = pathlib.Path(tmp_dir) / "active_print.json"

                app = _TestableUsageSync(args={
                    "lifecycle_phase1_enabled": True,
                })
                result = app._load_active_print("any_key")
                assert result is None
                # No error/warning logged
                assert not any("FAILED" in msg for msg, _ in app._log_calls)
            finally:
                self._cleanup()

    def test_active_print_corrupted_no_error(self):
        """active_print.json contains invalid JSON → returns None, LOAD_FAILED logged."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                import filament_iq.ams_print_usage_sync as mod
                ap_file = pathlib.Path(tmp_dir) / "active_print.json"
                mod.ACTIVE_PRINT_FILE = ap_file
                ap_file.write_text("{corrupt json!!")

                app = _TestableUsageSync(args={
                    "lifecycle_phase1_enabled": True,
                })
                result = app._load_active_print("any_key")
                assert result is None
                assert _has_log(app, "ACTIVE_PRINT_LOAD_FAILED")
            finally:
                self._cleanup()

    def test_active_print_cleared_on_finish(self):
        """Print finishes → active_print.json deleted."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                import filament_iq.ams_print_usage_sync as mod
                ap_file = pathlib.Path(tmp_dir) / "active_print.json"
                mod.ACTIVE_PRINT_FILE = ap_file
                ap_file.write_text(json.dumps({"job_key": "done"}))
                assert ap_file.exists()

                app = _TestableUsageSync(args={
                    "lifecycle_phase1_enabled": True,
                })
                app._clear_active_print()
                assert not ap_file.exists()
            finally:
                self._cleanup()

    def test_3mf_in_memory_skips_disk_recovery(self):
        """_threemf_data already present at finish → no disk recovery attempted."""
        app = _TestableUsageSync(args={
            "lifecycle_phase1_enabled": True,
            "lifecycle_phase2_enabled": True,
        })
        app._job_key = "test_skip_recovery"
        app._start_snapshot = {1: 500.0}
        app._threemf_data = [{"index": 0, "used_g": 15.0}]
        app.threemf_enabled = True
        app._do_finish = mock.MagicMock()
        app._on_print_finish("finish")
        # Should not attempt disk recovery
        assert not _has_log(app, "3MF_RECOVERED_FROM_DISK")
        assert not _has_log(app, "3MF_UNAVAILABLE_AT_FINISH")
        app._do_finish.assert_called_once_with("finish")

    def test_active_print_atomic_write(self):
        """Confirm tmp file used and replaced atomically (no .tmp left behind)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                import filament_iq.ams_print_usage_sync as mod
                ap_file = pathlib.Path(tmp_dir) / "active_print.json"
                mod.ACTIVE_PRINT_FILE = ap_file

                app = _TestableUsageSync(args={
                    "lifecycle_phase1_enabled": True,
                })
                app._job_key = "atomic_test"
                app._start_snapshot = {1: 100.0}
                app._threemf_data = None
                app._persist_active_print()

                # Verify final file exists and is valid
                assert ap_file.exists()
                data = json.loads(ap_file.read_text())
                assert data["job_key"] == "atomic_test"
                # Verify no .tmp file left behind (atomic replace cleaned it up)
                tmp_file = ap_file.with_suffix(".tmp")
                assert not tmp_file.exists()
            finally:
                self._cleanup()

    def test_persist_active_print_includes_trays_used_and_spool_ids(self):
        """trays_used and spool_id_snapshot appear in persisted JSON."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                import filament_iq.ams_print_usage_sync as mod
                ap_file = pathlib.Path(tmp_dir) / "active_print.json"
                mod.ACTIVE_PRINT_FILE = ap_file

                app = _TestableUsageSync(args={
                    "lifecycle_phase1_enabled": True,
                })
                app._job_key = "trays_test"
                app._start_snapshot = {1: 400.0, 5: 200.0}
                app._trays_used = {1, 5}
                app._spool_id_snapshot = {1: 61, 5: 29}
                app._threemf_data = None
                app._persist_active_print()

                data = json.loads(ap_file.read_text())
                assert data["trays_used"] == [1, 5]
                assert data["spool_id_snapshot"] == {"1": 61, "5": 29}
            finally:
                self._cleanup()

    def test_load_active_print_returns_full_dict(self):
        """_load_active_print returns dict with threemf_data, trays_used, spool_id_snapshot."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                import filament_iq.ams_print_usage_sync as mod
                ap_file = pathlib.Path(tmp_dir) / "active_print.json"
                mod.ACTIVE_PRINT_FILE = ap_file

                ap_file.write_text(json.dumps({
                    "job_key": "full_dict_test",
                    "start_snapshot": {"1": 400.0},
                    "trays_used": [1, 5],
                    "spool_id_snapshot": {"1": 61, "5": 29},
                    "threemf_data": [{"index": 0, "used_g": 10.0}],
                }))

                app = _TestableUsageSync(args={
                    "lifecycle_phase1_enabled": True,
                })
                result = app._load_active_print("full_dict_test")
                assert result is not None
                assert result["threemf_data"] == [{"index": 0, "used_g": 10.0}]
                assert result["trays_used"] == {1, 5}
                assert result["spool_id_snapshot"] == {1: 61, 5: 29}
            finally:
                self._cleanup()

    def test_spool_id_snapshot_captured_at_print_start(self):
        """_spool_id_snapshot populated after PRINT_START_CAPTURED."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                import filament_iq.ams_print_usage_sync as mod
                mod.ACTIVE_PRINT_FILE = pathlib.Path(tmp_dir) / "active_print.json"

                app = _TestableUsageSync(state_map={
                    "sensor.p1s_01p00c5a3101668_task_name": "test_print",
                    "sensor.p1s_01p00c5a3101668_print_status": "running",
                    "input_text.ams_slot_1_spool_id": "61",
                    "input_text.ams_slot_2_spool_id": "0",
                    "input_text.ams_slot_3_spool_id": "45",
                    "input_text.ams_slot_4_spool_id": "",
                    "input_text.ams_slot_5_spool_id": "29",
                    "input_text.ams_slot_6_spool_id": "nope",
                }, args={"lifecycle_phase1_enabled": True})
                app._on_print_start()
                assert app._spool_id_snapshot == {1: 61, 3: 45, 5: 29}
                assert _has_log(app, "PRINT_START_CAPTURED")
                assert _has_log(app, "spool_ids=3")
            finally:
                self._cleanup()


# ── lifecycle method tests ────────────────────────────────────────────

class TestLifecycleMethods:
    """Tests for _on_print_start, _on_print_end, _do_finish, _on_print_finish."""

    def test_on_print_start_captures_job_key(self):
        """_on_print_start sets job_key and start_snapshot."""
        app = _TestableUsageSync(
            state_map={
                "sensor.p1s_01p00c5a3101668_task_name": "benchy.3mf",
                "sensor.p1s_tray_1_fuel_gauge_remaining": "500.0",
                "sensor.p1s_tray_2_fuel_gauge_remaining": "300.0",
            },
            args={"lifecycle_phase1_enabled": True},
        )
        app._on_print_start()
        assert app._job_key.startswith("benchy.3mf_")
        assert _has_log(app, "PRINT_START_CAPTURED")
        assert any(c["service"] == "input_boolean/turn_on" for c in app._service_calls)
        assert any(c["service"] == "input_text/set_value" for c in app._service_calls)

    def test_on_print_end_clears_state(self):
        """_on_print_end clears start_snapshot and job_key."""
        app = _TestableUsageSync(args={"lifecycle_phase1_enabled": True})
        app._job_key = "test_key"
        app._start_snapshot = {1: 500.0}
        app._on_print_end()
        assert app._job_key == ""
        assert app._start_snapshot == {}
        assert any(c["service"] == "input_boolean/turn_off" for c in app._service_calls)

    def test_on_print_finish_no_job_key_skips(self):
        """_on_print_finish with no job_key logs skip."""
        app = _TestableUsageSync(args={
            "lifecycle_phase1_enabled": True,
            "lifecycle_phase2_enabled": True,
        })
        app._job_key = ""
        app._on_print_finish("finish")
        assert _has_log(app, "PRINT_FINISH_SKIP reason=no_job_key")

    def test_on_print_finish_no_start_snapshot_skips(self):
        """_on_print_finish with no start_snapshot logs skip."""
        app = _TestableUsageSync(args={
            "lifecycle_phase1_enabled": True,
            "lifecycle_phase2_enabled": True,
        })
        app._job_key = "test_key"
        app._start_snapshot = {}
        app._on_print_finish("finish")
        assert _has_log(app, "PRINT_FINISH_SKIP reason=no_start_snapshot")

    def test_on_print_finish_dedup_skip(self):
        """Same job_key processed twice → dedup skip."""
        app = _TestableUsageSync(args={
            "lifecycle_phase1_enabled": True,
            "lifecycle_phase2_enabled": True,
        })
        app._job_key = "dup_key"
        app._start_snapshot = {1: 500.0}
        app._last_processed_job_key = "dup_key"
        app._on_print_finish("finish")
        assert _has_log(app, "PRINT_FINISH_DEDUP_SKIP")

    def test_do_finish_offline_state_warning(self):
        """_do_finish with offline status logs 3MF_SUPPRESSED_NON_SUCCESS."""
        app = _TestableUsageSync(
            state_map=_default_state_map({4: 10}),
            args={
                "lifecycle_phase1_enabled": True,
                "lifecycle_phase2_enabled": True,
            },
        )
        app._job_key = "offline_test"
        app._start_snapshot = {4: 420.0}
        app._trays_used = {4}
        app._print_active = True
        app._state_map.update(_rfid_tag_uid_for_slots(app, [4]))
        app._state_map[app._fuel_gauge_pattern.format(slot=4)] = "370.0"
        app._do_finish("offline")
        assert _has_log(app, "3MF_SUPPRESSED_NON_SUCCESS")

    def test_do_finish_processes_usage(self):
        """_do_finish builds end snapshot and processes usage."""
        app = _TestableUsageSync(
            state_map={
                **_default_state_map({4: 10}),
                "sensor.p1s_tray_4_fuel_gauge_remaining": "370.0",
                "sensor.p1s_01p00c5a3101668_print_weight": "50",
                "sensor.p1s_01p00c5a3101668_task_name": "test.3mf",
            },
            args={
                "lifecycle_phase1_enabled": True,
                "lifecycle_phase2_enabled": True,
            },
        )
        app._job_key = "test_finish"
        app._start_snapshot = {4: 420.0}
        app._trays_used = {4}
        app._state_map.update(_rfid_tag_uid_for_slots(app, [4]))
        app._do_finish("finish")
        assert _has_log(app, "PRINT_FINISH_CAPTURED")
        assert len(app._use_calls) == 1
        assert app._use_calls[0]["spool_id"] == 10


class TestReadFuelGauge:
    """_read_fuel_gauge with fallback."""

    def test_fuel_gauge_returns_value(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_tray_1_fuel_gauge_remaining": "500.0",
        })
        assert app._read_fuel_gauge(1) == 500.0

    def test_fuel_gauge_unavailable_falls_back_to_ams(self):
        app = _TestableUsageSync(state_map={
            "sensor.ams_slot_1_remaining_g": "300.0",
        })
        assert app._read_fuel_gauge(1) == 300.0

    def test_fuel_gauge_both_unavailable(self):
        app = _TestableUsageSync(state_map={})
        assert app._read_fuel_gauge(1) == -1.0

    def test_fuel_gauge_invalid_value(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_tray_1_fuel_gauge_remaining": "unavailable",
        })
        assert app._read_fuel_gauge(1) == -1.0


class TestBuildStartEndSnapshot:
    """_build_start_snapshot and _build_end_snapshot."""

    def test_build_start_snapshot(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_tray_1_fuel_gauge_remaining": "500.0",
            "sensor.p1s_tray_2_fuel_gauge_remaining": "300.0",
        })
        snap = app._build_start_snapshot()
        assert 1 in snap
        assert 2 in snap
        assert snap[1] == 500.0

    def test_build_end_snapshot_only_start_slots(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_tray_1_fuel_gauge_remaining": "450.0",
            "sensor.p1s_tray_2_fuel_gauge_remaining": "280.0",
        })
        app._start_snapshot = {1: 500.0}
        snap = app._build_end_snapshot()
        assert 1 in snap
        assert 2 not in snap


class TestSnapshotPlausibility:
    """Snapshot trust validation — Shape 1: 0.0g on bound+loaded slot."""

    def test_implausible_excluded_bound_slot_zero(self):
        """Bound slot reading 0.0 is excluded from snapshot."""
        app = _TestableUsageSync(state_map={
            "sensor.p1s_tray_2_fuel_gauge_remaining": "0.0",
            "input_text.ams_slot_2_spool_id": "20",
        })
        # Make tray physically present via RFID tag
        app._state_map.update(_rfid_tag_uid_for_slots(app, [2]))
        snap = app._build_start_snapshot()
        assert 2 not in snap, f"implausible slot 2 should be excluded, got {snap}"
        assert _has_log(app, "SNAPSHOT_IMPLAUSIBLE")
        assert _has_log(app, "slot=2")

    def test_valid_kept_unbound_slot_zero(self):
        """Unbound slot reading 0.0 is kept in snapshot."""
        app = _TestableUsageSync(state_map={
            "sensor.p1s_tray_2_fuel_gauge_remaining": "0.0",
            "input_text.ams_slot_2_spool_id": "0",
        })
        snap = app._build_start_snapshot()
        assert 2 in snap, f"unbound slot 2 at 0.0 should stay in snapshot"
        assert snap[2] == 0.0
        assert not _has_log(app, "SNAPSHOT_IMPLAUSIBLE")

    def test_valid_kept_bound_slot_nonzero(self):
        """Bound slot reading 450.0 is kept normally."""
        app = _TestableUsageSync(state_map={
            "sensor.p1s_tray_2_fuel_gauge_remaining": "450.0",
            "input_text.ams_slot_2_spool_id": "20",
        })
        app._state_map.update(_rfid_tag_uid_for_slots(app, [2]))
        snap = app._build_start_snapshot()
        assert 2 in snap
        assert snap[2] == 450.0
        assert not _has_log(app, "SNAPSHOT_IMPLAUSIBLE")

    def test_rehydrate_helper_recovery_removes_stale_zero(self):
        """Stale 0.0 in HA helper JSON is removed during rehydration."""
        app = _TestableUsageSync(
            state_map={
                "sensor.p1s_01p00c5a3101668_print_status": "running",
                "input_text.filament_iq_start_json": '{"2": 0.0, "3": 550.0}',
                "sensor.p1s_01p00c5a3101668_task_name": "test_model",
                "input_text.filament_iq_active_job_key": "",
                "input_text.ams_slot_2_spool_id": "20",
                "input_text.ams_slot_3_spool_id": "30",
            },
            args={"lifecycle_phase1_enabled": True},
        )
        # Slot 2: bound + physically present → 0.0 is implausible
        app._state_map.update(_rfid_tag_uid_for_slots(app, [2]))
        app._rehydrate_print_state()
        assert 2 not in app._start_snapshot, (
            f"stale 0.0 for slot 2 should be removed, got {app._start_snapshot}"
        )
        assert 3 in app._start_snapshot
        assert app._start_snapshot[3] == 550.0
        assert _has_log(app, "SNAPSHOT_IMPLAUSIBLE_REHYDRATE")

    def test_end_to_end_excluded_slot_produces_data_loss(self):
        """Excluded RFID slot produces explicit DATA_LOSS, not silent BELOW_MIN."""
        app = _TestableUsageSync(
            state_map={
                "input_text.ams_slot_2_spool_id": "20",
                "sensor.p1s_tray_2_fuel_gauge_remaining": "280.0",
            },
            args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
        )
        app._state_map.update(_rfid_tag_uid_for_slots(app, [2]))
        app._job_key = "snapshot_e2e_test"
        # Simulate: slot 2 excluded from snapshot (fuel gauge was 0.0 at start)
        app._start_snapshot = {}  # slot 2 excluded
        app._spool_id_snapshot = {2: 20}
        app._trays_used = {2}
        app._print_active = True
        app.threemf_enabled = False

        from conftest import SpoolmanRecorder
        recorder = SpoolmanRecorder()
        app._spoolman_use = recorder.use
        app._spoolman_patch = recorder.patch

        app._do_finish("finish")

        # Slot 2 should NOT be written to Spoolman
        recorder.assert_not_used(20)
        # Should log explicit DATA_LOSS, not BELOW_MIN
        assert _has_log(app, "USAGE_NO_EVIDENCE")
        assert _has_log(app, "DATA_LOSS")
        assert not any("BELOW_MIN" in msg and "slot=2" in msg
                       for msg, _ in app._log_calls)


class TestSeedSlotStartGrams:
    """_seed_slot_start_grams write-once behavior."""

    def test_already_seeded_no_overwrite(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_tray_1_fuel_gauge_remaining": "300.0",
        })
        app._start_snapshot = {1: 500.0}
        app._seed_slot_start_grams(1)
        assert app._start_snapshot[1] == 500.0

    def test_new_slot_seeded(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_tray_3_fuel_gauge_remaining": "800.0",
        })
        app._start_snapshot = {}
        app._seed_slot_start_grams(3)
        assert 3 in app._start_snapshot
        assert app._start_snapshot[3] == 800.0


class TestCheckUnboundTrays:
    """_check_unbound_trays delayed check."""

    def test_not_printing_noop(self):
        app = _TestableUsageSync()
        app._print_active = False
        app._check_unbound_trays({})
        assert not _has_log(app, "PRINT_UNBOUND_WARNING")

    def test_no_trays_skips(self):
        app = _TestableUsageSync()
        app._print_active = True
        app._trays_used = set()
        app._check_unbound_trays({})
        assert _has_log(app, "UNBOUND_CHECK_SKIPPED")

    def test_unbound_tray_warns(self):
        app = _TestableUsageSync(state_map={
            "input_text.ams_slot_1_spool_id": "0",
            "input_text.ams_slot_1_unbound_reason": "UNBOUND_TAG_UID_NO_MATCH",
        })
        app._print_active = True
        app._trays_used = {1}
        app._check_unbound_trays({})
        assert _has_log(app, "PRINT_UNBOUND_WARNING")


class TestRehydratePrintState:
    """_rehydrate_print_state mid-print recovery."""

    def test_not_printing_noop(self):
        app = _TestableUsageSync(
            state_map={"sensor.p1s_01p00c5a3101668_print_status": "idle"},
            args={"lifecycle_phase1_enabled": True},
        )
        app._rehydrate_print_state()
        assert app._print_active is False

    def test_printing_rehydrates(self):
        app = _TestableUsageSync(
            state_map={
                "sensor.p1s_01p00c5a3101668_print_status": "running",
                "input_text.filament_iq_start_json": '{"1": 500.0}',
                "sensor.p1s_01p00c5a3101668_task_name": "test_model",
            },
            args={"lifecycle_phase1_enabled": True},
        )
        app._rehydrate_print_state()
        assert app._print_active is True
        assert 1 in app._start_snapshot
        assert app._start_snapshot[1] == 500.0
        assert _has_log(app, "REHYDRATE_PRINT_ACTIVE")

    def test_rehydrate_rebuilds_when_no_helper(self):
        app = _TestableUsageSync(
            state_map={
                "sensor.p1s_01p00c5a3101668_print_status": "running",
                "sensor.p1s_tray_1_fuel_gauge_remaining": "450.0",
                "sensor.p1s_01p00c5a3101668_task_name": "rebuild_test",
            },
            args={"lifecycle_phase1_enabled": True},
        )
        app._rehydrate_print_state()
        assert app._print_active is True
        assert 1 in app._start_snapshot
        assert _has_log(app, "REHYDRATE_START_SNAPSHOT_REBUILT")

    def test_rehydrate_reads_job_key_from_helper(self):
        """Rehydrate reads full job_key (with timestamp) from HA helper, not task_name."""
        app = _TestableUsageSync(
            state_map={
                "sensor.p1s_01p00c5a3101668_print_status": "running",
                "input_text.filament_iq_start_json": '{"1": 500.0}',
                "sensor.p1s_01p00c5a3101668_task_name": "test_model",
                "input_text.filament_iq_active_job_key": "test_model_1773438415",
            },
            args={"lifecycle_phase1_enabled": True},
        )
        app._rehydrate_print_state()
        assert app._job_key == "test_model_1773438415"
        assert _has_log(app, "REHYDRATE_JOB_KEY_FROM_HELPER")
        # Should NOT overwrite the helper
        helper_writes = [
            c for c in app._service_calls
            if c.get("entity_id") == "input_text.filament_iq_active_job_key"
        ]
        assert len(helper_writes) == 0, "should not overwrite helper when it has the correct key"

    def test_rehydrate_falls_back_to_task_name_when_helper_empty(self):
        """Rehydrate falls back to task_name when helper is empty/unknown."""
        app = _TestableUsageSync(
            state_map={
                "sensor.p1s_01p00c5a3101668_print_status": "running",
                "input_text.filament_iq_start_json": '{"1": 500.0}',
                "sensor.p1s_01p00c5a3101668_task_name": "test model fallback",
                "input_text.filament_iq_active_job_key": "unknown",
            },
            args={"lifecycle_phase1_enabled": True},
        )
        app._rehydrate_print_state()
        assert app._job_key == "test_model_fallback"
        assert _has_log(app, "REHYDRATE_JOB_KEY_FROM_TASK_NAME")

    def test_rehydrate_falls_back_when_helper_missing(self):
        """Rehydrate falls back to task_name when helper entity returns None."""
        app = _TestableUsageSync(
            state_map={
                "sensor.p1s_01p00c5a3101668_print_status": "running",
                "input_text.filament_iq_start_json": '{"1": 500.0}',
                "sensor.p1s_01p00c5a3101668_task_name": "no helper print",
            },
            args={"lifecycle_phase1_enabled": True},
        )
        app._rehydrate_print_state()
        assert app._job_key == "no_helper_print"
        assert _has_log(app, "REHYDRATE_JOB_KEY_FROM_TASK_NAME")

    def test_rehydrate_helper_key_matches_active_print_json(self):
        """Full rehydrate chain: helper key matches active_print.json → threemf_data restored."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                import filament_iq.ams_print_usage_sync as mod
                ap_file = pathlib.Path(tmp_dir) / "active_print.json"
                mod.ACTIVE_PRINT_FILE = ap_file

                threemf = [{"index": 0, "used_g": 100.67}]
                ap_file.write_text(json.dumps({
                    "job_key": "0.28mm_layer,_2_walls,_15%_infill_1773438415",
                    "start_snapshot": {"1": 940.0},
                    "threemf_data": threemf,
                }))

                app = _TestableUsageSync(
                    state_map={
                        "sensor.p1s_01p00c5a3101668_print_status": "running",
                        "input_text.filament_iq_start_json": '{"1": 940.0}',
                        "sensor.p1s_01p00c5a3101668_task_name": "0.28mm layer, 2 walls, 15% infill",
                        "input_text.filament_iq_active_job_key": "0.28mm_layer,_2_walls,_15%_infill_1773438415",
                    },
                    args={"lifecycle_phase1_enabled": True},
                )
                app._rehydrate_print_state()
                assert app._job_key == "0.28mm_layer,_2_walls,_15%_infill_1773438415"
                assert app._threemf_data == threemf
                assert _has_log(app, "ACTIVE_PRINT_RESTORED")
                assert not _has_log(app, "ACTIVE_PRINT_STALE")
            finally:
                mod.ACTIVE_PRINT_FILE = pathlib.Path(tmp_dir) / "active_print.json"


class TestResolveActiveTraySlot:
    """_resolve_active_tray_slot edge cases."""

    def test_returns_none_when_no_indices(self):
        app = _TestableUsageSync()
        assert app._resolve_active_tray_slot() is None

    def test_resolves_correct_slot(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_active_tray::ams_index": "0",
            "sensor.p1s_01p00c5a3101668_active_tray::tray_index": "0",
        })
        result = app._resolve_active_tray_slot()
        assert result == 1


class TestOnPrintStatusChange:
    """_on_print_status_change tracking lifecycle."""

    def test_start_triggers_tracking(self):
        app = _TestableUsageSync(args={"lifecycle_phase1_enabled": True})
        app._on_print_status_change(
            "entity", "state", "idle", "running", {}
        )
        assert app._print_active is True
        assert _has_log(app, "TRAY_TRACKING_START")

    def test_end_triggers_tracking_end(self):
        app = _TestableUsageSync(args={"lifecycle_phase1_enabled": True})
        app._print_active = True
        app._trays_used = {1, 2}
        app._on_print_status_change(
            "entity", "state", "running", "idle", {}
        )
        assert app._print_active is False
        assert _has_log(app, "TRAY_TRACKING_END")


class TestOnPrintFinishSynchronous:
    """_on_print_finish calls _do_finish synchronously."""

    def test_synchronous_finish_no_threemf(self):
        """With threemf_enabled=False, _do_finish called directly."""
        app = _TestableUsageSync(args={
            "lifecycle_phase1_enabled": True,
            "lifecycle_phase2_enabled": True,
        })
        app._job_key = "test_key"
        app._start_snapshot = {1: 500.0}
        app.threemf_enabled = False
        app._do_finish = mock.MagicMock()
        app._on_print_finish("finish")
        app._do_finish.assert_called_once_with("finish")


class TestFilterTraysByDuration:
    """_filter_trays_by_duration removes brief activations."""

    def test_brief_tray_filtered(self):
        import datetime
        app = _TestableUsageSync(args={"min_tray_active_seconds": 10})
        now = datetime.datetime.utcnow()
        app._tray_active_times = {
            1: [{"start": now, "end": now + datetime.timedelta(seconds=5)}],
            2: [{"start": now, "end": now + datetime.timedelta(seconds=60)}],
        }
        result = app._filter_trays_by_duration({1, 2})
        assert 1 not in result
        assert 2 in result


class TestSummarizeTrayTimes:
    """_summarize_tray_times returns {slot: seconds}."""

    def test_summarize(self):
        import datetime
        app = _TestableUsageSync()
        now = datetime.datetime.utcnow()
        app._tray_active_times = {
            1: [{"start": now, "end": now + datetime.timedelta(seconds=120)}],
        }
        result = app._summarize_tray_times()
        assert 1 in result
        assert result[1] == 120.0


class TestWriteStartJsonHelper:
    """_write_start_json_helper writes to HA helper."""

    def test_writes_json(self):
        app = _TestableUsageSync()
        app._start_snapshot = {1: 500.0, 4: 420.0}
        app._write_start_json_helper()
        calls = [c for c in app._service_calls
                 if c["service"] == "input_text/set_value"
                 and c.get("entity_id") == "input_text.filament_iq_start_json"]
        assert len(calls) == 1
        data = json.loads(calls[0]["value"])
        assert data["1"] == 500.0
        assert data["4"] == 420.0


# ── Coverage push: IO helpers, lifecycle, weight reconcile ────────────


class TestReadSpoolId:
    """_read_spool_id returns int or 0."""

    def test_valid_id(self):
        app = _TestableUsageSync(state_map={"input_text.ams_slot_1_spool_id": "42"})
        assert app._read_spool_id(1) == 42

    def test_none_returns_zero(self):
        app = _TestableUsageSync(state_map={"input_text.ams_slot_1_spool_id": None})
        assert app._read_spool_id(1) == 0

    def test_invalid_returns_zero(self):
        app = _TestableUsageSync(state_map={"input_text.ams_slot_1_spool_id": "banana"})
        assert app._read_spool_id(1) == 0

    def test_empty_string_returns_zero(self):
        app = _TestableUsageSync(state_map={"input_text.ams_slot_1_spool_id": ""})
        assert app._read_spool_id(1) == 0


class TestIsRfidSlot:
    """_is_rfid_slot checks tag_uid attribute."""

    def test_valid_rfid_tag(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_ams_1_tray_1::tag_uid": "AABBCCDD11223344",
        })
        assert app._is_rfid_slot(1) is True

    def test_zero_tag_not_rfid(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_ams_1_tray_1::tag_uid": "0000000000000000",
        })
        assert app._is_rfid_slot(1) is False

    def test_none_tag_not_rfid(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_ams_1_tray_1::tag_uid": None,
        })
        assert app._is_rfid_slot(1) is False

    def test_empty_tag_not_rfid(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_ams_1_tray_1::tag_uid": "",
        })
        assert app._is_rfid_slot(1) is False

    def test_unknown_slot_returns_false(self):
        app = _TestableUsageSync()
        assert app._is_rfid_slot(99) is False


class TestIsTrayPhysicallyPresent:
    """_is_tray_physically_present checks tag_uid and tray state."""

    def test_rfid_tag_present(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_ams_1_tray_1::tag_uid": "AABB",
            "sensor.p1s_01p00c5a3101668_ams_1_tray_1": "PLA",
        })
        assert app._is_tray_physically_present(1) is True

    def test_no_tag_but_tray_state_present(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_ams_1_tray_1::tag_uid": "",
            "sensor.p1s_01p00c5a3101668_ams_1_tray_1": "PLA Basic",
        })
        assert app._is_tray_physically_present(1) is True

    def test_empty_tray(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_ams_1_tray_1::tag_uid": "",
            "sensor.p1s_01p00c5a3101668_ams_1_tray_1": "empty",
        })
        assert app._is_tray_physically_present(1) is False

    def test_unknown_slot(self):
        app = _TestableUsageSync()
        assert app._is_tray_physically_present(99) is False


def _mock_urlopen_response(data_bytes):
    """Create a mock that works with `with urllib.request.urlopen(...) as resp:`."""
    resp = mock.MagicMock()
    resp.read.return_value = data_bytes
    resp.status = 200
    cm = mock.MagicMock()
    cm.__enter__ = mock.MagicMock(return_value=resp)
    cm.__exit__ = mock.MagicMock(return_value=False)
    return cm


class TestSpoolmanGetSync:
    """_spoolman_get performs HTTP GET (real implementation, not harness override)."""

    def test_success(self):
        app = _TestableUsageSync()
        cm = _mock_urlopen_response(json.dumps({"id": 1, "remaining_weight": 500}).encode())
        with mock.patch("urllib.request.urlopen", return_value=cm):
            result = AmsPrintUsageSync._spoolman_get(app, "/api/v1/spool/1")
        assert result["id"] == 1

    def test_failure_returns_none(self):
        app = _TestableUsageSync()
        with mock.patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            result = AmsPrintUsageSync._spoolman_get(app, "/api/v1/spool/1")
        assert result is None


class TestGetSpoolDisplayName:
    """_get_spool_display_name formats vendor+name+material."""

    def test_from_cache(self):
        app = _TestableUsageSync()
        cache = {42: {
            "filament": {
                "vendor": {"name": "Bambu"},
                "name": "Basic",
                "material": "PLA",
            },
        }}
        assert AmsPrintUsageSync._get_spool_display_name(app, 42, spools_cache=cache) == "Bambu Basic PLA"

    def test_no_vendor(self):
        app = _TestableUsageSync()
        cache = {42: {"filament": {"vendor": None, "name": "Basic", "material": "PLA"}}}
        assert AmsPrintUsageSync._get_spool_display_name(app, 42, spools_cache=cache) == "Basic PLA"

    def test_cache_miss_with_spoolman_data(self):
        app = _TestableUsageSync()
        # harness _spoolman_get returns {"remaining_weight": 100} (no filament) → empty name
        # Exercise the code path where data exists but has no vendor/name/material
        result = AmsPrintUsageSync._get_spool_display_name(app, 999)
        # No filament key means all parts are empty → stripped to ""
        assert result == ""

    def test_spoolman_returns_none(self):
        app = _TestableUsageSync()
        app._spoolman_get_override = lambda path: None
        assert AmsPrintUsageSync._get_spool_display_name(app, 999) == "spool 999"

    def test_exception_fallback(self):
        app = _TestableUsageSync()
        bad_cache = mock.MagicMock()
        bad_cache.get.side_effect = Exception("boom")
        assert AmsPrintUsageSync._get_spool_display_name(app, 1, spools_cache=bad_cache) == "spool 1"


class TestGetSpoolRemaining:
    """_get_spool_remaining returns float weight."""

    def test_from_cache(self):
        app = _TestableUsageSync()
        cache = {42: {"remaining_weight": 350.5}}
        assert AmsPrintUsageSync._get_spool_remaining(app, 42, spools_cache=cache) == 350.5

    def test_cache_miss_fetches(self):
        app = _TestableUsageSync()
        # harness _spoolman_get returns {"remaining_weight": 100}
        assert AmsPrintUsageSync._get_spool_remaining(app, 999) == 100.0

    def test_exception_returns_zero(self):
        app = _TestableUsageSync()
        bad_cache = mock.MagicMock()
        bad_cache.get.side_effect = Exception("boom")
        assert AmsPrintUsageSync._get_spool_remaining(app, 1, spools_cache=bad_cache) == 0.0


class TestSpoolmanPatchSync:
    """_spoolman_patch performs HTTP PATCH (real implementation)."""

    def test_success(self):
        app = _TestableUsageSync()
        cm = _mock_urlopen_response(json.dumps({"id": 1, "remaining_weight": 400}).encode())
        with mock.patch("urllib.request.urlopen", return_value=cm):
            result = AmsPrintUsageSync._spoolman_patch(app, 1, {"remaining_weight": 400})
        assert result["remaining_weight"] == 400

    def test_failure_returns_none(self):
        app = _TestableUsageSync()
        with mock.patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            result = AmsPrintUsageSync._spoolman_patch(app, 1, {"remaining_weight": 400})
        assert result is None
        assert any("SPOOLMAN_PATCH_FAILED" in msg for msg, _ in app._log_calls)


class TestReconcileRfidWeightsDeferred:
    """_reconcile_rfid_weights is deferred via run_in in _do_finish."""

    def test_do_finish_defers_reconciler(self):
        """_do_finish schedules reconciler via run_in, not synchronously."""
        app = _TestableUsageSync(
            state_map={
                **_default_state_map({4: 10}),
                "sensor.p1s_tray_4_fuel_gauge_remaining": "370.0",
                "sensor.p1s_01p00c5a3101668_print_weight": "50",
                "sensor.p1s_01p00c5a3101668_task_name": "test.3mf",
            },
            args={
                "lifecycle_phase1_enabled": True,
                "lifecycle_phase2_enabled": True,
            },
        )
        app._job_key = "deferred_test"
        app._start_snapshot = {4: 420.0}
        app._trays_used = {4}
        app._state_map.update(_rfid_tag_uid_for_slots(app, [4]))
        app._do_finish("finish")
        # Verify reconciler was scheduled via run_in, not called directly
        deferred_calls = [
            c for c in app._run_in_calls
            if c.get("callback") == app._reconcile_rfid_weights_deferred
        ]
        assert len(deferred_calls) == 1, f"expected 1 deferred call, got {deferred_calls}"
        assert deferred_calls[0]["delay"] == 60
        assert _has_log(app, "RFID_WEIGHT_RECONCILE_DEFERRED")

    def test_deferred_callback_calls_reconciler(self):
        """_reconcile_rfid_weights_deferred calls _reconcile_rfid_weights."""
        app = _TestableUsageSync()
        app._weight_reconcile_enabled = False
        app._reconcile_rfid_weights_deferred({})
        # Should not raise, disabled reconciler is a no-op

    def test_deferred_callback_catches_exception(self):
        """_reconcile_rfid_weights_deferred catches and logs exceptions."""
        app = _TestableUsageSync()
        app._weight_reconcile_enabled = True
        with mock.patch.object(app, "_reconcile_rfid_weights", side_effect=RuntimeError("boom")):
            app._reconcile_rfid_weights_deferred({})
        assert any("RFID_WEIGHT_RECONCILE_ERROR" in msg for msg, _ in app._log_calls)


class TestReconcileRfidWeights:
    """_reconcile_rfid_weights iterates slots."""

    def test_disabled(self):
        app = _TestableUsageSync()
        app._weight_reconcile_enabled = False
        app._reconcile_rfid_weights()  # should not raise

    def test_exception_per_slot_logged(self):
        app = _TestableUsageSync()
        app._weight_reconcile_enabled = True
        with mock.patch.object(app, "_reconcile_rfid_weight_slot", side_effect=Exception("boom")):
            app._reconcile_rfid_weights()
        assert any("RFID_WEIGHT_RECONCILE_SLOT_ERROR" in msg for msg, _ in app._log_calls)


class TestReconcileRfidWeightSlot:
    """_reconcile_rfid_weight_slot full reconciliation logic."""

    def _app_with_rfid(self, slot=1, remain=75, tray_weight=1000, remain_enabled=True,
                       spool_id="42", tag_uid="AABB"):
        tray_entity = "sensor.p1s_01p00c5a3101668_ams_1_tray_1"
        app = _TestableUsageSync(state_map={
            f"{tray_entity}::tag_uid": tag_uid,
            f"{tray_entity}::remain": remain,
            f"{tray_entity}::tray_weight": tray_weight,
            f"{tray_entity}::remain_enabled": remain_enabled,
            f"input_text.ams_slot_{slot}_spool_id": spool_id,
        })
        app._weight_reconcile_enabled = True
        return app

    def test_non_rfid_slot_skips(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_ams_1_tray_1::tag_uid": "",
        })
        app._reconcile_rfid_weight_slot(1)  # no error

    def test_unbound_slot_skips(self):
        app = self._app_with_rfid(spool_id="0")
        app._reconcile_rfid_weight_slot(1)  # no error

    def test_remain_disabled_skips(self):
        app = self._app_with_rfid(remain_enabled=False)
        app._reconcile_rfid_weight_slot(1)

    def test_tray_weight_zero_skips(self):
        app = self._app_with_rfid(tray_weight=0)
        app._reconcile_rfid_weight_slot(1)

    def test_remain_out_of_range_skips(self):
        app = self._app_with_rfid(remain=150)
        app._reconcile_rfid_weight_slot(1)
        assert any("RFID_WEIGHT_INVALID_REMAIN" in msg for msg, _ in app._log_calls)

    def test_remain_negative_skips(self):
        app = self._app_with_rfid(remain=-5)
        app._reconcile_rfid_weight_slot(1)
        assert any("RFID_WEIGHT_INVALID_REMAIN" in msg for msg, _ in app._log_calls)

    def test_weights_within_threshold_no_patch(self):
        app = self._app_with_rfid(remain=50, tray_weight=1000)
        # rfid_weight = 500g; spoolman = 503g -> delta 3g < 5g threshold -> no patch
        app._spoolman_get_override = lambda path: {"remaining_weight": 503.0}
        app._reconcile_rfid_weight_slot(1)
        assert not app._patch_calls

    def test_dry_run_no_patch(self):
        app = self._app_with_rfid(remain=50, tray_weight=1000)
        app.dry_run = True
        # rfid=500g < spoolman=600g -> downward direction, would patch, but dry_run
        app._spoolman_get_override = lambda path: {"remaining_weight": 600.0}
        app._reconcile_rfid_weight_slot(1)
        assert not app._patch_calls
        assert any("DRYRUN" in msg for msg, _ in app._log_calls)

    def test_actual_patch(self):
        app = self._app_with_rfid(remain=50, tray_weight=1000)
        # rfid=500g < spoolman=600g -> downward correction, delta=100g > 5g -> patch
        app._spoolman_get_override = lambda path: {"remaining_weight": 600.0}
        app._reconcile_rfid_weight_slot(1)
        assert len(app._patch_calls) == 1
        assert app._patch_calls[0]["data"]["remaining_weight"] == 500.0

    def test_spoolman_fetch_fails(self):
        app = self._app_with_rfid(remain=50, tray_weight=1000)
        app._spoolman_get_override = lambda path: None
        app._reconcile_rfid_weight_slot(1)
        assert any("spoolman_fetch_failed" in msg for msg, _ in app._log_calls)

    def test_patch_failure_logged(self):
        app = self._app_with_rfid(remain=50, tray_weight=1000)
        # rfid=500g < spoolman=600g -> downward, delta=100g -> would patch
        app._spoolman_get_override = lambda path: {"remaining_weight": 600.0}
        # Override _spoolman_patch to return None (simulating failure)
        app._spoolman_patch = lambda sid, data: None
        app._reconcile_rfid_weight_slot(1)
        assert any("RFID_WEIGHT_RECONCILE_FAILED" in msg for msg, _ in app._log_calls)

    def test_remain_string_converted(self):
        """remain as string '75' gets converted to float."""
        app = self._app_with_rfid(remain="75", tray_weight=1000)
        # rfid=750g < spoolman=850g -> downward, delta=100g -> patch
        app._spoolman_get_override = lambda path: {"remaining_weight": 850.0}
        app._reconcile_rfid_weight_slot(1)
        assert len(app._patch_calls) == 1
        assert app._patch_calls[0]["data"]["remaining_weight"] == 750.0

    def test_remain_invalid_string_skips(self):
        app = self._app_with_rfid(remain="banana", tray_weight=1000)
        app._reconcile_rfid_weight_slot(1)
        assert any("RFID_WEIGHT_INVALID_REMAIN" in msg for msg, _ in app._log_calls)


class TestReconcilerHardening:
    """Tests for reconciler hardening guards (v0.12.4)."""

    def _app_with_rfid(self, slot=1, remain=75, tray_weight=1000,
                       remain_enabled=True, spool_id="42", tag_uid="AABB"):
        tray_entity = "sensor.p1s_01p00c5a3101668_ams_1_tray_1"
        app = _TestableUsageSync(state_map={
            f"{tray_entity}::tag_uid": tag_uid,
            f"{tray_entity}::remain": remain,
            f"{tray_entity}::tray_weight": tray_weight,
            f"{tray_entity}::remain_enabled": remain_enabled,
            f"input_text.ams_slot_{slot}_spool_id": spool_id,
        })
        app._weight_reconcile_enabled = True
        return app

    # ── Fix 1: print_active guard in deferred callback ──

    def test_deferred_redefers_when_print_active(self):
        """If print_active=True when deferred fires, re-defer 60s."""
        app = self._app_with_rfid()
        app._print_active = True
        app._reconcile_rfid_weights_deferred({})
        assert _has_log(app, "RFID_WEIGHT_RECONCILE_DEFERRED_PRINT_ACTIVE")
        redefer = [c for c in app._run_in_calls
                   if c.get("callback") == app._reconcile_rfid_weights_deferred]
        assert len(redefer) == 1
        assert redefer[0]["delay"] == 60

    def test_deferred_proceeds_when_not_printing(self):
        """If print_active=False, deferred fires normally."""
        app = self._app_with_rfid()
        app._print_active = False
        app._spoolman_get_override = lambda path: {"remaining_weight": 900.0}
        app._reconcile_rfid_weights_deferred({})
        assert not _has_log(app, "RFID_WEIGHT_RECONCILE_DEFERRED_PRINT_ACTIVE")
        # Should have attempted reconciliation (patch or match)
        assert any("RFID_WEIGHT" in msg for msg, _ in app._log_calls)

    # ── Fix 2: directional guard removed — RFID is ground truth ──

    def test_upward_correction_allowed(self):
        """rfid_weight > spoolman_weight → patch proceeds (RFID is ground truth)."""
        app = self._app_with_rfid(remain=80, tray_weight=1000)
        # rfid=800g > spoolman=700g → upward correction allowed
        app._spoolman_get_override = lambda path: {"remaining_weight": 700.0}
        app._reconcile_rfid_weight_slot(1)
        assert len(app._patch_calls) == 1
        assert app._patch_calls[0]["data"]["remaining_weight"] == 800.0

    def test_downward_correction_allowed(self):
        """rfid_weight < spoolman_weight → patch proceeds."""
        app = self._app_with_rfid(remain=50, tray_weight=1000)
        # rfid=500g < spoolman=600g → downward correction
        app._spoolman_get_override = lambda path: {"remaining_weight": 600.0}
        app._reconcile_rfid_weight_slot(1)
        assert len(app._patch_calls) == 1
        assert app._patch_calls[0]["data"]["remaining_weight"] == 500.0

    # ── Fix 3: tray_weight sanity bounds ──

    def test_tray_weight_too_large_blocked(self):
        """tray_weight=10000g → skip (exceeds 2000g cap)."""
        app = self._app_with_rfid(tray_weight=10000)
        app._reconcile_rfid_weight_slot(1)
        assert not app._patch_calls
        assert _has_log(app, "RFID_WEIGHT_SKIP_TRAY_WEIGHT")

    def test_tray_weight_too_small_blocked(self):
        """tray_weight=5g → skip (below 50g minimum)."""
        app = self._app_with_rfid(tray_weight=5)
        app._reconcile_rfid_weight_slot(1)
        assert not app._patch_calls
        assert _has_log(app, "RFID_WEIGHT_SKIP_TRAY_WEIGHT")

    def test_tray_weight_at_min_bound_passes(self):
        """tray_weight=50g → passes sanity check."""
        app = self._app_with_rfid(remain=50, tray_weight=50)
        # rfid=25g < spoolman=30g → downward, delta=5g → patch
        app._spoolman_get_override = lambda path: {"remaining_weight": 30.0}
        app._reconcile_rfid_weight_slot(1)
        assert len(app._patch_calls) == 1

    def test_tray_weight_at_max_bound_passes(self):
        """tray_weight=2000g → passes sanity check."""
        app = self._app_with_rfid(remain=50, tray_weight=2000)
        # rfid=1000g < spoolman=1100g → downward, delta=100g → patch
        app._spoolman_get_override = lambda path: {"remaining_weight": 1100.0}
        app._reconcile_rfid_weight_slot(1)
        assert len(app._patch_calls) == 1

    # ── Fix 4: minimum delta threshold ──

    def test_delta_below_threshold_no_patch(self):
        """rfid=500g, spoolman=503g → delta=3g < 5g → no patch."""
        app = self._app_with_rfid(remain=50, tray_weight=1000)
        app._spoolman_get_override = lambda path: {"remaining_weight": 503.0}
        app._reconcile_rfid_weight_slot(1)
        assert not app._patch_calls
        assert _has_log(app, "RFID_WEIGHT_MATCH")

    def test_delta_at_threshold_no_patch(self):
        """rfid=500g, spoolman=504.9g → delta=4.9g < 5g → no patch."""
        app = self._app_with_rfid(remain=50, tray_weight=1000)
        app._spoolman_get_override = lambda path: {"remaining_weight": 504.9}
        app._reconcile_rfid_weight_slot(1)
        assert not app._patch_calls

    def test_delta_above_threshold_patches(self):
        """rfid=500g, spoolman=510g → delta=10g > 5g, downward → patch."""
        app = self._app_with_rfid(remain=50, tray_weight=1000)
        app._spoolman_get_override = lambda path: {"remaining_weight": 510.0}
        app._reconcile_rfid_weight_slot(1)
        assert len(app._patch_calls) == 1
        assert app._patch_calls[0]["data"]["remaining_weight"] == 500.0


class TestLoadSeenJobKeys:
    """_load_seen_job_keys loads dedup state from JSON."""

    def test_load_list(self):
        import tempfile as _tf
        app = _TestableUsageSync()
        with _tf.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(["key1", "key2"], f)
            f.flush()
            with mock.patch("filament_iq.ams_print_usage_sync.SEEN_JOBS_PATH", f.name):
                result = app._load_seen_job_keys()
        assert list(result.keys()) == ["key1", "key2"]
        os.unlink(f.name)

    def test_load_dict(self):
        import tempfile as _tf
        app = _TestableUsageSync()
        with _tf.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"k1": True, "k2": True}, f)
            f.flush()
            with mock.patch("filament_iq.ams_print_usage_sync.SEEN_JOBS_PATH", f.name):
                result = app._load_seen_job_keys()
        assert "k1" in result
        os.unlink(f.name)

    def test_missing_file(self):
        app = _TestableUsageSync()
        with mock.patch("filament_iq.ams_print_usage_sync.SEEN_JOBS_PATH", "/nonexistent/path.json"):
            result = app._load_seen_job_keys()
        assert len(result) == 0

    def test_invalid_json(self):
        import tempfile as _tf
        app = _TestableUsageSync()
        with _tf.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json{{{")
            f.flush()
            with mock.patch("filament_iq.ams_print_usage_sync.SEEN_JOBS_PATH", f.name):
                result = app._load_seen_job_keys()
        assert len(result) == 0
        assert any("could not load" in msg for msg, _ in app._log_calls)
        os.unlink(f.name)

    def test_overflow_trimmed(self):
        import tempfile as _tf
        app = _TestableUsageSync()
        keys = [f"key_{i}" for i in range(100)]
        with _tf.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(keys, f)
            f.flush()
            with mock.patch("filament_iq.ams_print_usage_sync.SEEN_JOBS_PATH", f.name):
                result = app._load_seen_job_keys()
        assert len(result) == 50  # MAX_SEEN_JOBS
        os.unlink(f.name)


class TestEnsureDataDir:
    """_ensure_data_dir creates directory and file."""

    def test_creates_file(self):
        import tempfile as _tf
        with _tf.TemporaryDirectory() as td:
            path = os.path.join(td, "data", "seen.json")
            with mock.patch("filament_iq.ams_print_usage_sync.SEEN_JOBS_PATH", path):
                app = _TestableUsageSync()
                app._ensure_data_dir()
            assert os.path.isfile(path)

    def test_oserror_logged(self):
        app = _TestableUsageSync()
        with mock.patch("filament_iq.ams_print_usage_sync.SEEN_JOBS_PATH", "/proc/fake/path.json"):
            app._ensure_data_dir()
        assert any("could not ensure" in msg for msg, _ in app._log_calls)


class TestCheckUnboundTrays:
    """_check_unbound_trays warns about unbound active slots."""

    def test_not_active_skips(self):
        app = _TestableUsageSync()
        app._print_active = False
        app._check_unbound_trays({})
        assert not any("UNBOUND" in msg for msg, _ in app._log_calls)

    def test_no_trays_skips(self):
        app = _TestableUsageSync()
        app._print_active = True
        app._trays_used = set()
        app._check_unbound_trays({})

    def test_unbound_slot_warns(self):
        app = _TestableUsageSync(state_map={
            "input_text.ams_slot_1_spool_id": "0",
            "input_text.ams_slot_1_unbound_reason": "NEEDS_ACTION",
        })
        app._print_active = True
        app._trays_used = {1}
        app._check_unbound_trays({})
        assert any("PRINT_UNBOUND_WARNING" in msg for msg, _ in app._log_calls)
        assert any(c["service"] == "notify/mobile_app_jd_pixel_10_pro_xl" for c in app._service_calls)

    def test_unbound_with_notify_target(self):
        """notify_target arg is now ignored; always routes to mobile app."""
        app = _TestableUsageSync(state_map={
            "input_text.ams_slot_1_spool_id": "0",
            "input_text.ams_slot_1_unbound_reason": "NEEDS_ACTION",
        }, args={"notify_target": "mobile_app_phone"})
        app._print_active = True
        app._trays_used = {1}
        app._check_unbound_trays({})
        assert any(c["service"] == "notify/mobile_app_jd_pixel_10_pro_xl" for c in app._service_calls)

    def test_bound_slot_no_warning(self):
        app = _TestableUsageSync(state_map={
            "input_text.ams_slot_1_spool_id": "42",
        })
        app._print_active = True
        app._trays_used = {1}
        app._check_unbound_trays({})
        assert not any("PRINT_UNBOUND_WARNING" in msg for msg, _ in app._log_calls)


class TestOnPrintFinish:
    """_on_print_finish lifecycle handler."""

    def test_no_job_key_skips(self):
        app = _TestableUsageSync()
        app._lifecycle_phase2 = True
        app._job_key = ""
        app._on_print_finish("finish")
        assert any("no_job_key" in msg for msg, _ in app._log_calls)

    def test_no_start_snapshot_skips(self):
        app = _TestableUsageSync()
        app._lifecycle_phase2 = True
        app._job_key = "test_job_123"
        app._start_snapshot = {}
        app._on_print_finish("finish")
        assert any("no_start_snapshot" in msg for msg, _ in app._log_calls)

    def test_dedup_skips(self):
        app = _TestableUsageSync()
        app._lifecycle_phase2 = True
        app._job_key = "test_job_123"
        app._start_snapshot = {1: 500.0}
        app._last_processed_job_key = "test_job_123"
        app._on_print_finish("finish")
        assert any("DEDUP_SKIP" in msg for msg, _ in app._log_calls)

    def test_threemf_unavailable_logs_warning(self):
        """3MF not in memory and no disk → logs 3MF_UNAVAILABLE, still calls _do_finish."""
        app = _TestableUsageSync()
        app._lifecycle_phase2 = True
        app._job_key = "test_job_123"
        app._start_snapshot = {1: 500.0}
        app.threemf_enabled = True
        app._threemf_data = None
        app._do_finish = mock.MagicMock()
        app._on_print_finish("finish")
        assert _has_log(app, "3MF_UNAVAILABLE_AT_FINISH")
        app._do_finish.assert_called_once_with("finish")

    def test_direct_do_finish(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_task_name": "benchy",
            "sensor.p1s_01p00c5a3101668_print_weight": "15.0",
            "input_text.ams_slot_1_spool_id": "10",
        })
        app._lifecycle_phase2 = True
        app._job_key = "benchy_123"
        app._start_snapshot = {1: 500.0}
        app._trays_used = {1}
        app._print_active = True
        app._state_map.update(_rfid_tag_uid_for_slots(app, [1]))
        app._state_map[app._fuel_gauge_pattern.format(slot=1)] = "450.0"
        app.threemf_enabled = False
        app._on_print_finish("finish")
        assert any("USAGE_SUMMARY" in msg for msg, _ in app._log_calls)


class TestOnPrintStatusChange:
    """_on_print_status_change transitions."""

    def test_start_transition(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_active_tray": "unknown",
        })
        app._on_print_status_change(
            "sensor.p1s_01p00c5a3101668_print_status", "state", "idle", "running", {}
        )
        assert app._print_active is True
        assert any("TRAY_TRACKING_START" in msg for msg, _ in app._log_calls)

    def test_end_transition(self):
        app = _TestableUsageSync()
        app._print_active = True
        app._trays_used = {1}
        app._current_active_slot = 1
        import datetime
        app._tray_active_times = {1: [{"start": datetime.datetime.utcnow(), "end": None}]}
        app._on_print_status_change(
            "sensor.p1s_01p00c5a3101668_print_status", "state", "running", "finish", {}
        )
        assert app._print_active is False
        assert any("TRAY_TRACKING_END" in msg for msg, _ in app._log_calls)

    def test_end_helper_write_failure(self):
        """Helper write failure is caught."""
        app = _TestableUsageSync()
        app._print_active = True
        app._trays_used = {1}
        app._current_active_slot = None
        app._tray_active_times = {}
        # Make call_service raise for the trays_used write
        original_call = app.call_service
        def failing_call(service, **kw):
            if "trays_used" in kw.get("entity_id", ""):
                raise Exception("HA unavailable")
            original_call(service, **kw)
        app.call_service = failing_call
        app._on_print_status_change(
            "sensor.p1s_01p00c5a3101668_print_status", "state", "running", "finish", {}
        )
        assert any("Failed to update HA helper" in msg for msg, _ in app._log_calls)


class TestSpoolmanUseSync:
    """_spoolman_use sends PUT request."""

    def test_success(self):
        app = _TestableUsageSync()
        cm = _mock_urlopen_response(json.dumps({"id": 1, "remaining_weight": 400}).encode())
        # The inner resp needs .status = 200
        cm.__enter__.return_value.status = 200
        with mock.patch("urllib.request.urlopen", return_value=cm):
            from filament_iq.ams_print_usage_sync import AmsPrintUsageSync
            result = AmsPrintUsageSync._spoolman_use(app, 1, 50.0)
        assert result["remaining_weight"] == 400

    def test_failure(self):
        app = _TestableUsageSync()
        with mock.patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            from filament_iq.ams_print_usage_sync import AmsPrintUsageSync
            result = AmsPrintUsageSync._spoolman_use(app, 1, 50.0)
        assert result is None
        assert any("USAGE_PATCH_FAILED" in msg for msg, _ in app._log_calls)


class TestOnPrintEnd:
    """_on_print_end clears state and turns off print_active."""

    def test_clears_state(self):
        app = _TestableUsageSync()
        app._lifecycle_phase1 = True
        app._start_snapshot = {1: 500.0}
        app._job_key = "test_123"
        app._on_print_end()
        assert app._start_snapshot == {}
        assert app._job_key == ""
        assert any(
            c["service"] == "input_boolean/turn_off" and "print_active" in c.get("entity_id", "")
            for c in app._service_calls
        )


class TestDoFinish:
    """_do_finish executes the full finish flow."""

    def test_offline_state_warning(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_task_name": "benchy",
            "sensor.p1s_01p00c5a3101668_print_weight": "15",
            "input_text.ams_slot_1_spool_id": "10",
        })
        app._lifecycle_phase2 = True
        app._lifecycle_phase1 = True
        app._job_key = "benchy_123"
        app._start_snapshot = {1: 500.0}
        app._trays_used = {1}
        app._print_active = True
        app._state_map.update(_rfid_tag_uid_for_slots(app, [1]))
        app._state_map[app._fuel_gauge_pattern.format(slot=1)] = "450.0"
        app._do_finish("offline")
        assert any("3MF_SUPPRESSED_NON_SUCCESS" in msg for msg, _ in app._log_calls)

    def test_non_failed_stamps_dedup(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_task_name": "benchy",
            "sensor.p1s_01p00c5a3101668_print_weight": "15",
            "input_text.ams_slot_1_spool_id": "10",
        })
        app._lifecycle_phase2 = True
        app._lifecycle_phase1 = True
        app._job_key = "benchy_123"
        app._start_snapshot = {1: 500.0}
        app._trays_used = {1}
        app._print_active = True
        app._state_map.update(_rfid_tag_uid_for_slots(app, [1]))
        app._state_map[app._fuel_gauge_pattern.format(slot=1)] = "450.0"
        app._do_finish("finish")
        assert app._last_processed_job_key == "benchy_123"

    def test_failed_does_not_stamp_dedup(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_task_name": "benchy",
            "sensor.p1s_01p00c5a3101668_print_weight": "15",
        })
        app._lifecycle_phase2 = True
        app._lifecycle_phase1 = True
        app._job_key = "benchy_123"
        app._start_snapshot = {1: 500.0}
        app._do_finish("failed")
        assert app._last_processed_job_key == ""


class TestActiveSlotsNarrowing:
    """_do_finish narrows active slots to trays_used ∩ start_snapshot."""

    def test_start_map_fallback_no_phantom_writes(self):
        """Only slot 2 in trays_used — slots 1,3,4,5,6 must not produce writes."""
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_task_name": "narrow_test",
            "sensor.p1s_01p00c5a3101668_print_weight": "20",
            "input_text.ams_slot_2_spool_id": "42",
        })
        app._lifecycle_phase1 = True
        app._lifecycle_phase2 = True
        app._job_key = "narrow_123"
        app._print_active = True
        # Start snapshot has all 6 slots (as it would in production)
        app._start_snapshot = {s: 500.0 for s in range(1, 7)}
        # But only slot 2 was actually used
        app._trays_used = {2}
        # Give slot 2 fuel gauge end reading (non-RFID, no gauge drift issue)
        app._state_map[app._fuel_gauge_pattern.format(slot=2)] = "480.0"
        # Give all other RFID slots gauge drift (would produce phantom writes)
        for s in [1, 3, 4, 5, 6]:
            app._state_map[f"input_text.ams_slot_{s}_spool_id"] = str(s + 100)
            app._state_map.update(_rfid_tag_uid_for_slots(app, [s]))
            app._state_map[app._fuel_gauge_pattern.format(slot=s)] = "495.0"
        app._do_finish("finish")
        # Only slot 2 should have been processed — check no writes for other slots
        written_slots = {c["spool_id"] for c in app._use_calls}
        # Slots 1,3,4,5,6 spool_ids are 101,103,104,105,106
        phantom_ids = {101, 103, 104, 105, 106}
        assert written_slots & phantom_ids == set(), (
            f"Phantom writes to idle slots: {written_slots & phantom_ids}"
        )

    def test_empty_trays_used_produces_no_evidence(self):
        """Empty trays_used at finish → USAGE_SKIP, no writes."""
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_task_name": "empty_trays",
            "sensor.p1s_01p00c5a3101668_print_weight": "10",
        })
        app._lifecycle_phase1 = True
        app._lifecycle_phase2 = True
        app._job_key = "empty_123"
        app._print_active = True
        app._start_snapshot = {1: 500.0, 2: 400.0}
        app._trays_used = set()
        app._do_finish("finish")
        assert _has_log(app, "USAGE_SKIP")
        assert _has_log(app, "NO_ACTIVE_SLOTS")
        assert len(app._use_calls) == 0

    def test_empty_trays_used_rehydrated_warns(self):
        """Rehydrated print with empty trays_used → WARNING logged."""
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_task_name": "rehydrated_empty",
            "sensor.p1s_01p00c5a3101668_print_weight": "10",
        })
        app._lifecycle_phase1 = True
        app._lifecycle_phase2 = True
        app._job_key = "rehydrated_123"
        app._print_active = True
        app._rehydrated = True
        app._start_snapshot = {1: 500.0}
        app._trays_used = set()
        app._do_finish("finish")
        assert any(
            "NO_ACTIVE_SLOTS" in msg and "rehydrated=True" in msg
            for msg, level in app._log_calls
            if level == "WARNING"
        )
        assert len(app._use_calls) == 0

    def test_rehydrated_trays_used_restored(self):
        """After rehydrate from active_print.json, _trays_used is restored."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                import filament_iq.ams_print_usage_sync as mod
                ap_file = pathlib.Path(tmp_dir) / "active_print.json"
                mod.ACTIVE_PRINT_FILE = ap_file

                ap_file.write_text(json.dumps({
                    "job_key": "rehydrate_test",
                    "start_snapshot": {"1": 500.0, "5": 200.0},
                    "trays_used": [1, 5],
                    "spool_id_snapshot": {"1": 61, "5": 29},
                    "threemf_data": None,
                }))

                app = _TestableUsageSync(state_map={
                    "sensor.p1s_01p00c5a3101668_print_status": "running",
                    "sensor.p1s_01p00c5a3101668_task_name": "rehydrate_test",
                    "input_text.filament_iq_active_job_key": "rehydrate_test",
                    "input_boolean.filament_iq_print_active": "on",
                }, args={"lifecycle_phase1_enabled": True})
                app._rehydrate_print_state()
                assert app._trays_used == {1, 5}
                assert app._spool_id_snapshot == {1: 61, 5: 29}
            finally:
                orig = getattr(mod, '_ACTIVE_PRINT_FILE_ORIG', None)
                if orig:
                    mod.ACTIVE_PRINT_FILE = orig
                elif ap_file.exists():
                    ap_file.unlink()

    def test_rehydrate_restores_print_start_time(self):
        """_print_start_time is restored from active_print.json on rehydrate."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                import filament_iq.ams_print_usage_sync as mod
                ap_file = pathlib.Path(tmp_dir) / "active_print.json"
                mod.ACTIVE_PRINT_FILE = ap_file

                ap_file.write_text(json.dumps({
                    "job_key": "start_time_test",
                    "start_snapshot": {"1": 500.0},
                    "trays_used": [1],
                    "spool_id_snapshot": {"1": 61},
                    "print_start_time": 1710700000.0,
                    "threemf_data": None,
                }))

                app = _TestableUsageSync(state_map={
                    "sensor.p1s_01p00c5a3101668_print_status": "running",
                    "sensor.p1s_01p00c5a3101668_task_name": "start_time_test",
                    "input_text.filament_iq_active_job_key": "start_time_test",
                    "input_boolean.filament_iq_print_active": "on",
                }, args={"lifecycle_phase1_enabled": True})
                app._rehydrate_print_state()
                assert app._print_start_time == 1710700000.0
            finally:
                orig = getattr(mod, '_ACTIVE_PRINT_FILE_ORIG', None)
                if orig:
                    mod.ACTIVE_PRINT_FILE = orig
                elif ap_file.exists():
                    ap_file.unlink()

    def test_duration_unknown_when_start_time_missing(self):
        """Missing print_start_time from disk leaves _print_start_time as None."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                import filament_iq.ams_print_usage_sync as mod
                ap_file = pathlib.Path(tmp_dir) / "active_print.json"
                mod.ACTIVE_PRINT_FILE = ap_file

                ap_file.write_text(json.dumps({
                    "job_key": "no_start_time",
                    "start_snapshot": {"1": 500.0},
                    "threemf_data": None,
                }))

                app = _TestableUsageSync(state_map={
                    "sensor.p1s_01p00c5a3101668_print_status": "running",
                    "sensor.p1s_01p00c5a3101668_task_name": "no_start_time",
                    "input_text.filament_iq_active_job_key": "no_start_time",
                    "input_boolean.filament_iq_print_active": "on",
                }, args={"lifecycle_phase1_enabled": True})
                app._print_start_time = None
                app._rehydrate_print_state()
                assert app._print_start_time is None
            finally:
                orig = getattr(mod, '_ACTIVE_PRINT_FILE_ORIG', None)
                if orig:
                    mod.ACTIVE_PRINT_FILE = orig
                elif ap_file.exists():
                    ap_file.unlink()


class TestExecuteWritesDepletedNonrfid:
    """_execute_writes depleted_nonrfid location PATCH."""

    def _make_decision(self, slot=1, spool_id=61, consumption_g=200.0,
                       method="depleted_nonrfid"):
        from filament_iq.consumption_engine import SlotDecision
        return SlotDecision(
            slot=slot, spool_id=spool_id, consumption_g=consumption_g,
            method=method, skip_reason=None, confidence="high",
        )

    def test_depleted_nonrfid_sets_location_empty(self):
        """After depleted_nonrfid write, PATCH location=Empty on the spool."""
        app = _TestableUsageSync()
        # Spoolman returns remaining > 0 (stale data), so decision.depleted stays False
        app._use_remaining_override = {61: 50.0}
        decision = self._make_decision()
        app._execute_writes([decision], "test_job")
        # /use call should have happened
        assert len(app._use_calls) == 1
        # location PATCH should have happened via the depleted_nonrfid path
        location_patches = [
            p for p in app._patch_calls
            if p["data"] == {"location": "Empty"} and p["spool_id"] == 61
        ]
        assert len(location_patches) == 1
        assert _has_log(app, "NONRFID_DEPLETED_LOCATION_SET")

    def test_depleted_nonrfid_location_patch_failure_does_not_fail_write(self):
        """If location PATCH fails, the overall result is still success."""
        app = _TestableUsageSync()
        app._use_remaining_override = {61: 50.0}
        # Make _spoolman_patch raise
        def _failing_patch(spool_id, data):
            raise RuntimeError("Spoolman down")
        app._spoolman_patch = _failing_patch
        decision = self._make_decision()
        decisions, patched, failed = app._execute_writes([decision], "test_job")
        assert patched == 1
        assert failed == 0
        assert _has_log(app, "NONRFID_DEPLETED_LOCATION_FAILED")

    def test_non_depleted_nonrfid_method_no_location_patch(self):
        """3mf method should not trigger the depleted_nonrfid location PATCH."""
        app = _TestableUsageSync()
        app._use_remaining_override = {61: 50.0}
        decision = self._make_decision(method="3mf")
        app._execute_writes([decision], "test_job")
        location_patches = [
            p for p in app._patch_calls
            if p["data"] == {"location": "Empty"}
        ]
        assert len(location_patches) == 0
        assert not _has_log(app, "NONRFID_DEPLETED_LOCATION_SET")


class TestOnActiveTrayChange:
    """_on_active_tray_change tracks active slot."""

    def test_not_printing_skips(self):
        app = _TestableUsageSync()
        app._print_active = False
        app._on_active_tray_change("sensor.p1s_01p00c5a3101668_active_tray", "state", "none", "PLA", {})
        assert not app._trays_used

    def test_slot_change_during_print(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_active_tray::ams_index": 0,
            "sensor.p1s_01p00c5a3101668_active_tray::tray_index": 0,
        })
        app._print_active = True
        app._current_active_slot = None
        app._on_active_tray_change("sensor.p1s_01p00c5a3101668_active_tray", "state", "none", "PLA", {})
        assert 1 in app._trays_used
        assert app._current_active_slot == 1

    def test_slot_change_to_unavailable(self):
        import datetime
        app = _TestableUsageSync()
        app._print_active = True
        app._current_active_slot = 1
        app._tray_active_times = {1: [{"start": datetime.datetime.utcnow(), "end": None}]}
        app._on_active_tray_change("sensor.p1s_01p00c5a3101668_active_tray", "state", "PLA", "unavailable", {})
        assert app._current_active_slot is None

    def test_slot_switch(self):
        import datetime
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_active_tray::ams_index": 0,
            "sensor.p1s_01p00c5a3101668_active_tray::tray_index": 1,
        })
        app._print_active = True
        app._current_active_slot = 1
        app._tray_active_times = {1: [{"start": datetime.datetime.utcnow(), "end": None}]}
        app._on_active_tray_change("sensor.p1s_01p00c5a3101668_active_tray", "state", "PLA", "PETG", {})
        assert 2 in app._trays_used
        assert app._current_active_slot == 2


class TestGetAccessCode:
    """_get_access_code reads from args or HA entity."""

    def test_from_args(self):
        app = _TestableUsageSync(args={"printer_access_code": "12345678"})
        assert app._get_access_code() == "12345678"

    def test_from_entity(self):
        app = _TestableUsageSync(state_map={
            "input_text.bambu_printer_access_code": "87654321",
        })
        assert app._get_access_code() == "87654321"

    def test_unavailable_returns_none(self):
        app = _TestableUsageSync(state_map={
            "input_text.bambu_printer_access_code": "unavailable",
        })
        assert app._get_access_code() is None

    def test_empty_returns_none(self):
        app = _TestableUsageSync(state_map={
            "input_text.bambu_printer_access_code": "",
        })
        assert app._get_access_code() is None


class TestRehydratePrintState:
    """_rehydrate_print_state restores state mid-print."""

    def test_not_running_skips(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_print_status": "idle",
        })
        app._rehydrate_print_state()
        assert app._print_active is False

    def test_running_activates(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_print_status": "running",
            "sensor.p1s_01p00c5a3101668_active_tray": "unknown",
        })
        app._rehydrate_print_state()
        assert app._print_active is True

    def test_phase1_recovers_from_helper(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_print_status": "running",
            "sensor.p1s_01p00c5a3101668_active_tray": "unknown",
            "input_text.filament_iq_start_json": '{"1": 500.0, "4": 400.0}',
            "sensor.p1s_01p00c5a3101668_task_name": "benchy",
        })
        app._lifecycle_phase1 = True
        app._rehydrate_print_state()
        assert app._start_snapshot == {1: 500.0, 4: 400.0}
        assert app._rehydrated is True

    def test_phase1_rebuilds_when_empty(self):
        app = _TestableUsageSync(state_map={
            "sensor.p1s_01p00c5a3101668_print_status": "running",
            "sensor.p1s_01p00c5a3101668_active_tray": "unknown",
            "input_text.filament_iq_start_json": "{}",
            "sensor.p1s_01p00c5a3101668_task_name": "benchy",
        })
        app._lifecycle_phase1 = True
        app._rehydrate_print_state()
        assert app._rehydrated is True
        assert any("REBUILT" in msg for msg, _ in app._log_calls)


class TestOnSpoolIdChange:
    """_on_spool_id_change during active print."""

    def test_not_phase3_skips(self):
        app = _TestableUsageSync()
        app._lifecycle_phase3 = False
        app._on_spool_id_change("input_text.ams_slot_1_spool_id", "state", "0", "42", {})
        assert not any("SPOOL_ID_CHANGED_DURING_PRINT" in msg for msg, _ in app._log_calls)

    def test_not_printing_skips(self):
        app = _TestableUsageSync()
        app._lifecycle_phase3 = True
        app._print_active = False
        app._on_spool_id_change("input_text.ams_slot_1_spool_id", "state", "0", "42", {})
        assert not any("SPOOL_ID_CHANGED_DURING_PRINT" in msg for msg, _ in app._log_calls)

    def test_logs_during_print(self):
        app = _TestableUsageSync()
        app._lifecycle_phase3 = True
        app._print_active = True
        app._on_spool_id_change("input_text.ams_slot_1_spool_id", "state", "0", "42", {})
        assert any("SPOOL_ID_CHANGED_DURING_PRINT" in msg for msg, _ in app._log_calls)

    def test_startup_suppressed(self):
        import datetime
        app = _TestableUsageSync()
        app._lifecycle_phase3 = True
        app._print_active = True
        app._startup_suppress_until = datetime.datetime.utcnow() + datetime.timedelta(seconds=60)
        app._on_spool_id_change("input_text.ams_slot_1_spool_id", "state", "0", "42", {})
        assert not any("SPOOL_ID_CHANGED_DURING_PRINT" in msg for msg, _ in app._log_calls)


class TestFetchSpoolsCache:
    """_fetch_spools_cache batch fetches from Spoolman."""

    def test_returns_dict(self):
        app = _TestableUsageSync()
        app._spoolman_get_override = lambda path: [
            {"id": 1, "remaining_weight": 500},
            {"id": 2, "remaining_weight": 300},
        ]
        cache = app._fetch_spools_cache()
        assert isinstance(cache, dict)

    def test_failure_returns_empty(self):
        app = _TestableUsageSync()
        app._spoolman_get_override = lambda path: None
        cache = app._fetch_spools_cache()
        assert cache == {}


class TestBuildSlotData:
    """_build_slot_data reads per-slot info."""

    def test_returns_slot_data(self):
        app = _TestableUsageSync(state_map={
            "input_text.ams_slot_1_spool_id": "42",
            "sensor.p1s_01p00c5a3101668_ams_1_tray_1::tag_uid": "AABB",
            "sensor.p1s_01p00c5a3101668_ams_1_tray_1": "PLA Basic",
        })
        app._spoolman_get_override = lambda path: {"id": 42, "remaining_weight": 500, "filament": {"color_hex": "FF0000", "material": "PLA"}}
        data = app._build_slot_data()
        assert 1 in data
        assert data[1]["spool_id"] == 42


# ── _collect_print_inputs tests ──────────────────────────────────────


class TestCollectPrintInputs:
    """Tests for _collect_print_inputs method."""

    def test_3mf_suppressed_for_rfid_slot(self):
        """RFID slot with 3MF match → SlotInput.threemf_used_g is None."""
        app = _TestableUsageSync(
            state_map={
                "input_text.ams_slot_1_spool_id": "10",
            },
            args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
        )
        app._state_map.update(_rfid_tag_uid_for_slots(app, [1]))
        app._state_map["sensor.p1s_tray_1_fuel_gauge_remaining"] = "800.0"
        inputs = app._collect_print_inputs(
            trays_used={1},
            start_snapshot={1: 900.0},
            end_snapshot={1: 800.0},
            threemf_matched_slots={1: (120.0, "exact_color_material")},
            spools_cache={},
        )
        assert len(inputs) == 1
        assert inputs[0].threemf_used_g is None
        assert inputs[0].threemf_method is None

    def test_non_rfid_tray_empty_fetches_spoolman_remaining(self):
        """Non-RFID slot, tray_empty=True → spoolman_remaining populated."""
        app = _TestableUsageSync(
            state_map={"input_text.ams_slot_3_spool_id": "30"},
            args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
        )
        entity = app._tray_entity_by_slot[3]
        app._state_map[entity] = "Empty"
        app._state_map[f"{entity}::tag_uid"] = ""
        inputs = app._collect_print_inputs(
            trays_used={3},
            start_snapshot={},
            end_snapshot={},
            threemf_matched_slots={},
            spools_cache={30: {"remaining_weight": 432.0}},
        )
        assert len(inputs) == 1
        assert inputs[0].spoolman_remaining == 432.0

    def test_non_rfid_not_empty_no_spoolman_fetch(self):
        """Non-RFID slot, tray not empty → spoolman_remaining is None."""
        app = _TestableUsageSync(
            state_map={"input_text.ams_slot_3_spool_id": "30"},
            args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
        )
        entity = app._tray_entity_by_slot[3]
        app._state_map[f"{entity}::tag_uid"] = ""
        inputs = app._collect_print_inputs(
            trays_used={3},
            start_snapshot={},
            end_snapshot={},
            threemf_matched_slots={},
            spools_cache={},
        )
        assert len(inputs) == 1
        assert inputs[0].spoolman_remaining is None

    def test_3mf_retry_schedule_uses_new_delays(self):
        """Verify retry_delays == [15, 45, 90]."""
        import filament_iq.ams_print_usage_sync as mod
        source = open(mod.__file__).read()
        assert "retry_delays = [15, 45, 90]" in source



