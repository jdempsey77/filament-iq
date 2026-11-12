#!/usr/bin/env python3
"""
test_runout_at_finish.py — a tray reading Empty at the finish is not always a runout (IN-1045).

Real sequence, 2026-11-11 (AppDaemon log): non-RFID slot 4, spool 118 with 817.0 g left,
slicer 27.36 g (3MF cache hit, single_filament_force). The spool was lifted off as the print
ended: RUNOUT_DETECTED slot=4 at 18:57:27, the finish at 18:57:31. The finish path saw the
tray Empty and wrote 3mf_depleted = max(27.36, 817.0) = 817 g: spool zeroed, location Empty,
slot helper cleared. The scale said 29.0 g.

  - Empty for <= NEAR_FINISH_EMPTY_S at a normal finish → 3mf_empty_at_finish: the slicer
    figure, spool stays bound, never zeroed (even if the ran_out boolean is still on)
  - remainder far above the slicer figure at a normal finish (empty age known or not)
    → 3mf_depletion_suspect: the slicer figure, DEPLETION_SUSPECT logged and a push sent
    after the write, saying what was actually recorded (IN-1048)
  - a runout the runout split handled (continuation slot) keeps today's path (IN-1048)
  - 3mf_empty_at_finish leaves a bell entry, so a real last-minute runout is not silent
  - a believable runout, or a failed/cancelled print → today's path
"""

import datetime

import pytest

from filament_iq.consumption_engine import (
    NEAR_FINISH_EMPTY_S,
    SlotInput,
    decide_consumption,
    depletion_suspect_limit,
)
from test_ams_print_usage_sync import _TestableUsageSync, _has_log

NOW = datetime.datetime(2026, 11, 11, 23, 57, 31, 723787, tzinfo=datetime.timezone.utc)
SLICER_G = 27.36
REMAINING_G = 817.0
SPOOL = 118
SUSPECT_TITLE = "Check Spool Weight"
TASK = "cdxl_x1_parts_Drawers x8"


def _app(tmp_path, empty_seconds_before_finish, ran_out="off", remaining=REMAINING_G):
    """Slot 4, non-RFID, spool 118, 3MF = one filament of 27.36 g, tray reads Empty.

    empty_seconds_before_finish=None: no last_changed attribute (age unknown).
    """
    app = _TestableUsageSync(
        state_map={"input_text.ams_slot_4_spool_id": str(SPOOL)},
        args={
            "lifecycle_phase1_enabled": True,
            "lifecycle_phase2_enabled": True,
            "data_dir": str(tmp_path),
        },
    )
    app._now_utc = lambda: NOW
    app._job_task_name = TASK
    app._job_key = "%s_%d" % (TASK.replace(" ", "_"), 1794438416)
    app._state_map[app._task_name_entity] = TASK
    app._state_map[app._print_weight_entity] = "27.36"
    app._trays_used = {4}
    app._start_snapshot = {}
    app._spool_id_snapshot = {4: SPOOL}
    app.threemf_enabled = True
    app._threemf_data = [
        {"index": 0, "used_g": SLICER_G, "color_hex": "8e9089", "material": "pla"}
    ]
    entity = app._tray_entity_by_slot[4]
    app._state_map[entity] = "Empty"
    app._state_map[f"{entity}::tag_uid"] = "0" * 16  # non-RFID tray
    app._state_map[f"{entity}::color"] = "#8E9089FF"
    app._state_map[f"{entity}::type"] = "PLA"
    if empty_seconds_before_finish is not None:
        changed = NOW - datetime.timedelta(seconds=empty_seconds_before_finish)
        app._state_map[f"{entity}::last_changed"] = changed.isoformat()
    app._state_map["input_boolean.ams_slot_4_ran_out"] = ran_out
    app._spoolman_get_override = lambda path: {"id": SPOOL, "remaining_weight": remaining}
    return app


def _writes(app):
    return [c["use_weight"] for c in app._use_calls if c["spool_id"] == SPOOL]


