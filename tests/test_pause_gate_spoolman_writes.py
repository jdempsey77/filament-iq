"""
Pause-gate tests: with input_boolean.filament_iq_enabled OFF, no non-GET request
may reach Spoolman from reconcile or usage-sync; with it ON, the same scenario
must write.

These tests deliberately do NOT stub _spoolman_patch/_spoolman_post/_spoolman_use
(the other suites do, which is why the gate was never exercised there). The real
methods run; only urllib.request.urlopen is intercepted, so "no write" means "no
HTTP request left the process", not "a log line was absent".
"""

import os
import sys
import types
import unittest.mock as mock

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

from filament_iq.ams_print_usage_sync import AmsPrintUsageSync
from filament_iq.ams_rfid_reconcile import AmsRfidReconcile

from test_ams_print_usage_sync import _TestableUsageSync
from test_ams_rfid_reconcile import (
    FakeSpoolman,
    TestableReconcile,
    _spool,
    _tray_entity,
    _tray_state,
)

SWITCH = "input_boolean.filament_iq_enabled"


class _FakeResp:
    status = 200

    def __init__(self, body=b"{}"):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Wire:
    """Captures every urlopen call; GET-only calls are not writes."""

    def __init__(self):
        self.requests = []

    def urlopen(self, req, timeout=None):
        self.requests.append((req.get_method(), req.full_url))
        return _FakeResp(b'{"remaining_weight": 400.0, "id": 1}')

    @property
    def writes(self):
        return [r for r in self.requests if r[0] != "GET"]


# ── reconcile: real write chokepoints ────────────────────────────────

class _RealWriteReconcile(TestableReconcile):
    """TestableReconcile, but with the app's own Spoolman write methods and
    a switch-aware get_state. Reads stay on the in-memory FakeSpoolman."""

    _spoolman_patch = AmsRfidReconcile._spoolman_patch
    _spoolman_post = AmsRfidReconcile._spoolman_post

    def get_state(self, entity_id, attribute=None):
        if entity_id == SWITCH:
            return self._state_map.get(SWITCH)
        return super().get_state(entity_id, attribute)


