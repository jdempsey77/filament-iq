#!/usr/bin/env python3
"""
test_dropout_tracking.py — a printer dropout must not stop tray tracking.

Bug: `running → offline` switched tray tracking off, and `offline → running`
with a job already open returned before switching it back on. No slot was
added and no time accrued for the rest of the print; the finish logged
`USAGE_SKIP reason=NO_ACTIVE_SLOTS` at INFO and nothing was recorded.

Covers:
  - same print reconnecting resumes tracking and keeps what was gathered
  - a different print starting over an open job gets its own job key
  - a finished print that tracked no slot raises one alert
"""

import datetime

import pytest

from conftest import SpoolmanRecorder
from test_ams_print_usage_sync import (
    _TestableUsageSync,
    _active_tray_state,
    _has_log,
    _rfid_tag_uid_for_slots,
)

ALERT_TITLE = "Consumption Not Recorded"


def _alerts(app):
    return [c for c in app._service_calls if c.get("title") == ALERT_TITLE]


def _make_app(tmp_path, task="bracket_v2 Plate 1", spools=None, rfid_slots=(1,)):
    """App with slot 1 (RFID, 900 g) and slot 4 (RFID, 500 g) bound."""
    spools = spools or {1: 10, 4: 40}
    state = {f"input_text.ams_slot_{s}_spool_id": str(i) for s, i in spools.items()}
    state["sensor.p1s_tray_1_fuel_gauge_remaining"] = "900.0"
    state["sensor.p1s_tray_4_fuel_gauge_remaining"] = "500.0"
    app = _TestableUsageSync(
        state_map=state,
        args={
            "lifecycle_phase1_enabled": True,
            "lifecycle_phase2_enabled": True,
            "data_dir": str(tmp_path),
        },
    )
    app._state_map[app._task_name_entity] = task
    app._state_map[app._print_weight_entity] = "120"
    app._state_map.update(_rfid_tag_uid_for_slots(app, list(rfid_slots)))
    return app


def _set_active_tray(app, ams_index, tray_index):
    app._state_map.update(_active_tray_state(app, ams_index, tray_index))


def _clear_active_tray(app):
    e = app._active_tray_entity
    app._state_map[e] = "unknown"
    app._state_map.pop(f"{e}::ams_index", None)
    app._state_map.pop(f"{e}::tray_index", None)


def _backdate_open_segment(app, slot, seconds):
    """Pretend the slot's open segment started `seconds` ago."""
    seg = app._tray_active_times[slot][-1]
    assert seg["end"] is None
    seg["start"] = datetime.datetime.utcnow() - datetime.timedelta(seconds=seconds)


def _status(app, old, new):
    app._state_map[app._print_status_entity] = new
    app._on_print_status_change(app._print_status_entity, None, old, new, {})


# ── same print reconnecting ───────────────────────────────────────────


def test_dropout_before_any_tray_seeded_then_finish_records_slot(tmp_path):
    """The 61-second case: offline before a tray was seeded, then a full print."""
    app = _make_app(tmp_path)
    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(10, remaining=800.0)

    _clear_active_tray(app)
    _status(app, "idle", "running")
    job_key = app._job_key
    assert job_key and app._trays_used == set()

    _status(app, "running", "offline")
    assert app._print_active is False

    _set_active_tray(app, 0, 0)  # slot 1 is feeding when the printer returns
    _status(app, "offline", "running")

    assert app._print_active is True
    assert app._job_key == job_key, "reconnect must not reset the job"
    assert app._trays_used == {1}
    assert _has_log(app, "TRAY_TRACKING_RESUMED")

    _backdate_open_segment(app, 1, 3600)
    app._state_map["sensor.p1s_tray_1_fuel_gauge_remaining"] = "800.0"
    _status(app, "running", "finish")

    assert not _has_log(app, "NO_ACTIVE_SLOTS")
    assert recorder.use_count == 1
    recorder.assert_used(spool_id=10, weight=100.0)
    assert _alerts(app) == []


def test_dropout_mid_print_keeps_earlier_time_and_adds_later(tmp_path):
    app = _make_app(tmp_path)
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")
    job_key = app._job_key
    start_snapshot = dict(app._start_snapshot)
    _backdate_open_segment(app, 1, 1000)

    _status(app, "running", "offline")
    assert app._summarize_tray_times()[1] == pytest.approx(1000, abs=2)

    _status(app, "offline", "running")
    assert app._print_active is True
    assert app._job_key == job_key
    assert app._start_snapshot == start_snapshot
    assert len(app._tray_active_times[1]) == 2, "a new segment opens on resume"
    _backdate_open_segment(app, 1, 500)

    _status(app, "running", "offline")  # close the segment to read the total
    assert app._summarize_tray_times()[1] == pytest.approx(1500, abs=4)
    assert app._trays_used == {1}


