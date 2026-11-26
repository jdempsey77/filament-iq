"""Multi-plate .gcode.3mf: Tier 1 uses only the plate being printed (hand-built fixtures)."""

import os
import tempfile
import zipfile

from tests.test_wrong_3mf_guard import _App  # installs the fake hassapi first
from filament_iq import ams_print_usage_sync as aps
from filament_iq.threemf_parser import (
    parse_3mf_filaments,
    parse_3mf_plates,
    plate_index_from_name,
    select_plate,
)


def _slice_info(plates):
    """plates: list of (index, [(id, type, color, used_g), ...])"""
    out = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    for idx, fils in plates:
        out.append(f'<plate><metadata key="index" value="{idx}"/>')
        for fid, typ, col, g in fils:
            out.append(
                f'<filament id="{fid}" tray_info_idx="GFA00" type="{typ}" '
                f'color="{col}" used_m="1.0" used_g="{g}"/>'
            )
        out.append("</plate>")
    out.append("</config>")
    return "\n".join(out).encode()


THREE = [
    (1, [(1, "PLA", "#FF0000FF", 60.0), (2, "PLA", "#00FF00FF", 45.0)]),
    (2, [(1, "PLA", "#FF0000FF", 20.0), (4, "PETG", "#0000FFFF", 10.0)]),
    (3, [(2, "PLA", "#00FF00FF", 10.4)]),
]


def _plates(spec=THREE):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.gcode.3mf")
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("Metadata/slice_info.config", _slice_info(spec))
    return parse_3mf_plates(p), p


def test_parser_returns_per_plate_and_old_shape_still_flat():
    plates, path = _plates()
    assert [p["index"] for p in plates] == [1, 2, 3]
    assert plates[0]["total_g"] == 105.0
    assert plates[0]["filaments"][0]["color_hex"] == "ff0000"
    assert round(sum(f["used_g"] for f in parse_3mf_filaments(path)), 1) == 145.4


def test_plate_index_from_name():
    assert plate_index_from_name("Metadata/plate_2.gcode") == 2
    assert plate_index_from_name("x.gcode.3mf") is None
    assert plate_index_from_name(None) is None


def test_select_by_weight_unique():
    plates, _ = _plates()
    p, why = select_plate(plates, 105.0)
    assert (p["index"], why) == (1, "weight")


def test_select_by_index_even_if_weights_close():
    plates, _ = _plates([(1, [(1, "PLA", "#FF0000FF", 100.0)]),
                         (2, [(1, "PLA", "#FF0000FF", 105.0)])])
    p, why = select_plate(plates, 103.0, plate_hint=2)
    assert (p["index"], why) == (2, "index")


def test_two_plates_in_tolerance_no_index_is_not_chosen():
    plates, _ = _plates([(1, [(1, "PLA", "#FF0000FF", 100.0)]),
                         (2, [(1, "PLA", "#FF0000FF", 105.0)])])
    assert select_plate(plates, 103.0) == (None, "none")


def test_single_plate_not_selected():
    plates, _ = _plates([(1, [(1, "PLA", "#FF0000FF", 5.0)])])
    assert select_plate(plates, 5.0) == (None, "single")


def _fetch(app, plates, hint=None, filename="x.gcode.3mf"):
    flat = [f for p in plates for f in p["filaments"]]
    result = {"job_key": "job1", "attempt": 1, "status": "success", "task_name": "t",
              "timing": {}, "file_count": 1, "filename": filename, "found_dir": "/cache",
              "filaments": flat, "plates": plates, "plate_hint": hint}
    aps.AmsPrintUsageSync._on_3mf_fetched(app, {"fetch_result": result})


def test_app_uses_plate_1_only_no_mismatch():
    plates, _ = _plates()
    app = _App("105.0")
    _fetch(app, plates)
    assert sum(f["used_g"] for f in app._threemf_data) == 105.0
    assert len(app._threemf_data) == 2
    assert app.persisted == [False]
    assert any("PLATE_SELECTED plate=1 reason=weight" in m for _, m in app.logs)
    assert not any("3MF_WEIGHT_MISMATCH" in m for _, m in app.logs)


def test_app_uses_index_hint():
    plates, _ = _plates()
    app = _App("30.0")
    _fetch(app, plates, hint=2)
    assert sum(f["used_g"] for f in app._threemf_data) == 30.0
    assert any("PLATE_SELECTED plate=2 reason=index" in m for _, m in app.logs)


def test_app_ambiguous_keeps_discard():
    plates, _ = _plates([(1, [(1, "PLA", "#FF0000FF", 100.0)]),
                         (2, [(1, "PLA", "#FF0000FF", 105.0)])])
    app = _App("90.0")
    _fetch(app, plates)
    assert app._threemf_data is None
    assert app.persisted == [True]
    assert any("3MF_WEIGHT_MISMATCH" in m for _, m in app.logs)
