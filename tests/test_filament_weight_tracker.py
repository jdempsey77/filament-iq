#!/usr/bin/env python3
"""
Tests for filament_weight_tracker — spool weight delta tracking.
Run: python -m pytest tests/test_filament_weight_tracker.py -v
"""

import datetime
import io
import json
import os
import sys
import tempfile
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

from filament_iq.filament_weight_tracker import FilamentWeightTracker


# ── test harness ──────────────────────────────────────────────────────

_DEFAULT_TEST_ARGS = {
    "printer_serial": "01p00c5a3101668",
    "printer_model": "p1s",
    "spoolman_url": "http://192.0.2.1:7912",
    "report_path": "/dev/null",
}


class _TestableTracker(FilamentWeightTracker):
    """FilamentWeightTracker with mocked I/O."""

    def __init__(self, args=None, before_weights=None, after_weights=None):
        a = dict(_DEFAULT_TEST_ARGS)
        a.update(args or {})
        super().__init__(None, "test_tracker", None, a, None, None, None)
        self._log_calls = []
        self._service_calls = []
        self._written_reports = []
        self._weight_sequence = []
        if before_weights is not None:
            self._weight_sequence.append(before_weights)
        if after_weights is not None:
            self._weight_sequence.append(after_weights)
        self._weight_call_idx = 0

        # Initialize state (normally done in initialize())
        self.spoolman_url = str(a.get("spoolman_url", "")).rstrip("/")
        self.report_path = str(a.get("report_path", "/dev/null"))
        self._before_snapshot = None
        self._before_timestamp = None
        self._print_name = None
        self._operator_status_entity = "sensor.filament_iq_operator_status"
        self._weight_snapshot_button_entity = "input_button.filament_iq_weight_snapshot_now"
        self._print_name_entities = []

    def initialize(self):
        pass

    def log(self, msg, level="INFO"):
        self._log_calls.append((msg, level))

    def call_service(self, service, **kwargs):
        self._service_calls.append({"service": service, **kwargs})

    def listen_state(self, *a, **kw):
        pass

    def run_in(self, callback, delay, **kw):
        pass

    def get_state(self, entity_id, attribute=None):
        return None

    def _get_all_spool_weights(self):
        """Return weights from sequence: first call = before, second = after."""
        if self._weight_call_idx < len(self._weight_sequence):
            result = self._weight_sequence[self._weight_call_idx]
            self._weight_call_idx += 1
            return result
        return None

    def _append_report(self, report):
        """Mock: capture reports instead of writing to disk."""
        self._written_reports.append(report)


def _has_log(app, substring):
    return any(substring in msg for msg, _ in app._log_calls)


def _weights(spool_data):
    """Build weight dict from {spool_id: remaining_weight} mapping."""
    result = {}
    for sid, weight in spool_data.items():
        result[sid] = {
            "remaining_weight": weight,
            "filament_name": f"Filament_{sid}",
            "material": "PLA",
            "vendor": "Test",
            "location": "AMS1_Slot1",
        }
    return result


# ── R2 #2: Weight Tracker tests ──────────────────────────────────────

class TestWeightTrackedOnFinish:
    """Weight deltas recorded correctly on print finish."""

    def test_single_spool_consumption(self):
        """One spool used 50g → report shows 50g consumed."""
        before = _weights({1: 500.0, 2: 800.0})
        after = _weights({1: 450.0, 2: 800.0})
        app = _TestableTracker(before_weights=before, after_weights=after)

        app._take_before_snapshot(reason="print_start")
        assert app._before_snapshot is not None

        app._take_after_snapshot_and_report(reason="print_end")
        assert len(app._written_reports) == 1
        report = app._written_reports[0]
        assert report["total_consumed_g"] == 50.0
        assert len(report["spool_deltas"]) == 1
        assert report["spool_deltas"][0]["spool_id"] == 1
        assert report["spool_deltas"][0]["consumed_g"] == 50.0

    def test_multiple_spools_independent(self):
        """Two spools consumed different amounts → both tracked independently."""
        before = _weights({1: 500.0, 2: 800.0, 3: 1000.0})
        after = _weights({1: 450.0, 2: 770.0, 3: 1000.0})
        app = _TestableTracker(before_weights=before, after_weights=after)

        app._take_before_snapshot(reason="print_start")
        app._take_after_snapshot_and_report(reason="print_end")

        report = app._written_reports[0]
        assert report["total_consumed_g"] == 80.0
        deltas = {d["spool_id"]: d["consumed_g"] for d in report["spool_deltas"]}
        assert deltas[1] == 50.0
        assert deltas[2] == 30.0
        assert 3 not in deltas  # unchanged spool not in deltas

    def test_no_consumption_empty_deltas(self):
        """No weight changes → report has zero consumed, empty deltas."""
        before = _weights({1: 500.0})
        after = _weights({1: 500.0})
        app = _TestableTracker(before_weights=before, after_weights=after)

        app._take_before_snapshot(reason="print_start")
        app._take_after_snapshot_and_report(reason="print_end")

        report = app._written_reports[0]
        assert report["total_consumed_g"] == 0.0
        assert len(report["spool_deltas"]) == 0


