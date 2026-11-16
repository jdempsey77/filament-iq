#!/usr/bin/env python3
"""A print that finishes while AppDaemon is down is closed on the next start (IN-1234).

The printer went finish while AppDaemon was restarting. _rehydrate_print_state only looked at running/paused, so the
job stayed open: no usage written, print_active left on, active_print.json left behind. Now, when the printer reads
finish/failed and print_active is still on, the job is restored and the normal finish runs once (deferred 10 s).
"""
import json
import os
import sys
import types

import pytest

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

from test_ams_print_usage_sync import _TestableUsageSync  # noqa: E402

JOB = "Widget_plate_1-abc.gcode.3mf_1700000000"


def _app(tmp_path, status, print_active="on"):
    app = _TestableUsageSync(
        state_map={"input_text.ams_slot_1_spool_id": "97"},
        args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True, "data_dir": str(tmp_path)},
    )
    app._state_map[app._print_status_entity] = status
    app._state_map[app._print_active_entity] = print_active
    app._state_map[app._job_key_entity] = JOB
    app._state_map[app._start_json_entity] = json.dumps({"1": 510.0})
    app._active_print_file.write_text(json.dumps({
        "job_key": JOB, "trays_used": [1], "spool_id_snapshot": {"1": 97}, "threemf_data": None,
    }))
    app._service_calls.clear()
    app._run_in_calls.clear()
    return app


def _finish_timers(app):
    return [c for c in app._run_in_calls if getattr(c["callback"], "__name__", "") == "_finish_after_rehydrate"]


@pytest.mark.parametrize("status", ["finish", "failed"])
def test_a_print_that_ended_while_down_is_restored_and_finished(tmp_path, status):
    app = _app(tmp_path, status)
    app._rehydrate_print_state()

    assert app._print_active is True
    assert app._job_key == JOB                                   # from the helper
    assert app._trays_used == {1}                                # from active_print.json
    assert app._spool_id_snapshot == {1: 97}
    assert app._start_snapshot == {1: 510.0}
    assert not [c for c in app._service_calls if c["service"] == "input_boolean/turn_on"]   # never re-armed
    timers = _finish_timers(app)
    assert [(t["delay"], t["status"]) for t in timers] == [(10, status)]

    finished = []
    app._on_print_finish = lambda s: finished.append(s)
    timers[0]["callback"]({"status": status})
    assert finished == [status]


@pytest.mark.parametrize("status,helper", [("finish", "off"), ("failed", "off"), ("idle", "on"), ("finish", "unknown")])
def test_nothing_happens_without_an_open_job(tmp_path, status, helper):
    app = _app(tmp_path, status, print_active=helper)
    app._rehydrate_print_state()
    assert app._print_active is False
    assert _finish_timers(app) == []
    assert app._service_calls == []


@pytest.mark.parametrize("now", ["running", "printing", "prepare", "idle"])
def test_the_deferred_finish_skips_when_the_printer_moved_on(tmp_path, now):
    app = _app(tmp_path, "finish")
    app._rehydrate_print_state()
    app._state_map[app._print_status_entity] = now               # a new print began in the 10 s
    finished = []
    app._on_print_finish = lambda s: finished.append(s)
    _finish_timers(app)[0]["callback"]({"status": "finish"})
    assert finished == []


def test_the_deferred_finish_skips_a_job_already_closed(tmp_path):
    app = _app(tmp_path, "finish")
    app._rehydrate_print_state()
    app._print_active = False                                    # closed by another path meanwhile
    finished = []
    app._on_print_finish = lambda s: finished.append(s)
    _finish_timers(app)[0]["callback"]({"status": "finish"})
    assert finished == []


def test_a_running_print_still_rehydrates_as_before(tmp_path):
    app = _app(tmp_path, "running")
    app._rehydrate_print_state()
    assert app._print_active is True
    assert [c for c in app._service_calls if c["service"] == "input_boolean/turn_on"]
    assert _finish_timers(app) == []
