"""IN-1161: a non-RFID slot's reading rising is a Spoolman correction or refill, never a spool swap.

11-13 r4b_Sign: spool 119 (no tag) was corrected 297.3 -> 424.27 in Spoolman at 19:47. HA's Spoolman sensor still
said 297 at the 19:59 start snapshot and 424 at the 22:07 end, and the negative-delta swap branch charged the
whole 297 g (rfid_delta_swapped) for a 2.13 g print."""

from test_ams_print_usage_sync import _TestableUsageSync, _rfid_tag_uid_for_slots


def _app(rfid):
    app = _TestableUsageSync(
        state_map={
            "input_text.ams_slot_2_spool_id": "119",
            "sensor.p1s_tray_2_fuel_gauge_remaining": "424.0",
        },
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    if rfid:
        app._state_map.update(_rfid_tag_uid_for_slots(app, [2]))
    app._job_key = "r4b_sign"
    app._start_snapshot = {2: 297.0}       # stale HA reading at start
    app._spool_id_snapshot = {2: 119}
    app._trays_used = {2}
    app._print_active = True
    return app


def _logs(app, prefix):
    return [m for m, _ in app._log_calls if m.startswith(prefix)]


def test_non_rfid_rise_is_not_a_swap_and_charges_nothing_for_it():
    app = _app(rfid=False)
    app._do_finish("finish")
    assert not _logs(app, "SPOOL_SWAP_DETECTED")
    assert _logs(app, "SPOOL_SWAP_IGNORED")
    assert not [c for c in app._use_calls if c["spool_id"] == 119 and c["use_weight"] >= 290]


def test_rfid_rise_is_still_a_swap():
    app = _app(rfid=True)
    app._do_finish("finish")
    assert _logs(app, "SPOOL_SWAP_DETECTED")
    assert not _logs(app, "SPOOL_SWAP_IGNORED")


def test_non_rfid_small_rise_logs_nothing():
    app = _app(rfid=False)
    app._start_snapshot = {2: 400.0}       # +24 g: under the 50 g threshold
    app._do_finish("finish")
    assert not _logs(app, "SPOOL_SWAP_DETECTED") and not _logs(app, "SPOOL_SWAP_IGNORED")


def test_tagged_at_start_untagged_at_finish_is_still_a_swap():
    # IN-1167 (gate follow-up on #130): an RFID spool swapped mid-print for an untagged one.
    app = _app(rfid=False)              # no tag at finish
    app._start_rfid_slots = {2}         # but slot 2 had one when the print started
    app._do_finish("finish")
    assert _logs(app, "SPOOL_SWAP_DETECTED")
    assert not _logs(app, "SPOOL_SWAP_IGNORED")


def test_start_snapshot_records_rfid_slots():
    app = _app(rfid=True)
    app._start_rfid_slots = set()
    app._build_start_snapshot()
    assert 2 in app._start_rfid_slots