def test_runout_after_dropout_adds_second_slot(tmp_path):
    app = _make_app(tmp_path)
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")
    _status(app, "running", "offline")
    _status(app, "offline", "running")

    _set_active_tray(app, 0, 3)  # runout: the printer switches to slot 4
    app._on_active_tray_change(app._active_tray_entity, None, "x", "y", {})

    assert app._trays_used == {1, 4}
    assert app._current_active_slot == 4
    assert 4 in app._tray_active_times


def test_tray_unreadable_on_reconnect_is_seeded_later(tmp_path):
    """Integration still reconnecting: the delayed seed picks the tray up."""
    app = _make_app(tmp_path)
    _clear_active_tray(app)
    _status(app, "idle", "running")
    _status(app, "running", "offline")
    _status(app, "offline", "running")

    assert app._print_active is True and app._trays_used == set()
    delayed = [c for c in app._run_in_calls
               if c["callback"].__name__ == "_delayed_resume_seed"]
    assert delayed, "a delayed re-seed must be scheduled"

    _set_active_tray(app, 0, 3)
    app._delayed_resume_seed({})
    assert app._trays_used == {4}


def test_unreadable_task_name_on_reconnect_is_the_same_job(tmp_path):
    app = _make_app(tmp_path)
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")
    job_key = app._job_key
    _status(app, "running", "offline")

    app._state_map[app._task_name_entity] = "unavailable"
    _status(app, "offline", "running")

    assert app._job_key == job_key
    assert app._print_active is True
    assert not _has_log(app, "PRINT_JOB_SUPERSEDED")


def test_spurious_running_event_while_tracking_changes_nothing(tmp_path):
    """old=None → running with tracking already on (AppDaemon/HA restart)."""
    app = _make_app(tmp_path)
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")
    segments_before = len(app._tray_active_times[1])

    _status(app, None, "running")

    assert _has_log(app, "PRINT_STATUS_SPURIOUS_SKIP")
    assert len(app._tray_active_times[1]) == segments_before
    assert app._trays_used == {1}


# ── a different print over an open job ────────────────────────────────


def test_different_job_over_open_one_gets_its_own_key(tmp_path):
    app = _make_app(tmp_path, task="bracket_v2 Plate 1")
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")
    old_key = app._job_key
    assert old_key.startswith("bracket_v2_Plate_1_")

    _status(app, "running", "offline")  # the finish is never seen

    app._state_map[app._task_name_entity] = "hinge_r3 Plate 2"
    _set_active_tray(app, 0, 3)
    _status(app, "offline", "running")

    assert _has_log(app, "PRINT_JOB_SUPERSEDED")
    assert app._job_key != old_key
    assert app._job_key.startswith("hinge_r3_Plate_2_")
    assert app._job_task_name == "hinge_r3 Plate 2"
    assert app._trays_used == {4}, "the old job's slots must not carry over"
    assert app._print_active is True
    assert _has_log(app, "TRAY_TRACKING_START")
    assert len(_alerts(app)) == 1, "one alert for the job that was dropped"


# ── finished print with no tracked slot ───────────────────────────────


def _no_slot_app(tmp_path, spools=None):
    app = _make_app(tmp_path, spools=spools)
    app._job_key = "bracket_v2_Plate_1_1700000000"
    app._job_task_name = "bracket_v2 Plate 1"
    app._start_snapshot = {1: 900.0}
    app._spool_id_snapshot = dict(spools or {1: 10, 4: 40})
    app._trays_used = set()
    return app


def test_no_active_slots_on_finished_print_alerts_once(tmp_path):
    app = _no_slot_app(tmp_path)
    app._do_finish("finish")

    assert _has_log(app, "USAGE_SKIP reason=NO_ACTIVE_SLOTS")
    assert any(
        "USAGE_NO_SLOTS_FINISHED" in msg and level == "WARNING"
        for msg, level in app._log_calls
    )
    alerts = _alerts(app)
    assert len(alerts) == 1
    assert "bracket_v2 Plate 1" in alerts[0]["message"]
    assert app._use_calls == []
    assert app._job_key == "", "the job still closes"


@pytest.mark.parametrize("status", ["failed", "canceled", "cancelled", "error"])
def test_no_active_slots_on_unsuccessful_print_stays_quiet(tmp_path, status):
    app = _no_slot_app(tmp_path)
    app._do_finish(status)

    assert _has_log(app, "USAGE_SKIP reason=NO_ACTIVE_SLOTS")
    assert _alerts(app) == []


def test_no_active_slots_with_nothing_bound_stays_quiet(tmp_path):
    app = _make_app(tmp_path)
    for slot in (1, 4):
        app._state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
    app._job_key = "bracket_v2_Plate_1_1700000000"
    app._start_snapshot = {1: 900.0}
    app._spool_id_snapshot = {}
    app._trays_used = set()
    app._do_finish("finish")

    assert _alerts(app) == []
