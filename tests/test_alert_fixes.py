#!/usr/bin/env python3
"""
test_alert_fixes.py — when "Consumption Not Recorded" should and should not fire.

  - a failed/cancelled print alerts for a slot only when that slot fed for at
    least `nousage_alert_min_seconds` (default 300); finished prints always alert
  - an RFID slot that records 0 g logs RFID_ZERO_DELTA_NO_SLICER, no push
  - a late task name is adopted only within 300 s of the print start
  - a NO_ACTIVE_SLOTS finish schedules the RFID weight reconciler
"""

import datetime
import time

import pytest

from test_ams_print_usage_sync import _TestableUsageSync, _has_log, _rfid_tag_uid_for_slots
from test_dropout_tracking import _alerts, _make_app, _set_active_tray, _status


def _segment(seconds):
    end = datetime.datetime.utcnow()
    return [{"start": end - datetime.timedelta(seconds=seconds), "end": end}]


def _nonrfid_app(tmp_path, active_seconds, args=None):
    """Slot 4: non-RFID, bound to spool 118, fed for `active_seconds`. No 3MF data."""
    a = {
        "lifecycle_phase1_enabled": True,
        "lifecycle_phase2_enabled": True,
        "data_dir": str(tmp_path),
    }
    a.update(args or {})
    app = _TestableUsageSync(
        state_map={"input_text.ams_slot_4_spool_id": "118"}, args=a
    )
    app._state_map[app._task_name_entity] = "holder_r3 Plate 1"
    app._state_map[app._print_weight_entity] = "129.1"
    app._job_key = "holder_r3_Plate_1_1700000000"
    app._job_task_name = "holder_r3 Plate 1"
    app._start_snapshot = {1: 900.0}
    app._spool_id_snapshot = {4: 118}
    app._trays_used = {4}
    app._tray_active_times = {4: _segment(active_seconds)}
    return app


def _rfid_app(tmp_path, active_seconds, start_g=900.0, end_g=900.0):
    """Slot 1: RFID, bound to spool 10. Fuel gauge start/end as given. No 3MF data."""
    app = _TestableUsageSync(
        state_map={
            "input_text.ams_slot_1_spool_id": "10",
            "sensor.p1s_tray_1_fuel_gauge_remaining": str(end_g),
        },
        args={
            "lifecycle_phase1_enabled": True,
            "lifecycle_phase2_enabled": True,
            "data_dir": str(tmp_path),
        },
    )
    app._state_map.update(_rfid_tag_uid_for_slots(app, [1]))
    app._state_map[app._print_weight_entity] = "65.2"
    app._job_key = "bins_rE_plate_3_1700000000"
    app._start_snapshot = {1: start_g}
    app._spool_id_snapshot = {1: 10}
    app._trays_used = {1}
    app._tray_active_times = {1: _segment(active_seconds)}
    return app


def _reconcile_calls(app):
    return [c for c in app._run_in_calls
            if c["callback"].__name__ == "_reconcile_rfid_weights_deferred"]


# ── 1. failed / cancelled prints: alert only past the threshold ───────


@pytest.mark.parametrize("seconds", [36, 112])
@pytest.mark.parametrize("status", ["failed", "canceled", "cancelled"])
def test_print_cancelled_in_first_minutes_gives_no_alert(tmp_path, seconds, status):
    app = _nonrfid_app(tmp_path, seconds)
    app._do_finish(status)

    assert _has_log(app, "USAGE_NO_EVIDENCE slot=4")
    assert _has_log(app, "USAGE_NO_EVIDENCE_NOTIFY_SKIPPED slot=4")
    assert _alerts(app) == []
    assert app._use_calls == []


def test_print_that_fails_after_two_hours_alerts(tmp_path):
    app = _nonrfid_app(tmp_path, 2 * 3600 + 25 * 60)
    app._do_finish("failed")

    alerts = _alerts(app)
    assert len(alerts) == 1
    assert "Slot 4 (spool 118)" in alerts[0]["message"]
    assert _has_log(app, "USAGE_NO_EVIDENCE_NOTIFY slot=4")


