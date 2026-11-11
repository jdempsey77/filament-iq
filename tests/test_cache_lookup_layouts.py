#!/usr/bin/env python3
"""
test_cache_lookup_layouts.py — `_try_cache_3mf` against both ha-bambulab layouts.

ha-bambulab has written print files in two shapes (file names below are real
task names; the printer serial directory is a placeholder):

  old:  <serial>/prints/cache/<n>-<task>.gcode
        <serial>/prints/cache/<n>-<task>.slice_info.config
        gcode-file sensor: "<n>-<task>.gcode"

  new:  <serial>/prints/<n>-<task>.gcode.gcode
        <serial>/prints/<n>-<task>.gcode.slice_info.config
        gcode-file sensor: "<n>-<task>.gcode.gcode"

`bambulab_cache_path` keeps pointing at `<serial>/prints/cache/` in both cases.
Task names may contain spaces ("cgd_r1_all_1 Shelf") or only underscores.
"""

import os
import time

import pytest

from test_ams_print_usage_sync import _TestableUsageSync, _has_log

GCODE_ENTITY = "sensor.p1s_test_gcode_file_downloaded"


def _slice_info(*filaments):
    """Build a slice_info.config body. filaments: (tray_info_idx, used_g, color)."""
    rows = "\n".join(
        f'    <filament id="{i}" used_g="{g}" used_m="1.0" color="{c}" '
        f'type="PLA" tray_info_idx="{idx}"/>'
        for i, (idx, g, c) in enumerate(filaments)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<config>\n  <plate>\n{rows}\n  </plate>\n</config>\n"
    )


@pytest.fixture
def cache(tmp_path):
    """Create <serial>/prints/cache/ and return (prints_dir, cache_dir, data_dir)."""
    prints = tmp_path / "YOUR_PRINTER_SERIAL" / "prints"
    cache_dir = prints / "cache"
    cache_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return prints, cache_dir, data_dir


def _app(cache, gcode_file, task, start_time=None):
    _prints, cache_dir, data_dir = cache
    app = _TestableUsageSync(
        args={
            # Trailing slash, exactly as the deployed config writes it.
            "bambulab_cache_path": str(cache_dir) + "/",
            "data_dir": str(data_dir),
        }
    )
    app._gcode_file_entity = GCODE_ENTITY
    app._state_map[GCODE_ENTITY] = gcode_file
    app._state_map[app._task_name_entity] = task
    app._print_start_time = start_time if start_time is not None else time.time()
    app._job_key = "test_job"
    return app


