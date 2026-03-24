"""
test_spoolman_writes.py — Integration tests for Spoolman write execution and notification.

Tests _execute_writes() phase, post-write depletion handling, dedup persistence,
and notification content. Uses SpoolmanRecorder from conftest.py for all write
assertions. Never asserts log strings to verify write behavior.

Coverage:
  - RFID slots use rfid_delta NOT 3MF (Bug 13 regression test — permanent guard)
  - Depleted spool always gets location PATCH to Empty (regardless of auto_empty)
  - auto_empty_spools=True clears slot binding when tray gone
  - auto_empty_spools=False does not clear binding
  - Write failure does not persist dedup key
  - Write success persists dedup key
  - dry_run produces zero Spoolman calls
  - Notification uses post_write_remaining not pre-write cache value
  - Mixed RFID+nonRFID: correct methods per slot type
"""

import os
import sys
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
from filament_iq.consumption_engine import SlotDecision


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_rfid_slot_uses_rfid_delta_not_3mf():
    """RFID slot must use fuel gauge delta, never 3MF. Permanent Bug 13 guard."""
    app = _TestableUsageSync(
        state_map={
            "input_text.ams_slot_1_spool_id": "10",
            "sensor.p1s_tray_1_fuel_gauge_remaining": "800.0",
        },
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    app._state_map.update(_rfid_tag_uid_for_slots(app, [1]))
    # Set up tray entity color/type for 3MF matching
    entity = app._tray_entity_by_slot[1]
    app._state_map[f"{entity}::color"] = "ff0000"
    app._state_map[f"{entity}::type"] = "pla"
    app._threemf_data = [{"index": 1, "used_g": 120.0, "color_hex": "ff0000", "material": "pla"}]
    app.threemf_enabled = True
    app._job_key = "bug13_test"
    app._start_snapshot = {1: 900.0}
    app._trays_used = {1}
    app._print_active = True

    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(10, remaining=800.0)

    app._do_finish("finish")

    recorder.assert_used(10, 100.0)  # delta=900-800=100, not 3MF 120
    assert recorder.use_calls[0]["use_weight"] != 120.0


def test_depleted_spool_always_gets_empty_location():
    """Depleted spool location must always be PATCHed to Empty."""
    app = _TestableUsageSync(
        state_map={},
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(47, remaining=0.0)

    decision = SlotDecision(
        slot=3, spool_id=47, consumption_g=432.0,
        method="depleted_nonrfid", skip_reason=None, confidence="low",
    )
    app._execute_writes([decision], "depleted_test")

    recorder.assert_used(47, 432.0)
    recorder.assert_patched_location(47, "Empty")


def test_notification_shows_post_write_remaining_not_pre_write():
    """Notification must show post-write remaining, not pre-write cache value."""
    app = _TestableUsageSync(
        state_map={
            "input_text.ams_slot_1_spool_id": "10",
            "sensor.p1s_tray_1_fuel_gauge_remaining": "800.0",
            "sensor.p1s_01p00c5a3101668_task_name": "test_model.3mf",
        },
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    app._state_map.update(_rfid_tag_uid_for_slots(app, [1]))
    app._job_key = "notif_test"
    app._start_snapshot = {1: 900.0}
    app._trays_used = {1}
    app._print_active = True
    app._print_start_time = __import__("time").time() - 3600

    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(10, remaining=731.0)

    app._do_finish("finish")

    # Find notification in service calls
    notif_calls = [c for c in app._service_calls if "notify" in c.get("service", "")]
    assert len(notif_calls) >= 1
    msg = notif_calls[0].get("message", "")
    assert "731" in msg  # post-write remaining
    assert "842" not in msg  # pre-write value should not appear


def test_write_failure_does_not_persist_dedup():
    app = _TestableUsageSync(
        state_map={"input_text.ams_slot_1_spool_id": "10",
                    "sensor.p1s_tray_1_fuel_gauge_remaining": "800.0"},
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    app._state_map.update(_rfid_tag_uid_for_slots(app, [1]))
    app._job_key = "fail_test"
    app._start_snapshot = {1: 900.0}
    app._trays_used = {1}
    app._print_active = True
    app._spoolman_use = lambda sid, w: None  # simulate failure
    app._do_finish("finish")
    assert "fail_test" not in app._seen_job_keys


def test_write_success_persists_dedup():
    app = _TestableUsageSync(
        state_map={"input_text.ams_slot_1_spool_id": "10",
                    "sensor.p1s_tray_1_fuel_gauge_remaining": "800.0"},
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    app._state_map.update(_rfid_tag_uid_for_slots(app, [1]))
    app._job_key = "success_test"
    app._start_snapshot = {1: 900.0}
    app._trays_used = {1}
    app._print_active = True
    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(10, remaining=800.0)
    app._do_finish("finish")
    assert "success_test" in app._seen_job_keys


def test_dry_run_produces_no_spoolman_calls():
    app = _TestableUsageSync(
        state_map={"input_text.ams_slot_1_spool_id": "10",
                    "sensor.p1s_tray_1_fuel_gauge_remaining": "800.0"},
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    app._state_map.update(_rfid_tag_uid_for_slots(app, [1]))
    app._job_key = "dry_test"
    app._start_snapshot = {1: 900.0}
    app._trays_used = {1}
    app._print_active = True
    app.dry_run = True
    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    app._do_finish("finish")
    assert recorder.use_count == 0
    assert recorder.patch_count == 0
    assert _has_log(app, "WOULD_PATCH")


def test_auto_empty_false_does_not_clear_slot_binding():
    app = _TestableUsageSync(
        state_map={},
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    app.auto_empty_spools = False
    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(47, remaining=0.0)
    decision = SlotDecision(
        slot=3, spool_id=47, consumption_g=432.0,
        method="depleted_nonrfid", skip_reason=None, confidence="low",
    )
    app._execute_writes([decision], "auto_empty_test")
    recorder.assert_patched_location(47, "Empty")  # always patch location
    # But should NOT clear slot binding
    binding_clears = [c for c in app._service_calls
                      if c.get("entity_id") == "input_text.ams_slot_3_spool_id"
                      and c.get("value") == "0"]
    assert len(binding_clears) == 0


def test_auto_empty_true_clears_binding_when_tray_gone():
    app = _TestableUsageSync(
        state_map={},
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    app.auto_empty_spools = True
    app._is_tray_physically_present = lambda slot: False
    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(47, remaining=0.0)
    decision = SlotDecision(
        slot=3, spool_id=47, consumption_g=432.0,
        method="depleted_nonrfid", skip_reason=None, confidence="low",
    )
    app._execute_writes([decision], "auto_empty_test")
    recorder.assert_patched_location(47, "Empty")
    binding_clears = [c for c in app._service_calls
                      if c.get("entity_id") == "input_text.ams_slot_3_spool_id"
                      and c.get("value") == "0"]
    assert len(binding_clears) == 1


def test_mixed_rfid_nonrfid_correct_methods():
    app = _TestableUsageSync(
        state_map={
            "input_text.ams_slot_1_spool_id": "10",
            "input_text.ams_slot_3_spool_id": "30",
            "sensor.p1s_tray_1_fuel_gauge_remaining": "800.0",
        },
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    app._state_map.update(_rfid_tag_uid_for_slots(app, [1]))
    # Slot 3 non-RFID with 3MF match
    entity3 = app._tray_entity_by_slot[3]
    app._state_map[f"{entity3}::tag_uid"] = ""
    app._state_map[f"{entity3}::color"] = "8e9089"
    app._state_map[f"{entity3}::type"] = "pla"
    app._threemf_data = [{"index": 3, "used_g": 45.0, "color_hex": "8e9089", "material": "pla"}]
    app.threemf_enabled = True
    app._job_key = "mixed_test"
    app._start_snapshot = {1: 900.0, 3: 725.0}
    app._trays_used = {1, 3}
    app._print_active = True

    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(10, remaining=800.0)
    recorder.set_use_response(30, remaining=680.0)

    app._do_finish("finish")

    recorder.assert_used(10, 100.0)  # RFID delta
    recorder.assert_used(30, 45.0)   # 3MF


# ---------------------------------------------------------------------------
# Rehydrated print 3MF matching tests (Mode A fix regression suite)
# ---------------------------------------------------------------------------

def test_rehydrated_single_slot_3mf_match_succeeds():
    """Rehydrated single non-RFID slot: 3MF match succeeds, consumption written.

    Scenario: AppDaemon restarts mid-print. _trays_used={5} after seed.
    spool_id_snapshot has slot 5 bound. 3MF contains matching color/material.
    Expected: slot 5 consumption written to Spoolman, no DATA_LOSS.
    """
    app = _TestableUsageSync(
        state_map={
            "input_text.ams_slot_5_spool_id": "50",
        },
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    entity5 = app._tray_entity_by_slot[5]
    app._state_map[f"{entity5}::tag_uid"] = ""  # non-RFID
    app._state_map[f"{entity5}::color"] = "ff5733"
    app._state_map[f"{entity5}::type"] = "pla"
    app._threemf_data = [
        {"index": 0, "used_g": 9.65, "color_hex": "ff5733", "material": "pla"},
    ]
    app.threemf_enabled = True
    app._job_key = "rehydrate_single_slot_001"
    app._start_snapshot = {}  # non-RFID has no fuel gauge
    app._spool_id_snapshot = {5: 50}
    app._trays_used = {5}
    app._rehydrated = True
    app._print_active = True

    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(50, remaining=90.0)

    app._do_finish("finish")

    recorder.assert_used(50, 9.65)
    assert not _has_log(app, "DATA_LOSS")
    assert not _has_log(app, "3MF_UNMATCHED")


def test_rehydrated_multi_slot_both_matched():
    """Rehydrated multi-slot: missing non-RFID slot readmitted via 3MF match.

    Scenario: Print used RFID slot 2 and non-RFID slot 5. After restart
    slot 2 is in _trays_used (tray change event fired post-restart) but
    slot 5 is NOT (it was active pre-restart only, no post-restart event).
    3MF has entry matching slot 5's color. The readmit logic should recover
    slot 5 from the 3MF match and write consumption for both slots.
    """
    app = _TestableUsageSync(
        state_map={
            "input_text.ams_slot_2_spool_id": "20",
            "input_text.ams_slot_5_spool_id": "50",
            "sensor.p1s_tray_2_fuel_gauge_remaining": "280.0",
        },
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    # Slot 2: RFID
    app._state_map.update(_rfid_tag_uid_for_slots(app, [2]))
    # Slot 5: non-RFID with 3MF color
    entity5 = app._tray_entity_by_slot[5]
    app._state_map[f"{entity5}::tag_uid"] = ""
    app._state_map[f"{entity5}::color"] = "1a8f3e"
    app._state_map[f"{entity5}::type"] = "petg"
    app._threemf_data = [
        {"index": 0, "used_g": 43.6, "color_hex": "1a8f3e", "material": "petg"},
    ]
    app.threemf_enabled = True
    app._job_key = "rehydrate_multi_slot_001"
    app._start_snapshot = {2: 300.0}  # RFID slot only
    app._spool_id_snapshot = {2: 20, 5: 50}
    # Slot 2 tracked post-restart, slot 5 missing
    app._trays_used = {2}
    app._rehydrated = True
    app._print_active = True

    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(20, remaining=280.0)
    recorder.set_use_response(50, remaining=56.0)

    app._do_finish("finish")

    recorder.assert_used(20, 20.0)   # RFID delta: 300 - 280
    recorder.assert_used(50, 43.6)   # 3MF match — slot 5 readmitted
    assert not _has_log(app, "3MF_UNMATCHED")
    assert _has_log(app, "REHYDRATE_READMIT_SLOTS")


def test_rehydrated_single_filament_color_match():
    """Rehydrated single-slot: color matching works when single_filament_force
    cannot fire (trays_used=None passed to matcher).

    With trays_used=None the single_filament_force path is skipped, but the
    color/material matcher should still find the correct slot.
    """
    app = _TestableUsageSync(
        state_map={
            "input_text.ams_slot_5_spool_id": "50",
        },
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    entity5 = app._tray_entity_by_slot[5]
    app._state_map[f"{entity5}::tag_uid"] = ""
    app._state_map[f"{entity5}::color"] = "abcdef"
    app._state_map[f"{entity5}::type"] = "pla"
    app._threemf_data = [
        {"index": 0, "used_g": 12.5, "color_hex": "abcdef", "material": "pla"},
    ]
    app.threemf_enabled = True
    app._job_key = "rehydrate_color_match_001"
    app._start_snapshot = {}
    app._spool_id_snapshot = {5: 50}
    app._trays_used = {5}
    app._rehydrated = True
    app._print_active = True

    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(50, remaining=87.5)

    app._do_finish("finish")

    recorder.assert_used(50, 12.5)
    assert _has_log(app, "3MF_MATCH")
    # Verify the match used color matching, not single_filament_force
    match_logs = [m for m, _ in app._log_calls if "3MF_MATCH" in m]
    assert any("exact_color_material" in m for m in match_logs)


def test_non_rehydrated_print_unchanged():
    """Non-rehydrated print: existing behavior unchanged (regression guard).

    Normal print with complete _trays_used={3}. 3MF has slot 3 match and
    an unmatched purge filament. Slot 3 matched and written; purge UNMATCHED.
    """
    app = _TestableUsageSync(
        state_map={
            "input_text.ams_slot_3_spool_id": "30",
        },
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    entity3 = app._tray_entity_by_slot[3]
    app._state_map[f"{entity3}::tag_uid"] = ""
    app._state_map[f"{entity3}::color"] = "8e9089"
    app._state_map[f"{entity3}::type"] = "pla"
    app._threemf_data = [
        {"index": 0, "used_g": 22.0, "color_hex": "8e9089", "material": "pla"},
        {"index": 1, "used_g": 1.5, "color_hex": "f330f9", "material": "pla"},  # purge
    ]
    app.threemf_enabled = True
    app._job_key = "nonrehydrated_regression_001"
    app._start_snapshot = {3: 500.0}
    app._spool_id_snapshot = {3: 30}
    app._trays_used = {3}
    app._rehydrated = False
    app._print_active = True

    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(30, remaining=478.0)

    app._do_finish("finish")

    recorder.assert_used(30, 22.0)
    assert _has_log(app, "3MF_MATCH")
    assert _has_log(app, "3MF_UNMATCHED")
    assert not _has_log(app, "REHYDRATE_READMIT_SLOTS")


# ---------------------------------------------------------------------------
# EOL spool auto-archive tests
# ---------------------------------------------------------------------------

def test_archive_fires_when_depleted_and_flag_enabled():
    """auto_archive_depleted_spools=True + remaining=0 → archive PATCH fires."""
    app = _TestableUsageSync(
        state_map={},
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True,
              "auto_archive_depleted_spools": True},
    )
    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(47, remaining=0.0)

    decision = SlotDecision(
        slot=3, spool_id=47, consumption_g=432.0,
        method="depleted_nonrfid", skip_reason=None, confidence="low",
    )
    app._execute_writes([decision], "archive_test")

    archive_calls = [c for c in recorder.patch_calls
                     if c["payload"].get("archived") is True]
    assert len(archive_calls) == 1
    assert archive_calls[0]["spool_id"] == 47
    assert _has_log(app, "SPOOL_ARCHIVED")


def test_archive_does_not_fire_when_flag_disabled():
    """auto_archive_depleted_spools=False (default) + remaining=0 → no archive."""
    app = _TestableUsageSync(
        state_map={},
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
    )
    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(47, remaining=0.0)

    decision = SlotDecision(
        slot=3, spool_id=47, consumption_g=432.0,
        method="depleted_nonrfid", skip_reason=None, confidence="low",
    )
    app._execute_writes([decision], "archive_disabled_test")

    archive_calls = [c for c in recorder.patch_calls
                     if c["payload"].get("archived") is True]
    assert len(archive_calls) == 0
    assert _has_log(app, "USAGE_SPOOL_DEPLETED")
    assert not _has_log(app, "SPOOL_ARCHIVED")


def test_archive_does_not_fire_when_not_depleted():
    """auto_archive_depleted_spools=True + remaining=45.0 → no archive."""
    app = _TestableUsageSync(
        state_map={},
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True,
              "auto_archive_depleted_spools": True},
    )
    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch
    recorder.set_use_response(47, remaining=45.0)

    decision = SlotDecision(
        slot=3, spool_id=47, consumption_g=50.0,
        method="rfid_delta", skip_reason=None, confidence="high",
    )
    app._execute_writes([decision], "archive_not_depleted_test")

    archive_calls = [c for c in recorder.patch_calls
                     if c["payload"].get("archived") is True]
    assert len(archive_calls) == 0
    assert not _has_log(app, "SPOOL_ARCHIVED")
    assert not _has_log(app, "USAGE_SPOOL_DEPLETED")


def test_archive_failure_does_not_block_unbind():
    """Archive exception must not prevent auto_empty_spools unbind."""
    app = _TestableUsageSync(
        state_map={},
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True,
              "auto_archive_depleted_spools": True, "auto_empty_spools": True},
    )
    app._is_tray_physically_present = lambda slot: False

    call_count = [0]
    original_patch = SpoolmanRecorder().patch

    def failing_then_ok_patch(spool_id, payload):
        call_count[0] += 1
        if payload.get("archived"):
            raise RuntimeError("Spoolman archive failed")
        return {"id": spool_id, **payload}

    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = failing_then_ok_patch
    recorder.set_use_response(47, remaining=0.0)

    decision = SlotDecision(
        slot=3, spool_id=47, consumption_g=432.0,
        method="depleted_nonrfid", skip_reason=None, confidence="low",
    )
    app._execute_writes([decision], "archive_fail_test")

    assert _has_log(app, "SPOOL_ARCHIVE_FAILED")
    # Unbind should still have fired despite archive failure
    binding_clears = [c for c in app._service_calls
                      if c.get("entity_id") == "input_text.ams_slot_3_spool_id"
                      and c.get("value") == "0"]
    assert len(binding_clears) == 1


def test_dry_run_suppresses_archive():
    """dry_run=True → no Spoolman calls at all, including archive."""
    app = _TestableUsageSync(
        state_map={},
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True,
              "auto_archive_depleted_spools": True, "dry_run": True},
    )
    recorder = SpoolmanRecorder()
    app._spoolman_use = recorder.use
    app._spoolman_patch = recorder.patch

    decision = SlotDecision(
        slot=3, spool_id=47, consumption_g=432.0,
        method="depleted_nonrfid", skip_reason=None, confidence="low",
    )
    app._execute_writes([decision], "dry_run_test")

    assert recorder.use_count == 0
    assert recorder.patch_count == 0
    assert _has_log(app, "WOULD_PATCH")
    assert not _has_log(app, "SPOOL_ARCHIVED")
