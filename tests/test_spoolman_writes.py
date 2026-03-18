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
