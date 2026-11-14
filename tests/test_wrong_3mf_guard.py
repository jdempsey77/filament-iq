"""IN-1159: the native 3MF fetch must never use another print's file.

11-13 19:59, print sleeps_sign_r4b_Sign (printer weight 72.09 g): the first working FTPS fetch listed 258
files in /cache, none matched the task, and find_best_3mf's "newest" fallback took
dremel_charger_cleat_print_set.3mf (1 filament, black, 165 g)."""

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

from filament_iq.threemf_parser import find_best_3mf, slicer_weight_mismatch
from filament_iq import ams_print_usage_sync as aps


CACHE = ["benchy.3mf", "dremel_charger_cleat_print_set.3mf", "cube_a.3mf"]


def test_the_1113_listing_gives_no_file_instead_of_a_guess():
    assert find_best_3mf(CACHE, "sleeps_sign_r4b_Sign") is None


def test_a_real_name_still_matches():
    assert find_best_3mf(CACHE + ["sleeps_sign_r4b_Sign.gcode.3mf"], "sleeps_sign_r4b_Sign") == \
        "sleeps_sign_r4b_Sign.gcode.3mf"


def test_no_task_name_keeps_the_legacy_last_file():
    assert find_best_3mf(CACHE, "") == "cube_a.3mf"


class TestWeightGuard:
    def test_the_1113_file_is_rejected(self):
        assert "165.0" in slicer_weight_mismatch(165.0, 72.09)

    def test_the_right_file_passes(self):
        assert slicer_weight_mismatch(71.91, 72.09) == ""

    def test_unknown_printer_weight_never_rejects(self):
        assert slicer_weight_mismatch(165.0, 0) == ""
        assert slicer_weight_mismatch(165.0, None) == ""
        assert slicer_weight_mismatch(165.0, "unavailable") == ""

    def test_small_prints_get_15_g_slack(self):
        assert slicer_weight_mismatch(2.3 + 14.9, 2.3) == ""
        assert slicer_weight_mismatch(2.3 + 15.1, 2.3) != ""

    def test_large_prints_get_30_percent(self):
        assert slicer_weight_mismatch(129.0, 100.0) == ""
        assert slicer_weight_mismatch(131.0, 100.0) != ""
        assert slicer_weight_mismatch(69.0, 100.0) != ""


class _App:
    """Just enough of AmsPrintUsageSync for _on_3mf_fetched's success path."""

    def __init__(self, print_weight):
        self._job_key = "job1"
        self._print_weight_entity = "sensor.p_print_weight"
        self._state = {"sensor.p_print_weight": print_weight}
        self._threemf_data = None
        self._threemf_filename = None
        self._threemf_source_mtime = 0.0
        self._threemf_from_disk_restore = False
        self.logs, self.persisted = [], []

    def get_state(self, entity):
        return self._state.get(entity)

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


def _fetched(app, used_g):
    result = {"job_key": "job1", "attempt": 1, "status": "success", "task_name": "sleeps_sign_r4b_Sign",
              "timing": {}, "file_count": 258, "filename": "x.3mf", "found_dir": "/cache",
              "filaments": [{"index": 1, "used_g": used_g, "color_hex": "000000", "material": "pla"}]}
    aps.AmsPrintUsageSync._on_3mf_fetched(app, {"fetch_result": result})


def test_fetch_with_a_far_off_weight_is_discarded_and_marked_unavailable():
    app = _App("72.09")
    _fetched(app, 165.0)
    assert app._threemf_data is None
    assert app.persisted == [True]
    assert any("3MF_WEIGHT_MISMATCH" in m for _, m in app.logs)


def test_fetch_with_a_matching_weight_is_kept():
    app = _App("72.09")
    _fetched(app, 71.91)
    assert app._threemf_data and app._threemf_data[0]["used_g"] == 71.91
    assert app.persisted == [False]


def test_fetch_with_no_printer_weight_is_kept():
    app = _App("unavailable")
    _fetched(app, 165.0)
    assert app._threemf_data is not None