class TestWeightNotTrackedOnFailure:
    """No report when Spoolman is unavailable."""

    def test_before_snapshot_fails_gracefully(self):
        """If Spoolman unreachable for before snapshot, no crash."""
        app = _TestableTracker()  # No weight sequence = returns None
        result = app._take_before_snapshot(reason="print_start")
        assert result is False
        assert app._before_snapshot is None
        assert _has_log(app, "Before snapshot FAILED")

    def test_no_before_snapshot_skips_report(self):
        """If no before snapshot exists, after snapshot skips gracefully."""
        after = _weights({1: 450.0})
        app = _TestableTracker(after_weights=after)
        # Don't take before snapshot — go straight to after
        app._take_after_snapshot_and_report(reason="print_end")
        assert len(app._written_reports) == 0
        assert _has_log(app, "No before snapshot")

    def test_after_snapshot_fails_gracefully(self):
        """If Spoolman unreachable for after snapshot, no crash."""
        before = _weights({1: 500.0})
        app = _TestableTracker(before_weights=before)
        # before_weights consumed, after returns None
        app._take_before_snapshot(reason="print_start")
        app._take_after_snapshot_and_report(reason="print_end")
        assert len(app._written_reports) == 0
        assert _has_log(app, "After snapshot FAILED")


class TestSnapshotClearedAfterReport:
    """Before snapshot is cleared after generating a report."""

    def test_snapshot_cleared(self):
        """_before_snapshot reset to None after report generated."""
        before = _weights({1: 500.0})
        after = _weights({1: 450.0})
        app = _TestableTracker(before_weights=before, after_weights=after)

        app._take_before_snapshot(reason="print_start")
        app._take_after_snapshot_and_report(reason="print_end")

        assert app._before_snapshot is None
        assert app._before_timestamp is None
        assert app._print_name is None


# ── callback and lifecycle tests ─────────────────────────────────────

class TestOnPrintStart:
    """_on_print_start callback behavior."""

    def test_on_print_start_takes_snapshot(self):
        """_on_print_start with new!=old takes before snapshot."""
        before = _weights({1: 500.0})
        app = _TestableTracker(before_weights=before)
        app._on_print_start("entity", "state", "idle", "printing_normally", {})
        assert app._before_snapshot is not None

    def test_on_print_start_same_state_noop(self):
        """_on_print_start with old==new is a no-op."""
        before = _weights({1: 500.0})
        app = _TestableTracker(before_weights=before)
        app._on_print_start("entity", "state", "printing", "printing", {})
        assert app._before_snapshot is None


class TestOnPrintEnd:
    """_on_print_end callback behavior."""

    def test_on_print_end_same_state_noop(self):
        """_on_print_end with old==new is a no-op."""
        app = _TestableTracker()
        app._on_print_end("entity", "state", "idle", "idle", {})
        # No run_in call captured (run_in is mocked as no-op)
        assert not _has_log(app, "report")

    def test_delayed_after_snapshot(self):
        """_delayed_after_snapshot delegates to _take_after_snapshot_and_report."""
        before = _weights({1: 500.0})
        after = _weights({1: 450.0})
        app = _TestableTracker(before_weights=before, after_weights=after)
        app._take_before_snapshot(reason="test")
        app._delayed_after_snapshot({"reason": "print_end"})
        assert len(app._written_reports) == 1
        assert app._written_reports[0]["reason"] == "print_end"

    def test_delayed_after_snapshot_default_reason(self):
        """_delayed_after_snapshot with no reason kwarg defaults to 'auto'."""
        before = _weights({1: 500.0})
        after = _weights({1: 450.0})
        app = _TestableTracker(before_weights=before, after_weights=after)
        app._take_before_snapshot(reason="test")
        app._delayed_after_snapshot({})
        assert app._written_reports[0]["reason"] == "auto"


