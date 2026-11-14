"""IN-1170 (Jerry 23:04 '98 A'): a stuck RFID gauge must not reset Spoolman after a print that used the spool.

11-13 r4b_Sign: spool 98 (Bambu PLA black, RFID) read 1000 g at the start and at the end of a 65.5 g print. The
finish fell back to the slicer (RFID_DELTA_ZERO_SLICER_FALLBACK); 60 s later the RFID-wins reconcile patched Spoolman
back to the gauge's 1000 g. Gate on 339d5b5: the mark must hold per spool until the gauge reading changes, not for
one reconcile, or the next finish that doesn't use that slot resets it again."""
import json

from filament_iq import consumption_engine
from filament_iq.consumption_engine import SlotDecision
from test_ams_print_usage_sync import _has_log, _reconcile_app, _TestableUsageSync


def _stuck_app(tmp_path, remain=100, spoolman=934.5):
    app = _reconcile_app(slot=4, spool_id=10, remain=remain, tray_weight=1000, spoolman_remaining=spoolman)
    app._gauge_stuck_path = tmp_path / "gauge_stuck.json"
    return app


def test_a_marked_spool_is_not_reset_while_its_gauge_reads_the_same(tmp_path):
    app = _stuck_app(tmp_path)
    app._mark_gauge_stuck(4, 10)
    app._reconcile_rfid_weights()
    assert app._patch_calls == []
    assert _has_log(app, "RFID_GAUGE_STUCK slot=4 spool_id=10")


def test_later_finishes_that_dont_use_the_slot_still_dont_reset_it(tmp_path):
    # the gate's case: the mark outlives one reconcile
    app = _stuck_app(tmp_path)
    app._mark_gauge_stuck(4, 10)
    for _ in range(3):
        app._reconcile_rfid_weights()
    assert app._patch_calls == []


def test_the_mark_survives_a_restart(tmp_path):
    app = _stuck_app(tmp_path)
    app._mark_gauge_stuck(4, 10)
    assert json.loads((tmp_path / "gauge_stuck.json").read_text()) == {"10": 100.0}
    fresh = _stuck_app(tmp_path)               # a new app instance reads the file
    fresh._reconcile_rfid_weights()
    assert fresh._patch_calls == []


def test_a_moving_gauge_clears_the_mark_and_rfid_wins_again(tmp_path):
    app = _stuck_app(tmp_path)
    app._mark_gauge_stuck(4, 10)
    entity = app._tray_entity_by_slot[4]
    app._state_map[f"{entity}::remain"] = 92    # the AMS re-read the tag
    app._reconcile_rfid_weights()
    assert app._patch_calls and app._patch_calls[0]["data"] == {"remaining_weight": 920.0}
    assert json.loads((tmp_path / "gauge_stuck.json").read_text()) == {}
    assert _has_log(app, "RFID_GAUGE_STUCK_CLEARED slot=4 spool_id=10")


def test_a_swapped_in_spool_has_no_mark(tmp_path):
    app = _stuck_app(tmp_path, remain=39, spoolman=500.0)
    app._gauge_marks()["98"] = 100.0           # the stuck spool, now out of this slot
    app._reconcile_rfid_weights()              # slot 4 holds spool 10: reconciles normally
    assert app._patch_calls[0]["data"] == {"remaining_weight": 390.0}