def _zeroed(app):
    return [p for p in app._patch_calls
            if p["spool_id"] == SPOOL and p["data"].get("remaining_weight") == 0]


def _emptied(app):
    return [p for p in app._patch_calls
            if p["spool_id"] == SPOOL and p["data"].get("location") == "Empty"]


def _suspect_pushes(app):
    return [c for c in app._service_calls if c.get("title") == SUSPECT_TITLE]


def _bells(app, slot=4):
    return [c for c in app._service_calls
            if c.get("title") == f"Slot {slot} read Empty at finish"]


# ── 1. the 11-11 18:57 sequence ──────────────────────────────────────


@pytest.mark.parametrize("ran_out", ["off", "on"])
def test_replay_1857_lift_at_finish_writes_slicer_figure(tmp_path, ran_out):
    """Empty 4.2 s before the finish: 27.36 g written, spool not zeroed or emptied.

    ran_out=on covers the race where the runout tracker has not cleared its boolean yet.
    """
    app = _app(tmp_path, empty_seconds_before_finish=4.2, ran_out=ran_out)
    app._use_remaining_override = {SPOOL: REMAINING_G - SLICER_G}
    app._do_finish("finish")

    assert _writes(app) == [pytest.approx(SLICER_G)]
    assert _zeroed(app) == []
    assert _emptied(app) == []
    assert _has_log(app, "RUNOUT_AT_FINISH_IGNORED slot=4 spool_id=118")
    assert _has_log(app, "method=3mf_empty_at_finish")
    assert not _has_log(app, "USAGE_SPOOL_DEPLETED")
    assert not _has_log(app, "SLOT_HELPER_CLEARED_ON_DEPLETION")
    assert not _has_log(app, "RUNOUT_ZERO_OVERRIDE")
    assert _suspect_pushes(app) == []


def test_empty_at_window_edge_is_still_a_lift(tmp_path):
    app = _app(tmp_path, empty_seconds_before_finish=NEAR_FINISH_EMPTY_S)
    app._use_remaining_override = {SPOOL: REMAINING_G - SLICER_G}
    app._do_finish("finish")
    assert _writes(app) == [pytest.approx(SLICER_G)]
    assert _has_log(app, "method=3mf_empty_at_finish")


# ── 2. a real runout keeps today's behaviour ─────────────────────────


def test_mid_print_runout_still_depletes(tmp_path):
    """Empty 30 min before the finish: a real runout, the remainder is charged."""
    app = _app(tmp_path, empty_seconds_before_finish=1800, remaining=20.0)
    app._use_remaining_override = {SPOOL: 0.0}
    app._do_finish("finish")
    assert _writes(app) == [pytest.approx(SLICER_G)]  # max(27.36, 20.0)
    assert _has_log(app, "method=3mf_depleted")
    assert _has_log(app, "USAGE_SPOOL_DEPLETED")


def test_known_runout_with_big_remainder_is_suspect_not_silent(tmp_path):
    """IN-1048 (1): Empty 121 s before the finish but 817 g left in Spoolman: not believable.

    The slicer figure is written and a push says so; the remainder is never charged silently.
    """
    app = _app(tmp_path, empty_seconds_before_finish=NEAR_FINISH_EMPTY_S + 1)
    app._use_remaining_override = {SPOOL: REMAINING_G - SLICER_G}
    app._do_finish("finish")
    assert _writes(app) == [pytest.approx(SLICER_G)]
    assert _zeroed(app) == [] and _emptied(app) == []
    assert _has_log(app, "method=3mf_depletion_suspect")
    assert _has_log(app, "empty_age_s=121.0")
    assert len(_suspect_pushes(app)) == 1


@pytest.mark.parametrize("status", ["failed", "canceled"])
def test_failed_or_cancelled_print_keeps_depletion(tmp_path, status):
    app = _app(tmp_path, empty_seconds_before_finish=4.2)
    app._use_remaining_override = {SPOOL: 0.0}
    app._do_finish(status)
    # Unchanged by IN-1045: a failed/cancelled print takes no 3MF match, so the
    # Empty tray goes down depleted_nonrfid and the remainder is charged.
    assert _writes(app) == [pytest.approx(REMAINING_G)]
    assert _has_log(app, "method=depleted_nonrfid")
    assert not _has_log(app, "RUNOUT_AT_FINISH_IGNORED")
    assert not _has_log(app, "DEPLETION_SUSPECT")