def _write(path, body, mtime=None):
    path.write_text(body, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


# ── old layout ────────────────────────────────────────────────────────


def test_old_layout_hits(cache):
    _prints, cache_dir, _ = cache
    _write(
        cache_dir / "987409-dremel_charger_cleat_print_set.slice_info.config",
        _slice_info(("GFL99", "145.43", "#8E9089FF")),
    )
    app = _app(
        cache,
        "987409-dremel_charger_cleat_print_set.gcode",
        "dremel_charger_cleat_print_set",
    )

    assert app._try_cache_3mf() is True
    assert [f["used_g"] for f in app._threemf_data] == [145.43]
    assert app._threemf_filename == (
        "987409-dremel_charger_cleat_print_set.slice_info.config"
    )
    assert _has_log(app, "3MF_CACHE_HIT")


# ── new layout ────────────────────────────────────────────────────────


def test_new_layout_hits_task_with_space(cache):
    """`<n>-<task>.gcode.gcode` in prints/, one level above the configured path."""
    prints, _cache_dir, _ = cache
    _write(
        prints / "244884-cgd_r1_all_1 Shelf.gcode.slice_info.config",
        _slice_info(("GFL99", "52.73", "#8E9089")),
    )
    app = _app(cache, "244884-cgd_r1_all_1 Shelf.gcode.gcode", "cgd_r1_all_1 Shelf")

    assert app._try_cache_3mf() is True
    assert [f["used_g"] for f in app._threemf_data] == [52.73]
    assert app._threemf_filename == (
        "244884-cgd_r1_all_1 Shelf.gcode.slice_info.config"
    )
    assert _has_log(app, "3MF_CACHE_HIT")
    assert not _has_log(app, "task_mismatch")


def test_new_layout_hits_task_with_underscores(cache):
    prints, _cache_dir, _ = cache
    _write(
        prints / "168902-cdxl_x1_coupons_chassis_Coupons.gcode.slice_info.config",
        _slice_info(("GFA00", "36.60", "#8E9089")),
    )
    app = _app(
        cache,
        "168902-cdxl_x1_coupons_chassis_Coupons.gcode.gcode",
        "cdxl_x1_coupons_chassis_Coupons",
    )

    assert app._try_cache_3mf() is True
    assert [f["used_g"] for f in app._threemf_data] == [36.6]


def test_new_layout_wrong_task_misses(cache):
    """The sensor still names the previous print's file: must not be used."""
    prints, _cache_dir, _ = cache
    _write(
        prints / "168902-cdxl_x1_coupons_chassis_Coupons.gcode.slice_info.config",
        _slice_info(("GFA00", "36.60", "#8E9089")),
    )
    app = _app(
        cache,
        "168902-cdxl_x1_coupons_chassis_Coupons.gcode.gcode",
        "cdxl_x1_coupons_chassis_Chassis",
    )

    assert app._try_cache_3mf() is False
    assert app._threemf_data is None
    assert _has_log(app, "3MF_CACHE_MISS reason=task_mismatch")


def test_new_layout_stale_file_misses(cache):
    """A file written well before this print started belongs to an earlier run."""
    prints, _cache_dir, _ = cache
    start = time.time()
    _write(
        prints / "244906-cgd_r1_all_6 Shelf.gcode.slice_info.config",
        _slice_info(("GFL99", "52.73", "#8E9089")),
        mtime=start - 3600,
    )
    app = _app(
        cache,
        "244906-cgd_r1_all_6 Shelf.gcode.gcode",
        "cgd_r1_all_6 Shelf",
        start_time=start,
    )

    assert app._try_cache_3mf() is False
    assert app._threemf_data is None
    assert _has_log(app, "3MF_CACHE_MISS reason=stale")


def test_new_layout_multi_plate_merge(cache):
    """Sibling plates (same <n> prefix) next to the found file are merged."""
    prints, _cache_dir, _ = cache
    _write(
        prints / "655217-Nightstand_Gridfinity_rB_Back-Left rB.gcode.slice_info.config",
        _slice_info(("GFA00", "36.80", "#000000")),
    )
    _write(
        prints / "655217-Nightstand_Gridfinity_rB_Back-Right rB.gcode.slice_info.config",
        _slice_info(("GFL99", "30.00", "#8E9089")),
    )
    # Different prefix: another upload, must not be merged.
    _write(
        prints / "654979-Nightstand_Gridfinity_rB_Back-Left rB.gcode.slice_info.config",
        _slice_info(("GFB00", "99.00", "#FFFFFF")),
    )
    app = _app(
        cache,
        "655217-Nightstand_Gridfinity_rB_Back-Left rB.gcode.gcode",
        "Nightstand_Gridfinity_rB_Back-Left rB",
    )

    assert app._try_cache_3mf() is True
    assert sorted(f["used_g"] for f in app._threemf_data) == [30.0, 36.8]
    assert _has_log(app, "3MF_CACHE_MULTI_PLATE plate_count=2")


def test_new_layout_does_not_merge_old_layout_siblings(cache):
    """Siblings are searched only in the directory the file was found in."""
    prints, cache_dir, _ = cache
    _write(
        prints / "244884-cgd_r1_all_1 Shelf.gcode.slice_info.config",
        _slice_info(("GFL99", "52.73", "#8E9089")),
    )
    _write(
        cache_dir / "244884-some_older_job.slice_info.config",
        _slice_info(("GFB00", "99.00", "#FFFFFF")),
    )
    app = _app(cache, "244884-cgd_r1_all_1 Shelf.gcode.gcode", "cgd_r1_all_1 Shelf")

    assert app._try_cache_3mf() is True
    assert [f["used_g"] for f in app._threemf_data] == [52.73]


# ── misses common to both ─────────────────────────────────────────────


def test_file_in_neither_directory_misses(cache):
    app = _app(cache, "244884-cgd_r1_all_1 Shelf.gcode.gcode", "cgd_r1_all_1 Shelf")

    assert app._try_cache_3mf() is False
    assert _has_log(app, "3MF_CACHE_MISS reason=file_not_found")


def test_no_cache_path_configured_misses(cache):
    app = _app(cache, "244884-cgd_r1_all_1 Shelf.gcode.gcode", "cgd_r1_all_1 Shelf")
    app._bambulab_cache_path = ""

    assert app._try_cache_3mf() is False
    assert app._cache_search_dirs() == []


# ── shared file finder (also used by the MakerWorld metadata readers) ──


def test_find_cache_file_covers_both_directories(cache):
    prints, cache_dir, _ = cache
    old_3mf = cache_dir / "987409-dremel_charger_cleat_print_set.3mf"
    new_3mf = prints / "244884-cgd_r1_all_1 Shelf.gcode.3mf"
    old_3mf.write_bytes(b"")
    new_3mf.write_bytes(b"")
    app = _app(cache, "", "")

    assert app._find_cache_file(old_3mf.name) == str(old_3mf)
    assert app._find_cache_file(new_3mf.name) == str(new_3mf)
    assert app._find_cache_file("nope.3mf") == ""
    assert app._find_cache_file("") == ""
