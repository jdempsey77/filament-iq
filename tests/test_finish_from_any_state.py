#!/usr/bin/env python3
"""
test_finish_from_any_state.py — a finish must be seen from any previous state.

Bug: end-of-print handling only ran when the previous state was `running` or
`printing`. A terminal state reached from `pause`, `paused`, `offline`,
`unknown` or `unavailable` (pause then cancel; a print that ends while the
printer is offline) was ignored and the job stayed open, so the next print was
absorbed into it or superseded it.

Also covers: following a task name that arrives after the print start, the
rehydrate rule for the recorded task name, and the 61-second-dropout case end
to end on a non-RFID slot.
"""

import time

import pytest

from conftest import SpoolmanRecorder
from test_ams_print_usage_sync import _TestableUsageSync, _has_log
from test_dropout_tracking import (
    _alerts,
    _backdate_open_segment,
    _clear_active_tray,
    _make_app,
    _set_active_tray,
    _status,
)


def _recorder(app, spool_id=10, remaining=800.0):
    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(spool_id, remaining=remaining)
    return recorder


def _count_log(app, token):
    return sum(1 for msg, _ in app._log_calls if token in msg)


# ── terminal state from a non-running state ───────────────────────────


def test_pause_then_failed_closes_job_and_writes_rfid_delta(tmp_path):
    """Pause, then cancel: `pause → failed` must run the finish path."""
    app = _make_app(tmp_path)
    recorder = _recorder(app, remaining=850.0)
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")
    _backdate_open_segment(app, 1, 1200)

    _status(app, "running", "pause")
    assert app._job_key, "a pause keeps the job open"

    app._state_map["sensor.p1s_tray_1_fuel_gauge_remaining"] = "850.0"
    _status(app, "pause", "failed")

    assert _has_log(app, "PRINT_FINISH_FROM_NON_RUNNING")
    assert _has_log(app, "TRAY_TRACKING_END")
    assert recorder.use_count == 1
    recorder.assert_used(spool_id=10, weight=50.0)
    assert app._job_key == "", "the job is closed"
    assert app._print_active is False
    assert app._tray_active_times[1][-1]["end"] is not None, "open segment closed"


@pytest.mark.parametrize("gap_state", ["offline", "unknown", "unavailable"])
def test_finish_while_unreachable_records_slots_tracked_before(tmp_path, gap_state):
    """The print ends while the printer is offline: `offline → finish`."""
    app = _make_app(tmp_path)
    recorder = _recorder(app, remaining=800.0)
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")
    _backdate_open_segment(app, 1, 3000)

    _status(app, "running", gap_state)
    assert app._job_key and app._trays_used == {1}

    app._state_map["sensor.p1s_tray_1_fuel_gauge_remaining"] = "800.0"
    _status(app, gap_state, "finish")

    assert recorder.use_count == 1
    recorder.assert_used(spool_id=10, weight=100.0)
    assert app._job_key == ""
    assert _alerts(app) == []


def test_print_after_pause_then_cancel_gets_fresh_key_and_no_supersede(tmp_path):
    app = _make_app(tmp_path, task="bracket_v2 Plate 1")
    _recorder(app)
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")
    _status(app, "running", "pause")
    _status(app, "pause", "failed")
    app._log_calls.clear()
    app._service_calls.clear()

    app._state_map[app._task_name_entity] = "hinge_r3 Plate 2"
    _set_active_tray(app, 0, 3)
    _status(app, "failed", "prepare")
    _status(app, "prepare", "running")

    assert _has_log(app, "PRINT_START_CAPTURED")
    assert app._job_key.startswith("hinge_r3_Plate_2_")
    assert app._trays_used == {4}
    assert not _has_log(app, "PRINT_STATUS_SPURIOUS_SKIP"), "not absorbed"
    assert not _has_log(app, "PRINT_JOB_SUPERSEDED")
    assert _alerts(app) == []