# ── 3. age unknown: a doubtful depletion is never silent ─────────────


def test_lift_at_finish_leaves_a_bell_not_a_push(tmp_path):
    """IN-1048 (2): the slot stays bound, but a bell entry says so (a real last-minute runout)."""
    app = _app(tmp_path, empty_seconds_before_finish=4.2)
    app._use_remaining_override = {SPOOL: REMAINING_G - SLICER_G}
    app._do_finish("finish")
    bells = _bells(app)
    assert len(bells) == 1
    assert "Recorded 27.4 g" in bells[0]["message"]
    assert "790 g" in bells[0]["message"]
    assert _suspect_pushes(app) == []


def test_suspect_push_after_a_failed_write_says_it_failed(tmp_path):
    """IN-1048 (3): the push is sent after the write and never claims a write that failed."""
    app = _app(tmp_path, empty_seconds_before_finish=None)
    app._use_fail_spool_ids = {SPOOL}
    app._do_finish("finish")
    assert _writes(app) == []
    pushes = _suspect_pushes(app)
    assert len(pushes) == 1
    assert "FAILED" in pushes[0]["message"]
    assert "Recorded" not in pushes[0]["message"]
    assert _has_log(app, "written=False")


def test_suspect_push_comes_after_the_write(tmp_path):
    app = _app(tmp_path, empty_seconds_before_finish=None)
    app._use_remaining_override = {SPOOL: REMAINING_G - SLICER_G}
    order = []
    real_use, real_notify = app._spoolman_use, app.fiq_notify
    app._spoolman_use = lambda *a, **k: (order.append("write"), real_use(*a, **k))[1]
    app.fiq_notify = lambda *a, **k: (order.append("notify"), real_notify(*a, **k))[1]
    app._do_finish("finish")
    assert order.index("write") < order.index("notify")
    assert "Spoolman now shows 790 g" in _suspect_pushes(app)[0]["message"]


def test_unknown_age_big_remainder_is_suspect_and_alerts(tmp_path):
    app = _app(tmp_path, empty_seconds_before_finish=None)
    app._use_remaining_override = {SPOOL: REMAINING_G - SLICER_G}
    app._do_finish("finish")

    assert _writes(app) == [pytest.approx(SLICER_G)]
    assert _zeroed(app) == []
    assert _emptied(app) == []
    assert _has_log(app, "DEPLETION_SUSPECT slot=4 spool_id=118")
    assert _has_log(app, "method=3mf_depletion_suspect")
    pushes = _suspect_pushes(app)
    assert len(pushes) == 1
    assert "817 g" in pushes[0]["message"]


def test_unknown_age_small_remainder_depletes_as_before(tmp_path):
    """Remainder within the limit (60 g <= max(41.0, 77.4)): a believable runout."""
    app = _app(tmp_path, empty_seconds_before_finish=None, remaining=60.0)
    app._use_remaining_override = {SPOOL: 0.0}
    app._do_finish("finish")
    assert _writes(app) == [pytest.approx(60.0)]
    assert _has_log(app, "method=3mf_depleted")
    assert _suspect_pushes(app) == []


def test_bad_last_changed_counts_as_unknown(tmp_path):
    app = _app(tmp_path, empty_seconds_before_finish=None)
    app._state_map[f"{app._tray_entity_by_slot[4]}::last_changed"] = "not a time"
    app._use_remaining_override = {SPOOL: REMAINING_G - SLICER_G}
    app._do_finish("finish")
    assert _has_log(app, "method=3mf_depletion_suspect")


# ── 4. engine rules ──────────────────────────────────────────────────