class TestOnManualSnapshot:
    """_on_manual_snapshot toggle behavior."""

    def test_manual_first_press_takes_before(self):
        """First button press takes before snapshot + notification."""
        before = _weights({1: 500.0})
        app = _TestableTracker(before_weights=before)
        app._on_manual_snapshot("entity", "state", None, "pressed", {})
        assert app._before_snapshot is not None
        notif = [c for c in app._service_calls
                 if c["service"] == "persistent_notification/create"]
        assert len(notif) == 1
        assert "Before snapshot" in notif[0]["message"]

    def test_manual_second_press_generates_report(self):
        """Second button press (before exists) generates delta report + notification."""
        before = _weights({1: 500.0})
        after = _weights({1: 450.0})
        app = _TestableTracker(before_weights=before, after_weights=after)
        app._on_manual_snapshot("entity", "state", None, "pressed_1", {})
        app._on_manual_snapshot("entity", "state", "pressed_1", "pressed_2", {})
        assert len(app._written_reports) == 1
        notif = [c for c in app._service_calls
                 if c["service"] == "persistent_notification/create"]
        assert len(notif) == 2
        assert "Delta report" in notif[1]["message"]

    def test_manual_same_state_noop(self):
        """Same old==new triggers no action."""
        app = _TestableTracker()
        app._on_manual_snapshot("entity", "state", "pressed", "pressed", {})
        assert app._before_snapshot is None

    def test_manual_new_is_none_noop(self):
        """new=None triggers no action."""
        app = _TestableTracker()
        app._on_manual_snapshot("entity", "state", "old", None, {})
        assert app._before_snapshot is None

    def test_manual_before_fails_no_notification(self):
        """If Spoolman unreachable, before snapshot fails — no notification."""
        app = _TestableTracker()  # No weights = returns None
        app._on_manual_snapshot("entity", "state", None, "pressed", {})
        assert app._before_snapshot is None
        notif = [c for c in app._service_calls
                 if c["service"] == "persistent_notification/create"]
        assert len(notif) == 0


class TestGetPrintName:
    """_get_print_name extracts name from entity attributes."""

    def test_returns_file_attribute(self):
        """If entity has 'file' attribute, returns it."""
        app = _TestableTracker()
        app._print_name_entities = ["sensor.printer_stage"]
        _original_get = app.get_state
        def _mock_get(entity_id, attribute=None):
            if entity_id == "sensor.printer_stage" and attribute == "all":
                return {"attributes": {"file": "benchy.3mf"}}
            return None
        app.get_state = _mock_get
        assert app._get_print_name() == "benchy.3mf"

    def test_returns_subtask_name(self):
        """Falls back to subtask_name if file is empty."""
        app = _TestableTracker()
        app._print_name_entities = ["sensor.printer_stage"]
        def _mock_get(entity_id, attribute=None):
            if entity_id == "sensor.printer_stage" and attribute == "all":
                return {"attributes": {"file": "", "subtask_name": "my_print"}}
            return None
        app.get_state = _mock_get
        assert app._get_print_name() == "my_print"

    def test_returns_unknown_when_no_entities(self):
        """No entities configured → returns 'unknown_print'."""
        app = _TestableTracker()
        app._print_name_entities = []
        assert app._get_print_name() == "unknown_print"

    def test_handles_exception_gracefully(self):
        """Entity access raises → returns 'unknown_print'."""
        app = _TestableTracker()
        app._print_name_entities = ["sensor.broken"]
        def _mock_get(entity_id, attribute=None):
            raise RuntimeError("not available")
        app.get_state = _mock_get
        assert app._get_print_name() == "unknown_print"


class TestWeightEdgeCases:
    """Edge cases in weight tracking."""

    def test_spool_disappears_after_print(self):
        """Spool in before but not in after → not in deltas."""
        before = _weights({1: 500.0, 2: 800.0})
        after = _weights({1: 450.0})  # spool 2 gone
        app = _TestableTracker(before_weights=before, after_weights=after)
        app._take_before_snapshot(reason="test")
        app._take_after_snapshot_and_report(reason="test")
        report = app._written_reports[0]
        assert report["total_consumed_g"] == 50.0
        assert len(report["spool_deltas"]) == 1

    def test_negative_delta_tracked(self):
        """Weight increased (refill) → negative delta recorded."""
        before = _weights({1: 500.0})
        after = _weights({1: 600.0})  # refilled
        app = _TestableTracker(before_weights=before, after_weights=after)
        app._take_before_snapshot(reason="test")
        app._take_after_snapshot_and_report(reason="test")
        report = app._written_reports[0]
        assert report["total_consumed_g"] == -100.0
        assert report["spool_deltas"][0]["consumed_g"] == -100.0


# ── real _get_all_spool_weights tests (mocked HTTP) ──────────────────