def test_threshold_is_inclusive_and_configurable(tmp_path):
    at_default = _nonrfid_app(tmp_path, 300)
    at_default._do_finish("failed")
    assert len(_alerts(at_default)) == 1

    lowered = _nonrfid_app(tmp_path, 36, args={"nousage_alert_min_seconds": 30})
    lowered._do_finish("failed")
    assert len(_alerts(lowered)) == 1


@pytest.mark.parametrize("seconds", [36, 4000])
def test_finished_non_rfid_print_with_no_evidence_always_alerts(tmp_path, seconds):
    app = _nonrfid_app(tmp_path, seconds)
    app._do_finish("finish")

    assert len(_alerts(app)) == 1
    assert not _has_log(app, "USAGE_NO_EVIDENCE_NOTIFY_SKIPPED")


def test_rehydrated_failed_print_alerts_because_timing_is_unknown(tmp_path):
    app = _nonrfid_app(tmp_path, 36)
    app._rehydrated = True
    app._do_finish("failed")

    assert len(_alerts(app)) == 1


def test_one_push_per_slot_keeps_the_per_slot_tag(tmp_path):
    """A non-helper notify service gets one call per slot; the tag is per slot."""
    app = _nonrfid_app(tmp_path, 4000, args={"notify_service": "jerry_mobile"})
    app.notify_service = "jerry_mobile"
    app._do_finish("finish")

    pushes = [c for c in app._service_calls if c.get("service") == "script/notify_jerry"
              and c.get("title") == "Consumption Not Recorded"]
    assert len(pushes) == 1
    assert pushes[0]["tag"] == "fiq-nousage-slot-4"


# ── 2. RFID slot that records nothing ─────────────────────────────────


def test_rfid_zero_delta_without_slicer_data_logs_warning_and_no_push(tmp_path):
    app = _rfid_app(tmp_path, 5544)
    app._do_finish("finish")

    warnings = [m for m, level in app._log_calls
                if "RFID_ZERO_DELTA_NO_SLICER" in m and level == "WARNING"]
    assert len(warnings) == 1
    assert "slot=1" in warnings[0] and "spool_id=10" in warnings[0]
    assert "slicer_estimate_g=65.2" in warnings[0]
    assert _alerts(app) == []
    assert app._use_calls == []


def test_rfid_delta_written_logs_no_zero_delta_warning(tmp_path):
    app = _rfid_app(tmp_path, 5544, start_g=900.0, end_g=830.0)
    app._do_finish("finish")

    assert app._use_calls == [{"spool_id": 10, "use_weight": 70.0}]
    assert not _has_log(app, "RFID_ZERO_DELTA_NO_SLICER")


def test_rfid_zero_delta_on_quick_cancel_is_not_counted(tmp_path):
    app = _rfid_app(tmp_path, 36)
    app._do_finish("failed")

    assert not _has_log(app, "RFID_ZERO_DELTA_NO_SLICER")
    assert _alerts(app) == []


# ── 5. late task name: only within 300 s of the start ─────────────────


def test_task_name_arriving_10s_after_start_is_adopted(tmp_path):
    app = _make_app(tmp_path, task="previous_print Plate 1")
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")
    app._print_start_time = time.time() - 10

    app._on_task_name_change(
        app._task_name_entity, None, "previous_print Plate 1", "bracket_v2 Plate 1", {}
    )

    assert app._job_task_name == "bracket_v2 Plate 1"
    assert _has_log(app, "JOB_TASK_NAME_UPDATED")