def test_the_finish_marks_spools_whose_gauge_fell_back_to_the_slicer(monkeypatch, tmp_path):
    decisions = [
        SlotDecision(slot=3, spool_id=98, consumption_g=65.51, method="3mf",
                     skip_reason="RFID_DELTA_ZERO_SLICER_FALLBACK", confidence="high"),
        SlotDecision(slot=4, spool_id=124, consumption_g=2.29, method="3mf", skip_reason=None, confidence="high"),
    ]
    monkeypatch.setattr(consumption_engine, "decide_consumption", lambda *a, **k: decisions)
    app = _TestableUsageSync(
        state_map={"input_text.ams_slot_3_spool_id": "98", "input_text.ams_slot_4_spool_id": "124"},
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    app._gauge_stuck_path = tmp_path / "gauge_stuck.json"
    app._state_map[f"{app._tray_entity_by_slot[3]}::remain"] = 100
    app._job_key = "r4b_sign"
    app._start_snapshot = {3: 1000.0, 4: 991.0}
    app._spool_id_snapshot = {3: 98, 4: 124}
    app._trays_used = {3, 4}
    app._print_active = True
    app._do_finish("finish")
    assert app._gauge_marks() == {"98": 100.0}


# ── IN-1170 option B (chat 08:09 11-14): a full gauge with real usage in Spoolman is stuck, with no fallback on record ──
def _full_app(tmp_path, spoolman, used, initial=1000.0, remain=100):
    app = _stuck_app(tmp_path, remain=remain, spoolman=spoolman)

    def _get(path):
        if "/api/v1/spool/10" in path:
            d = {"id": 10, "remaining_weight": spoolman, "initial_weight": initial}
            if used is not None:
                d["used_weight"] = used
            return d
        return {"remaining_weight": 100}
    app._spoolman_get_override = _get
    return app


def test_full_gauge_with_spoolman_usage_is_marked_and_not_reset(tmp_path):
    # spool 10 after the 11-14 00:59 Cube A print: 959.79 in Spoolman, gauge still 100%
    app = _full_app(tmp_path, spoolman=959.8, used=40.2)
    app._reconcile_rfid_weights()
    assert app._patch_calls == []
    assert app._gauge_marks() == {"10": 100.0}
    assert json.loads((tmp_path / "gauge_stuck.json").read_text()) == {"10": 100.0}
    assert _has_log(app, "RFID_GAUGE_STUCK_FULL slot=4 spool_id=10")


def test_once_marked_later_reconciles_keep_spoolman(tmp_path):
    app = _full_app(tmp_path, spoolman=934.5, used=65.5)
    for _ in range(3):
        app._reconcile_rfid_weights()
    assert app._patch_calls == []
    assert _has_log(app, "RFID_GAUGE_STUCK slot=4 spool_id=10")


def test_a_genuinely_new_full_spool_is_not_marked(tmp_path):
    app = _full_app(tmp_path, spoolman=1000.0, used=0.0)
    app._reconcile_rfid_weights()
    assert app._patch_calls == []
    assert app._gauge_marks() == {}
    assert _has_log(app, "RFID_WEIGHT_MATCH slot=4 spool_id=10")


def test_a_new_spool_with_only_a_purge_used_is_not_marked(tmp_path):
    # 2 g or less is a purge/tip, not a stuck gauge; the gauge's 10 g resolution can't show it anyway
    app = _full_app(tmp_path, spoolman=992.0, used=2.0, initial=994.0)
    app._reconcile_rfid_weights()
    assert app._gauge_marks() == {}
    assert app._patch_calls and app._patch_calls[0]["data"] == {"remaining_weight": 1000.0}


def test_a_spool_rfid_wins_just_reset_is_not_marked_until_corrected(tmp_path):
    # the known limit: after the reset Spoolman shows full and 0 used, so nothing tells it apart from a new spool.
    app = _full_app(tmp_path, spoolman=1000.0, used=0.0)
    app._reconcile_rfid_weights()
    assert app._gauge_marks() == {} and app._patch_calls == []
    # the print-ops EDIT puts the real value back; the next reconcile marks it and keeps it
    corrected = _full_app(tmp_path, spoolman=959.8, used=40.2)
    corrected._reconcile_rfid_weights()
    assert corrected._patch_calls == []
    assert corrected._gauge_marks() == {"10": 100.0}


def test_used_falls_back_to_initial_minus_remaining(tmp_path):
    app = _full_app(tmp_path, spoolman=959.8, used=None, initial=1000.0)
    app._reconcile_rfid_weights()
    assert app._patch_calls == [] and app._gauge_marks() == {"10": 100.0}


def test_unknown_usage_leaves_rfid_wins_alone(tmp_path):
    app = _stuck_app(tmp_path, remain=100, spoolman=959.8)   # helper's spool dict has no used/initial weight
    app._reconcile_rfid_weights()
    assert app._gauge_marks() == {}
    assert app._patch_calls[0]["data"] == {"remaining_weight": 1000.0}


def test_a_gauge_below_full_still_wins(tmp_path):
    app = _full_app(tmp_path, spoolman=959.8, used=40.2, remain=99)
    app._reconcile_rfid_weights()
    assert app._gauge_marks() == {}
    assert app._patch_calls[0]["data"] == {"remaining_weight": 990.0}


def test_the_full_mark_clears_when_the_gauge_moves(tmp_path):
    app = _full_app(tmp_path, spoolman=959.8, used=40.2)
    app._reconcile_rfid_weights()
    app._state_map[f"{app._tray_entity_by_slot[4]}::remain"] = 95   # the AMS re-read the tag
    app._reconcile_rfid_weights()
    assert app._gauge_marks() == {}
    assert app._patch_calls[0]["data"] == {"remaining_weight": 950.0}   # RFID wins again
    assert _has_log(app, "RFID_GAUGE_STUCK_CLEARED slot=4 spool_id=10")
