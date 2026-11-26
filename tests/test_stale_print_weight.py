"""IN-1178: a print_weight that stopped updating is stale; the file-name match still guards."""

import time

from tests.test_wrong_3mf_guard import _App  # installs the fake hassapi first
from filament_iq import ams_print_usage_sync as aps

SEEN = 1_800_000_000.0  # task first seen


class _A(_App):
    def __init__(self, weight, changed, seen=SEEN):
        super().__init__(weight)
        self._task_seen_ts = seen
        self._changed = changed

    def get_state(self, entity, attribute=None):
        if entity == self._print_weight_entity and attribute == "all":
            return {"state": self._state[entity], "last_changed": self._changed}
        return self._state.get(entity)


def _plates(*totals):
    return [{"index": i + 1, "total_g": t,
             "filaments": [{"index": 1, "used_g": t, "color_hex": "000000", "material": "pla"}]}
            for i, t in enumerate(totals)]


def _fetch(app, total, filename="sleeps_sign.gcode.3mf", task="sleeps_sign", plates=None, hint=None):
    result = {"job_key": "job1", "attempt": 1, "status": "success", "task_name": task,
              "timing": {}, "file_count": 1, "filename": filename, "found_dir": "/cache",
              "filaments": [{"index": 1, "used_g": total, "color_hex": "000000", "material": "pla"}],
              "plates": plates or [], "plate_hint": hint}
    aps.AmsPrintUsageSync._on_3mf_fetched(app, {"fetch_result": result})


def test_weight_changed_during_prepare_is_fresh_and_mismatch_discards():
    app = _A("105", SEEN + 30)
    _fetch(app, 150.4)
    assert app._threemf_data is None
    assert any("3MF_WEIGHT_MISMATCH" in m for _, m in app.logs)
    assert not any("3MF_WEIGHT_STALE" in m for _, m in app.logs)


def test_weight_just_under_60s_before_task_is_fresh():
    app = _A("105", SEEN - 59)
    _fetch(app, 150.4)
    assert app._threemf_data is None


def test_stale_weight_uses_3mf_when_name_matches():
    app = _A("105", SEEN - 61)
    _fetch(app, 150.4)
    assert app._threemf_data[0]["used_g"] == 150.4
    assert any("3MF_WEIGHT_STALE print_weight=105" in m for _, m in app.logs)
    assert app.persisted == [False]


def test_stale_weight_still_requires_the_name_match():
    app = _A("105", SEEN - 3600)
    _fetch(app, 150.4, filename="dremel_cleat.3mf")
    assert app._threemf_data is None
    assert app.persisted == [True]


def test_stale_multi_plate_without_hint_is_discarded():
    app = _A("105", SEEN - 3600)
    _fetch(app, 150.4, plates=_plates(105.0, 150.4))
    assert app._threemf_data is None
    assert app.persisted == [True]


def test_stale_multi_plate_with_hint_uses_that_plate():
    app = _A("105", SEEN - 3600)
    _fetch(app, 255.4, plates=_plates(105.0, 150.4), hint=2)
    assert app._threemf_data[0]["used_g"] == 150.4


def test_ha_restart_fresh_last_changed_mismatch_discards():
    app = _A("105", SEEN + 5000)  # restart reset last_changed to now
    _fetch(app, 150.4)
    assert app._threemf_data is None
    assert any("3MF_WEIGHT_MISMATCH" in m for _, m in app.logs)


def test_unknown_cases_keep_today():
    app = _A("105", None)
    _fetch(app, 150.4)
    assert app._threemf_data is None
    app = _A("105", SEEN - 3600, seen=None)
    _fetch(app, 150.4)
    assert app._threemf_data is None


def test_parse_ts_and_stale_rule():
    assert aps._parse_ha_ts("2026-11-24T23:16:00+00:00") is not None
    assert aps._parse_ha_ts("unknown") is None
    assert aps.weight_is_stale(100.0, 200.0) and not aps.weight_is_stale(140.0, 200.0)


def test_task_first_seen_uses_entity_last_changed():
    class App:
        _task_name_entity = "sensor.t"

        def get_state(self, e, attribute=None):
            return {"state": "x", "last_changed": "2026-11-26T08:00:00+00:00"}

    ts = aps.AmsPrintUsageSync._task_first_seen_ts(App())
    assert ts == aps._parse_ha_ts("2026-11-26T08:00:00+00:00")
    App.get_state = lambda self, e, attribute=None: "x"
    assert abs(aps.AmsPrintUsageSync._task_first_seen_ts(App()) - time.time()) < 5
