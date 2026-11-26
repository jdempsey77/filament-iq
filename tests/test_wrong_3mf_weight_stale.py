"""3MF_WEIGHT_STALE: a print_weight last changed before this print started is unknown."""

import sys
import types

if "hassapi" not in sys.modules:
    _hassapi = types.ModuleType("hassapi")

    class _FakeHass:
        def __init__(self, *a, **k):
            self.args = {}

        def log(self, msg, level="INFO"):
            pass

    _hassapi.Hass = _FakeHass
    sys.modules["hassapi"] = _hassapi

from filament_iq import ams_print_usage_sync as aps

START = 1_795_000_000.0  # epoch seconds of the print start


def _iso(ts):
    import datetime
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()


class _App:
    def __init__(self, weight, changed):
        self._job_key = "job1"
        self._print_weight_entity = "sensor.p1s_YOUR_PRINTER_SERIAL_print_weight"
        self._print_start_time = START
        self._weight, self._changed = weight, changed
        self._threemf_data = None
        self._threemf_filename = None
        self._threemf_source_mtime = 0.0
        self._threemf_from_disk_restore = False
        self.logs, self.persisted = [], []

    def get_state(self, entity, attribute=None):
        return {"state": self._weight, "last_changed": self._changed}

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def _persist_active_print(self, threemf_unavailable=False):
        self.persisted.append(threemf_unavailable)

    def _write_threemf_sensor(self):
        pass

    def _write_makerworld_sensors(self, **k):
        pass

    def run_in(self, *a, **k):
        pass


def _plate(idx, grams):
    f = [{"index": 1, "used_g": grams, "color_hex": "000000", "material": "pla"}]
    return {"index": idx, "filaments": f, "total_g": grams}


def _fetch(app, plates, hint=None):
    flat = [f for p in plates for f in p["filaments"]]
    result = {"job_key": "job1", "attempt": 1, "status": "success", "task_name": "t",
              "timing": {}, "file_count": 1, "filename": "x.gcode.3mf", "found_dir": "/cache",
              "filaments": flat, "plates": plates, "plate_hint": hint}
    aps.AmsPrintUsageSync._on_3mf_fetched(app, {"fetch_result": result})


STALE = _iso(START - 3600)
FRESH = _iso(START + 60)


def _stale_logged(app):
    return any("3MF_WEIGHT_STALE print_weight=105.0" in m and "- using 3MF" in m for _, m in app.logs)


def test_stale_single_plate_used():
    app = _App("105.0", STALE)
    _fetch(app, [_plate(1, 150.4)])
    assert app._threemf_data[0]["used_g"] == 150.4
    assert app.persisted == [False]
    assert _stale_logged(app)
    assert not any("3MF_WEIGHT_MISMATCH" in m for _, m in app.logs)


def test_stale_multi_plate_with_hint_uses_that_plate():
    app = _App("105.0", STALE)
    _fetch(app, [_plate(1, 105.0), _plate(2, 150.4)], hint=2)
    assert [f["used_g"] for f in app._threemf_data] == [150.4]
    assert _stale_logged(app)


def test_stale_multi_plate_no_hint_discards():
    app = _App("105.0", STALE)
    _fetch(app, [_plate(1, 105.0), _plate(2, 150.4)])
    assert app._threemf_data is None
    assert app.persisted == [True]


def test_fresh_mismatch_discards():
    app = _App("105.0", FRESH)
    _fetch(app, [_plate(1, 150.4 + 30)])
    assert app._threemf_data is None
    assert any("3MF_WEIGHT_MISMATCH" in m for _, m in app.logs)
    assert not _stale_logged(app)


def test_fresh_match_used():
    app = _App("105.0", FRESH)
    _fetch(app, [_plate(1, 104.0)])
    assert app._threemf_data[0]["used_g"] == 104.0
    assert not _stale_logged(app)