def _reconcile_slot4_scenario(switch_state):
    """A known-UID spool at Shelf sits in AMS slot 4: reconcile must move it
    (PATCH location=AMS1_Slot4). Same scenario as
    test_slot_4_ok_patches_location_ams1_slot4."""
    tag = "C7D26F7B00000100"
    spools = [_spool(601, remaining_weight=500, rfid_tag_uid=tag, location="Shelf", color_hex="ff0000")]
    filaments = [{"id": 1, "name": "Bambu PLA Basic", "material": "PLA", "color_hex": "ff0000",
                  "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
    sm = FakeSpoolman(spools, filaments)
    state_map = {SWITCH: switch_state}
    for slot in range(1, 5):
        tray_ent = _tray_entity(slot)
        if slot == 4:
            state_map[tray_ent] = _tray_state(tag, tray_type="PLA", color="ff0000",
                                              name="Bambu PLA Basic", filament_id="bambu")
            state_map[f"{tray_ent}::all"] = {"attributes": _tray_state(tag)["attributes"], "state": "valid"}
        else:
            state_map[tray_ent] = _tray_state("", tray_type="", color="", name="", filament_id="")
            state_map[f"{tray_ent}::all"] = {"attributes": _tray_state("")["attributes"], "state": "empty"}
        state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
        state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
        state_map[f"input_text.ams_slot_{slot}_status"] = ""
    args = {"printer_serial": "01p00c5a3101668", "spoolman_url": "http://192.0.2.1:7912",
            "enabled": True, "debug_logs": False,
            "nonrfid_enabled_entity": "input_boolean.filament_iq_nonrfid_enabled"}
    r = _RealWriteReconcile(sm, state_map, args=args)
    return r


def _run(app, fn):
    wire = _Wire()
    with mock.patch("urllib.request.urlopen", wire.urlopen):
        fn()
    return wire


class TestReconcileGate:

    def test_switch_off_full_reconcile_run_sends_no_spoolman_write(self):
        r = _reconcile_slot4_scenario("off")
        wire = _run(r, lambda: r._run_reconcile("test"))
        assert wire.writes == [], f"paused reconcile wrote to Spoolman: {wire.writes}"
        # Prove the scenario really reached the write site (checked-and-none,
        # not could-not-check): the gate must have logged the suppressed PATCH.
        would = [m for m, _ in r._log_calls if m.startswith("WOULD_PATCH") and "spool/601" in m]
        assert would, "scenario never reached the PATCH site — test proves nothing"
        assert "AMS1_Slot4" in would[0]

    def test_switch_on_same_run_does_write(self):
        r = _reconcile_slot4_scenario("on")
        wire = _run(r, lambda: r._run_reconcile("test"))
        patches = [w for w in wire.writes if w[0] == "PATCH" and w[1].endswith("/api/v1/spool/601")]
        assert patches, f"unpaused reconcile did not PATCH spool 601; requests={wire.requests}"
        assert not [m for m, _ in r._log_calls if m.startswith("WOULD_")]

    def test_unresolved_switch_fails_open_and_writes(self):
        """Documented base-class behaviour: unknown/unavailable => enabled."""
        r = _reconcile_slot4_scenario("unavailable")
        wire = _run(r, lambda: r._run_reconcile("test"))
        assert [w for w in wire.writes if w[0] == "PATCH"]

    def test_post_gated_off_and_open_on(self):
        r = _reconcile_slot4_scenario("off")
        wire = _run(r, lambda: r._spoolman_post("/api/v1/spool", {"filament_id": 1}))
        assert wire.writes == []
        r = _reconcile_slot4_scenario("on")
        wire = _run(r, lambda: r._spoolman_post("/api/v1/spool", {"filament_id": 1}))
        assert [w[0] for w in wire.writes] == ["POST"]

    def test_patch_spool_fields_and_filament_patch_gated_off(self):
        """Callers that go through the other helpers (lot_nr enroll, filament color)."""
        r = _reconcile_slot4_scenario("off")

        def go():
            r._patch_spool_fields(601, {"lot_nr": "abc"})
            r._patch_spool_fields(601, {"location": "AMS1_Slot4"})
            assert r._spoolman_patch("/api/v1/filament/1", {"color_hex": "00ff00"}) is None

        wire = _run(r, go)
        assert wire.writes == []

    def test_color_sync_on_bind_paused_writes_nothing_and_reports_false(self):
        r = _reconcile_slot4_scenario("off")
        r._spoolman_get = lambda path: {"id": 601, "filament": {"id": 1, "color_hex": "111111"}}
        result = []
        wire = _run(r, lambda: result.append(r._sync_filament_color_on_bind(1, 601, "FF0000")))
        assert result == [False]
        assert wire.writes == []
        assert [m for m, _ in r._log_calls if m.startswith("COLOR_SYNC_DRYRUN")], \
            "did not reach the color PATCH site"

    def test_color_sync_on_bind_enabled_patches(self):
        r = _reconcile_slot4_scenario("on")
        r._spoolman_get = lambda path: {"id": 601, "filament": {"id": 1, "color_hex": "111111"}}
        result = []
        wire = _run(r, lambda: result.append(r._sync_filament_color_on_bind(1, 601, "FF0000")))
        assert result == [True]
        assert [w for w in wire.writes if w[1].endswith("/api/v1/filament/1")]


# ── usage-sync: real write chokepoints ───────────────────────────────

class _RealWriteUsageSync(_TestableUsageSync):
    _spoolman_use = AmsPrintUsageSync._spoolman_use
    _spoolman_patch = AmsPrintUsageSync._spoolman_patch

    def get_state(self, entity_id, attribute=None):
        if entity_id == SWITCH:
            return self._state_map.get(SWITCH)
        return super().get_state(entity_id, attribute)


class TestUsageSyncGate:

    def test_use_and_patch_blocked_when_paused(self):
        app = _RealWriteUsageSync(state_map={SWITCH: "off"})
        wire = _run(app, lambda: (app._spoolman_use(10, 5.0),
                                  app._spoolman_patch(10, {"remaining_weight": 0})))
        assert wire.writes == []

    def test_use_and_patch_write_when_enabled(self):
        app = _RealWriteUsageSync(state_map={SWITCH: "on"})
        wire = _run(app, lambda: (app._spoolman_use(10, 5.0),
                                  app._spoolman_patch(10, {"remaining_weight": 0})))
        assert [w[0] for w in wire.writes] == ["PUT", "PATCH"]

    def _swap_app(self, switch_state):
        app = _RealWriteUsageSync(
            state_map={
                SWITCH: switch_state,
                "input_text.ams_slot_1_spool_id": "10",
                "sensor.p1s_tray_1_fuel_gauge_remaining": "900.0",
            },
            args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
        )
        app._job_key = "swap_gate_test"
        app._start_snapshot = {1: 100.0}   # end 900 => raw_delta -800 => swap
        app._spool_id_snapshot = {1: 10}
        app._trays_used = {1}
        app._print_active = True
        return app

    def test_mid_print_swap_branch_sends_no_use_when_paused(self):
        app = self._swap_app("off")
        wire = _run(app, lambda: app._do_finish("finish"))
        swap_logged = [m for m, _ in app._log_calls if m.startswith("SPOOL_SWAP_DETECTED")]
        assert swap_logged, "scenario never hit the swap branch — test proves nothing"
        assert wire.writes == [], f"paused swap branch wrote: {wire.writes}"

    def test_mid_print_swap_branch_writes_when_enabled(self):
        app = self._swap_app("on")
        wire = _run(app, lambda: app._do_finish("finish"))
        assert [m for m, _ in app._log_calls if m.startswith("SPOOL_SWAP_DETECTED")]
        assert [w for w in wire.writes if w[0] == "PUT" and "/spool/10/use" in w[1]], wire.requests