class _RealIOTracker(FilamentWeightTracker):
    """Tracker that uses real _get_all_spool_weights and _append_report (no override)."""

    def __init__(self, args=None):
        a = dict(_DEFAULT_TEST_ARGS)
        a.update(args or {})
        super().__init__(None, "test_tracker", None, a, None, None, None)
        self._log_calls = []
        self.spoolman_url = str(a.get("spoolman_url", "")).rstrip("/")
        self.report_path = str(a.get("report_path", "/dev/null"))
        self._before_snapshot = None
        self._before_timestamp = None
        self._print_name = None
        self._operator_status_entity = "sensor.filament_iq_operator_status"
        self._weight_snapshot_button_entity = "input_button.filament_iq_weight_snapshot_now"
        self._print_name_entities = []

    def initialize(self):
        pass

    def log(self, msg, level="INFO"):
        self._log_calls.append((msg, level))

    def call_service(self, service, **kwargs):
        pass

    def listen_state(self, *a, **kw):
        pass

    def run_in(self, callback, delay, **kw):
        pass

    def get_state(self, entity_id, attribute=None):
        return None


class TestRealGetAllSpoolWeights:
    """Test _get_all_spool_weights with mocked HTTP responses."""

    def test_normal_spool_list(self):
        """Standard list response → dict of spool weights."""
        app = _RealIOTracker()
        response_data = [
            {"id": 1, "remaining_weight": 500.0, "location": "AMS1_Slot1",
             "filament": {"name": "PLA", "material": "PLA", "vendor": {"name": "Bambu"}}},
            {"id": 2, "remaining_weight": 300.0, "location": "AMS1_Slot2",
             "filament": {"name": "PETG", "material": "PETG", "vendor": {"name": "Overture"}}},
        ]
        with mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__ = lambda s: io.BytesIO(json.dumps(response_data).encode())
            m.return_value.__exit__ = mock.Mock(return_value=False)
            result = app._get_all_spool_weights()
        assert result is not None
        assert 1 in result
        assert 2 in result
        assert result[1]["remaining_weight"] == 500.0

    def test_items_key_response(self):
        """Paginated response with 'items' key."""
        app = _RealIOTracker()
        response_data = {
            "items": [
                {"id": 1, "remaining_weight": 500.0, "location": "",
                 "filament": {"name": "PLA", "material": "PLA", "vendor": {"name": "Bambu"}}},
            ]
        }
        with mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__ = lambda s: io.BytesIO(json.dumps(response_data).encode())
            m.return_value.__exit__ = mock.Mock(return_value=False)
            result = app._get_all_spool_weights()
        assert result is not None
        assert 1 in result

    def test_invalid_response_format(self):
        """Non-list, non-dict → None."""
        app = _RealIOTracker()
        with mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__ = lambda s: io.BytesIO(b'"just a string"')
            m.return_value.__exit__ = mock.Mock(return_value=False)
            result = app._get_all_spool_weights()
        assert result is None
        assert any("Unexpected response" in msg for msg, _ in app._log_calls)

    def test_connection_error(self):
        """HTTP error → None returned."""
        app = _RealIOTracker()
        with mock.patch("urllib.request.urlopen", side_effect=ConnectionError("refused")):
            result = app._get_all_spool_weights()
        assert result is None
        assert any("Failed to fetch" in msg for msg, _ in app._log_calls)

    def test_spool_with_zero_id_skipped(self):
        """Spool with id=0 or missing id → skipped."""
        app = _RealIOTracker()
        response_data = [
            {"id": 0, "remaining_weight": 100.0, "filament": {}},
            {"id": 1, "remaining_weight": 500.0, "location": "",
             "filament": {"name": "PLA", "material": "PLA", "vendor": {"name": "Bambu"}}},
        ]
        with mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__ = lambda s: io.BytesIO(json.dumps(response_data).encode())
            m.return_value.__exit__ = mock.Mock(return_value=False)
            result = app._get_all_spool_weights()
        assert 0 not in result
        assert 1 in result


class TestRealAppendReport:
    """Test _append_report with real file I/O."""

    def test_append_report_writes_json(self, tmp_path):
        """Report appended as JSON line."""
        report_file = tmp_path / "reports.log"
        app = _RealIOTracker(args={"report_path": str(report_file)})
        report = {"total_consumed_g": 50.0, "spool_deltas": []}
        app._append_report(report)
        lines = report_file.read_text().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["total_consumed_g"] == 50.0

    def test_append_report_write_failure(self):
        """Unwritable path → error logged, no crash."""
        app = _RealIOTracker(args={"report_path": "/nonexistent/dir/report.log"})
        app._append_report({"total_consumed_g": 0})
        assert any("Failed to write" in msg for msg, _ in app._log_calls)
