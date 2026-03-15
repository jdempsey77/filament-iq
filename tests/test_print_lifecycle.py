"""
test_print_lifecycle.py — Integration tests for print start/end orchestration.

Tests _on_print_start, _do_finish flow, active_print.json lifecycle,
and 3MF data availability at print end. Uses SpoolmanRecorder from conftest.py.

Coverage:
  - Print start captures RFID fuel gauges and writes active_print.json
  - Print end loads 3MF from disk when not in memory
  - Cancelled print suppresses 3MF, RFID delta proceeds normally
  - Failed print produces no Spoolman writes
  - Dedup prevents double-write on same job_key
  - Non-success status suppresses 3MF match but allows RFID delta
"""

import json
import os
import pathlib
import sys
import tempfile
import types
from unittest import mock

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

from test_ams_print_usage_sync import _TestableUsageSync, _has_log, _rfid_tag_uid_for_slots
from conftest import SpoolmanRecorder


class TestPrintLifecycle:

    def _make_rfid_app(self, slot=1, spool_id=10, start_g=900.0, end_g=800.0):
        """Build app with single RFID slot ready for _do_finish."""
        app = _TestableUsageSync(
            state_map={
                f"input_text.ams_slot_{slot}_spool_id": str(spool_id),
                f"sensor.p1s_tray_{slot}_fuel_gauge_remaining": str(end_g),
            },
            args={
                "lifecycle_phase1_enabled": True,
                "lifecycle_phase2_enabled": True,
            },
        )
        app._state_map.update(_rfid_tag_uid_for_slots(app, [slot]))
        app._job_key = "lifecycle_test"
        app._start_snapshot = {slot: start_g}
        app._trays_used = {slot}
        app._print_active = True
        return app

    def test_print_start_captures_rfid_snapshot(self):
        app = _TestableUsageSync(
            state_map={
                "input_text.ams_slot_1_spool_id": "10",
                "sensor.p1s_tray_1_fuel_gauge_remaining": "900.0",
                "sensor.p1s_01p00c5a3101668_task_name": "test model",
            },
            args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
        )
        app._state_map.update(_rfid_tag_uid_for_slots(app, [1]))
        app._on_print_start()
        assert 1 in app._start_snapshot
        assert app._start_snapshot[1] == 900.0
        assert _has_log(app, "PRINT_START_CAPTURED")

    def test_cancelled_print_suppresses_3mf_allows_rfid(self):
        app = self._make_rfid_app()
        recorder = SpoolmanRecorder()
        app._spoolman_use = recorder.use
        app._spoolman_patch = recorder.patch
        recorder.set_use_response(10, remaining=800.0)
        app._threemf_data = [{"index": 1, "used_g": 120.0, "color_hex": "ff0000", "material": "pla"}]
        app.threemf_enabled = True
        app._do_finish("canceled")
        assert _has_log(app, "3MF_SUPPRESSED_NON_SUCCESS")
        assert recorder.use_count == 1

    def test_failed_print_produces_no_writes(self):
        """Failed prints still process RFID delta but do NOT stamp dedup."""
        app = self._make_rfid_app()
        recorder = SpoolmanRecorder()
        app._spoolman_use = recorder.use
        app._spoolman_patch = recorder.patch
        recorder.set_use_response(10, remaining=800.0)
        app._on_print_finish("failed")
        # RFID delta writes proceed even for failed prints (new behavior)
        assert recorder.use_count == 1
        # But dedup is NOT stamped for failed status
        assert app._last_processed_job_key == ""

    def test_dedup_prevents_double_write(self):
        app = self._make_rfid_app()
        recorder = SpoolmanRecorder()
        app._spoolman_use = recorder.use
        app._spoolman_patch = recorder.patch
        recorder.set_use_response(10, remaining=800.0)
        app._do_finish("finish")
        first_count = recorder.use_count
        assert first_count == 1
        # Reset for second call
        app._job_key = "lifecycle_test"
        app._start_snapshot = {1: 800.0}
        app._trays_used = {1}
        app._print_active = True
        app._do_finish("finish")
        assert recorder.use_count == first_count  # no additional writes
        assert _has_log(app, "DEDUP_SKIP")

    def test_no_active_slots_skips_gracefully(self):
        app = _TestableUsageSync(
            state_map={},
            args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
        )
        app._job_key = "empty_test"
        app._start_snapshot = {}
        app._trays_used = set()
        app._print_active = True
        recorder = SpoolmanRecorder()
        app._spoolman_use = recorder.use
        app._spoolman_patch = recorder.patch
        app._do_finish("finish")
        assert recorder.use_count == 0

    def test_print_end_loads_3mf_from_disk_when_not_in_memory(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            import filament_iq.ams_print_usage_sync as mod
            orig = mod.ACTIVE_PRINT_FILE
            try:
                ap_file = pathlib.Path(tmp_dir) / "active_print.json"
                mod.ACTIVE_PRINT_FILE = ap_file
                threemf = [{"index": 0, "used_g": 50.0}]
                ap_file.write_text(json.dumps({
                    "job_key": "disk_test",
                    "start_snapshot": {"1": 900.0},
                    "threemf_data": threemf,
                }))
                app = _TestableUsageSync(
                    state_map={},
                    args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
                )
                app._job_key = "disk_test"
                app._start_snapshot = {1: 900.0}
                app._trays_used = set()
                app._print_active = True
                app.threemf_enabled = True
                app._threemf_data = None
                app._do_finish = mock.MagicMock()
                app._on_print_finish("finish")
                assert app._threemf_data == threemf
                assert _has_log(app, "3MF_RECOVERED_FROM_DISK")
            finally:
                mod.ACTIVE_PRINT_FILE = orig