def test_repeated_terminal_event_is_processed_once(tmp_path):
    app = _make_app(tmp_path)
    recorder = _recorder(app, remaining=800.0)
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")
    _backdate_open_segment(app, 1, 3000)
    _status(app, "running", "offline")
    app._state_map["sensor.p1s_tray_1_fuel_gauge_remaining"] = "800.0"

    _status(app, "offline", "finish")
    _status(app, "unavailable", "finish")
    _status(app, "offline", "finish")
    _status(app, "finish", "finish")

    assert recorder.use_count == 1
    assert _count_log(app, "PRINT_FINISH_FROM_NON_RUNNING") == 1
    assert _count_log(app, "PRINT_FINISH_CAPTURED") == 1
    assert not _has_log(app, "PRINT_FINISH_SKIP")


def test_terminal_event_with_no_open_job_does_nothing(tmp_path):
    """HA restart with an idle printer: `unavailable → finish`, nothing open."""
    app = _make_app(tmp_path)
    _status(app, "unavailable", "finish")

    assert not _has_log(app, "PRINT_FINISH_FROM_NON_RUNNING")
    assert not _has_log(app, "TRAY_TRACKING_END")
    assert app._use_calls == []


def test_running_to_finish_still_finishes_once(tmp_path):
    """Regression: the normal path is unchanged and does not run twice."""
    app = _make_app(tmp_path)
    recorder = _recorder(app, remaining=800.0)
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")
    _backdate_open_segment(app, 1, 3000)
    app._state_map["sensor.p1s_tray_1_fuel_gauge_remaining"] = "800.0"

    _status(app, "running", "finish")

    assert recorder.use_count == 1
    assert not _has_log(app, "PRINT_FINISH_FROM_NON_RUNNING")


# ── task name that arrives late ───────────────────────────────────────


def test_late_task_name_then_reconnect_is_the_same_job(tmp_path):
    """The sensor still names the previous print when the job starts."""
    app = _make_app(tmp_path, task="previous_print Plate 1")
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")
    job_key = app._job_key
    assert app._job_task_name == "previous_print Plate 1"

    # The real name lands a few seconds into the print.
    app._state_map[app._task_name_entity] = "bracket_v2 Plate 1"
    app._on_task_name_change(
        app._task_name_entity, None, "previous_print Plate 1", "bracket_v2 Plate 1", {}
    )
    assert app._job_task_name == "bracket_v2 Plate 1"
    assert _has_log(app, "JOB_TASK_NAME_UPDATED")

    _status(app, "running", "offline")
    _status(app, "offline", "running")

    assert app._job_key == job_key
    assert not _has_log(app, "PRINT_JOB_SUPERSEDED")
    assert _has_log(app, "TRAY_TRACKING_RESUMED")
    assert _alerts(app) == []


def test_task_name_is_not_followed_while_tracking_is_off(tmp_path):
    """During a dropout a changed name is evidence of a different print."""
    app = _make_app(tmp_path, task="bracket_v2 Plate 1")
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")
    old_key = app._job_key
    _status(app, "running", "offline")

    app._state_map[app._task_name_entity] = "hinge_r3 Plate 2"
    app._on_task_name_change(
        app._task_name_entity, None, "bracket_v2 Plate 1", "hinge_r3 Plate 2", {}
    )
    assert app._job_task_name == "bracket_v2 Plate 1"
    assert not _has_log(app, "JOB_TASK_NAME_UPDATED")

    _status(app, "offline", "running")
    assert _has_log(app, "PRINT_JOB_SUPERSEDED")
    assert app._job_key.startswith("hinge_r3_Plate_2_") and app._job_key != old_key


@pytest.mark.parametrize("value", ["", "unknown", "unavailable", None])
def test_unreadable_task_name_is_ignored(tmp_path, value):
    app = _make_app(tmp_path, task="bracket_v2 Plate 1")
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")

    app._on_task_name_change(app._task_name_entity, None, "bracket_v2 Plate 1", value, {})

    assert app._job_task_name == "bracket_v2 Plate 1"


