#!/usr/bin/env python3
"""
Tests for filament_weight_tracker — spool weight delta tracking.
Run: python -m pytest tests/test_filament_weight_tracker.py -v
"""

import datetime
import json
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

_APPS = os.path.join(os.path.dirname(__file__), "..", "apps")
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
