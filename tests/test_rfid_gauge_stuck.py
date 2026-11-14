"""IN-1170 (Jerry 23:04 '98 A'): a stuck RFID gauge must not reset Spoolman after a print that used the slot.

11-13 r4b_Sign: spool 98 (Bambu PLA black, RFID) read 1000 g at the start and at the end of a 65.5 g print. The
finish fell back to the slicer (RFID_DELTA_ZERO_SLICER_FALLBACK); 60 s later the RFID-wins reconcile patched Spoolman
back to the gauge's 1000 g, undoing the charge, and it would undo every hand correction the same way."""

from filament_iq import consumption_engine
from filament_iq.consumption_engine import SlotDecision
from test_ams_print_usage_sync import _has_log, _reconcile_app, _TestableUsageSync


def test_a_stuck_slot_is_not_reset_and_says_why():
    app = _reconcile_app(slot=4, spool_id=10, remain=100, tray_weight=1000, spoolman_remaining=934.5)
    app._gauge_stuck_slots = {4}
    app._reconcile_rfid_weights()
    assert app._patch_calls == []
    assert _has_log(app, "RFID_GAUGE_STUCK slot=4 spool_id=10")


def test_the_verdict_covers_one_reconcile_only():
    app = _reconcile_app(slot=4, spool_id=10, remain=100, tray_weight=1000, spoolman_remaining=934.5)
    app._gauge_stuck_slots = {4}
    app._reconcile_rfid_weights()
    assert app._gauge_stuck_slots == set()
    app._reconcile_rfid_weights()          # a later finish with a moving gauge: RFID wins again
    assert app._patch_calls and app._patch_calls[0]["data"] == {"remaining_weight": 1000.0}


def test_other_slots_still_reconcile():
    app = _reconcile_app(slot=4, spool_id=10, remain=39, tray_weight=1000, spoolman_remaining=500.0)
    app._gauge_stuck_slots = {3}
    app._reconcile_rfid_weights()
    assert app._patch_calls[0]["data"] == {"remaining_weight": 390.0}


def test_the_finish_marks_slots_whose_gauge_fell_back_to_the_slicer(monkeypatch):
    decisions = [
        SlotDecision(slot=3, spool_id=98, consumption_g=65.51, method="3mf",
                     skip_reason="RFID_DELTA_ZERO_SLICER_FALLBACK", confidence="high"),
        SlotDecision(slot=4, spool_id=124, consumption_g=2.29, method="3mf", skip_reason=None, confidence="high"),
        SlotDecision(slot=1, spool_id=97, consumption_g=20.0, method="rfid_delta", skip_reason=None,
                     confidence="high"),
    ]
    monkeypatch.setattr(consumption_engine, "decide_consumption", lambda *a, **k: decisions)
    app = _TestableUsageSync(
        state_map={"input_text.ams_slot_3_spool_id": "98", "input_text.ams_slot_4_spool_id": "124",
                   "input_text.ams_slot_1_spool_id": "97"},
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    app._job_key = "r4b_sign"
    app._start_snapshot = {1: 540.0, 3: 1000.0, 4: 991.0}
    app._spool_id_snapshot = {1: 97, 3: 98, 4: 124}
    app._trays_used = {1, 3, 4}
    app._print_active = True
    app._do_finish("finish")
    assert app._gauge_stuck_slots == {3}