def test_task_name_change_with_no_open_job_is_ignored(tmp_path):
    app = _make_app(tmp_path)
    app._on_task_name_change(app._task_name_entity, None, "a", "b", {})
    assert app._job_task_name == ""


# ── rehydrate ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "task,expected",
    [
        ("bracket_v2 Plate 1", "bracket_v2 Plate 1"),  # key belongs to this task
        ("hinge_r3 Plate 2", ""),                      # key is from another job
        ("unavailable", ""),
    ],
)
def test_rehydrate_restores_task_name_only_when_job_key_matches(tmp_path, task, expected):
    app = _make_app(tmp_path, task=task)
    app._state_map[app._print_status_entity] = "running"
    app._state_map[app._job_key_entity] = "bracket_v2_Plate_1_1700000000"

    app._rehydrate_print_state()

    assert app._job_key == "bracket_v2_Plate_1_1700000000"
    assert app._job_task_name == expected


# ── the 61-second dropout, end to end, non-RFID slot ──────────────────


def test_early_dropout_non_rfid_slot_writes_slicer_grams(tmp_path):
    """Dropout before any tray is seeded; slicer file found at finish.

    Slot 4 is non-RFID and bound. The gcode-file sensor still names the
    previous print at start; the new-layout slicer file appears later.
    """
    prints = tmp_path / "YOUR_PRINTER_SERIAL" / "prints"
    cache_dir = prints / "cache"
    cache_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    gcode_entity = "sensor.p1s_test_gcode_file_downloaded"

    app = _TestableUsageSync(
        state_map={
            "input_text.ams_slot_1_spool_id": "97",
            "input_text.ams_slot_4_spool_id": "118",
            "sensor.p1s_tray_1_fuel_gauge_remaining": "670.0",
        },
        args={
            "lifecycle_phase1_enabled": True,
            "lifecycle_phase2_enabled": True,
            "bambulab_cache_path": str(cache_dir) + "/",
            "data_dir": str(data_dir),
        },
    )
    app.threemf_enabled = True
    app._gcode_file_entity = gcode_entity
    app._state_map[gcode_entity] = "168902-cdxl_x1_coupons_chassis_Coupons.gcode.gcode"
    app._state_map[app._task_name_entity] = "cdxl_x1_coupons_chassis_Chassis"
    app._state_map[app._print_weight_entity] = "488.51"

    _clear_active_tray(app)
    _status(app, "idle", "running")
    job_key = app._job_key
    assert app._trays_used == set()

    _status(app, "running", "offline")  # 61 s in, nothing seeded yet

    _set_active_tray(app, 0, 3)  # slot 4 feeding when the printer returns
    _status(app, "offline", "running")
    assert app._job_key == job_key
    assert app._trays_used == {4}

    # ~2 min in: ha-bambulab writes this print's files and updates the sensor.
    (prints / "2710709-cdxl_x1_coupons_chassis_Chassis.gcode.slice_info.config").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <plate>\n'
        '    <filament id="1" type="PLA" color="#8E9089" used_g="488.51" '
        'used_m="160.0" tray_info_idx="GFL99"/>\n  </plate>\n</config>\n',
        encoding="utf-8",
    )
    app._state_map[gcode_entity] = "2710709-cdxl_x1_coupons_chassis_Chassis.gcode.gcode"

    _backdate_open_segment(app, 4, 45000)
    _status(app, "running", "finish")

    assert _has_log(app, "3MF_CACHE_HIT")
    assert not _has_log(app, "NO_ACTIVE_SLOTS")
    assert app._use_calls == [{"spool_id": 118, "use_weight": 488.51}]
    assert _has_log(app, "method=3mf")
    assert _alerts(app) == []
    assert app._job_key == ""