def _inp(**kw):
    base = dict(
        slot=4, spool_id=SPOOL, is_rfid=False, tray_empty=True, tray_active_seconds=2898.8,
        start_g=None, end_g=None, threemf_used_g=SLICER_G,
        threemf_method="single_filament_force", spoolman_remaining=REMAINING_G,
    )
    base.update(kw)
    return SlotInput(**base)


@pytest.mark.parametrize("kw,method,grams", [
    (dict(normal_finish=True, empty_age_s=4.2), "3mf_empty_at_finish", SLICER_G),
    (dict(normal_finish=True, empty_age_s=121.0), "3mf_depletion_suspect", SLICER_G),
    (dict(normal_finish=True, empty_age_s=121.0, spoolman_remaining=60.0), "3mf_depleted", 60.0),
    # IN-1048 (2): the runout split handled it (spoolman_remaining = the split share): today's path
    (dict(normal_finish=True, empty_age_s=4.2, threemf_method="runout_split_depleted",
          threemf_used_g=12.0, spoolman_remaining=12.0), "3mf_depleted", 12.0),
    (dict(normal_finish=True, empty_age_s=None), "3mf_depletion_suspect", SLICER_G),
    (dict(normal_finish=False, empty_age_s=4.2), "3mf_depleted", REMAINING_G),
    (dict(normal_finish=False, empty_age_s=None), "3mf_depleted", REMAINING_G),
    (dict(), "3mf_depleted", REMAINING_G),  # defaults: today's behaviour
    (dict(normal_finish=True, empty_age_s=4.2, tray_empty=False), "3mf", SLICER_G),
])
def test_engine_decision(kw, method, grams):
    [d] = decide_consumption([_inp(**kw)])
    assert (d.method, d.consumption_g) == (method, pytest.approx(grams))


def test_suspect_limit():
    assert depletion_suspect_limit(27.36) == pytest.approx(77.36)
    assert depletion_suspect_limit(200.0) == pytest.approx(300.0)


def test_runout_split_in_last_120s_keeps_todays_path():
    """IN-1048 (2), end to end: a real runout handed to a continuation slot 10 s before the
    finish is not a lift. Same numbers as test_multi_spool_runout's end-to-end split."""
    app = _TestableUsageSync(
        state_map={
            "input_text.ams_slot_3_spool_id": "65",
            "input_text.ams_slot_4_spool_id": "0",  # reconciler cleared
        },
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    app._now_utc = lambda: NOW
    app._trays_used = {3, 4}
    app._start_snapshot = {3: 839.0}
    app._spool_id_snapshot = {3: 65, 4: 58}
    app._job_key = "desk_organizer_test"
    app.threemf_enabled = True
    app._threemf_data = [{"index": 0, "used_g": 160.72, "color_hex": "161616", "material": "pla"}]
    e4, e3 = app._tray_entity_by_slot[4], app._tray_entity_by_slot[3]
    app._state_map[e4] = "Empty"
    app._state_map[f"{e4}::tag_uid"] = "0" * 16
    app._state_map[f"{e4}::last_changed"] = (NOW - datetime.timedelta(seconds=10)).isoformat()
    app._state_map[e3] = "loaded"
    app._state_map[f"{e3}::tag_uid"] = "0" * 16
    app._state_map[f"{e3}::color"] = "#161616FF"
    app._state_map[f"{e3}::type"] = "PLA"
    app._spoolman_get_override = lambda path: (
        {"remaining_weight": 100.0, "id": 58} if "58" in path else {"remaining_weight": 500.0}
    )
    app._use_remaining_override = {58: 0.0, 65: 439.28}
    app._do_finish("finish")

    slot4 = [c["use_weight"] for c in app._use_calls if c["spool_id"] == 58]
    assert slot4 == [pytest.approx(100.0)]
    assert _has_log(app, "RUNOUT_SPLIT_DETECTED")
    assert not _has_log(app, "RUNOUT_AT_FINISH_IGNORED")
    assert not _has_log(app, "DEPLETION_SUSPECT")
    assert _has_log(app, "method=3mf_depleted")