def test_task_name_arriving_after_the_window_is_not_adopted(tmp_path):
    app = _make_app(tmp_path, task="bracket_v2 Plate 1")
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")
    app._print_start_time = time.time() - 301

    app._on_task_name_change(
        app._task_name_entity, None, "bracket_v2 Plate 1", "hinge_r3 Plate 2", {}
    )

    assert app._job_task_name == "bracket_v2 Plate 1"
    assert _has_log(app, "JOB_TASK_NAME_NOT_ADOPTED")
    assert _has_log(app, "reason=outside_window")


def test_task_name_is_never_adopted_without_a_start_time(tmp_path):
    app = _make_app(tmp_path, task="bracket_v2 Plate 1")
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")
    app._print_start_time = None

    app._on_task_name_change(
        app._task_name_entity, None, "bracket_v2 Plate 1", "hinge_r3 Plate 2", {}
    )

    assert app._job_task_name == "bracket_v2 Plate 1"
    assert _has_log(app, "reason=no_start_time")


def test_pause_offline_new_name_an_hour_later_is_superseded_not_absorbed(tmp_path):
    """Tracking is still on after `pause → offline`; the old job must not
    take the next print's name and absorb it."""
    app = _make_app(tmp_path, task="bracket_v2 Plate 1")
    _set_active_tray(app, 0, 0)
    _status(app, "idle", "running")
    old_key = app._job_key

    _status(app, "running", "pause")
    _status(app, "pause", "offline")
    assert app._print_active is True, "pause → offline leaves tracking on"

    # An hour later a different print is sent.
    app._print_start_time = time.time() - 3600
    app._state_map[app._task_name_entity] = "hinge_r3 Plate 2"
    app._on_task_name_change(
        app._task_name_entity, None, "bracket_v2 Plate 1", "hinge_r3 Plate 2", {}
    )
    assert app._job_task_name == "bracket_v2 Plate 1"

    _set_active_tray(app, 0, 3)
    _status(app, "offline", "running")

    assert _has_log(app, "PRINT_JOB_SUPERSEDED")
    assert app._job_key != old_key
    assert app._job_key.startswith("hinge_r3_Plate_2_")
    assert app._trays_used == {4}
    assert len(_alerts(app)) == 1


# ── 6. NO_ACTIVE_SLOTS still schedules the RFID weight reconciler ─────


def test_no_active_slots_finish_schedules_the_reconciler(tmp_path):
    """Early exit: no slot was tracked at all."""
    app = _make_app(tmp_path)
    app._job_key = "bracket_v2_Plate_1_1700000000"
    app._start_snapshot = {1: 900.0}
    app._spool_id_snapshot = {1: 10}
    app._trays_used = set()
    app._do_finish("finish")

    assert _has_log(app, "USAGE_SKIP reason=NO_ACTIVE_SLOTS")
    calls = _reconcile_calls(app)
    assert len(calls) == 1 and calls[0]["delay"] == 60
    assert _has_log(app, "RFID_WEIGHT_RECONCILE_DEFERRED")


def test_no_active_slots_after_unbound_slot_filter_schedules_the_reconciler(tmp_path):
    """Late exit: the only tracked slot has no spool bound."""
    app = _make_app(tmp_path, spools={1: 10})
    app._job_key = "bracket_v2_Plate_1_1700000000"
    app._start_snapshot = {1: 900.0, 3: 500.0}
    app._spool_id_snapshot = {1: 10}
    app._trays_used = {3}
    app._tray_active_times = {3: _segment(9000)}
    app._do_finish("finish")

    assert _has_log(app, "PHANTOM_SLOT_FILTERED slot=3")
    assert _has_log(app, "USAGE_SKIP reason=NO_ACTIVE_SLOTS")
    assert len(_reconcile_calls(app)) == 1


def test_full_finish_still_schedules_the_reconciler_once(tmp_path):
    app = _rfid_app(tmp_path, 5544, start_g=900.0, end_g=830.0)
    app._do_finish("finish")

    assert len(_reconcile_calls(app)) == 1
