"""AMS tray colours are RRGGBBAA: a leading 00/ff is colour, not alpha.

normalize_color_hex used to read an 8-char colour starting with 00 or ff as AARRGGBB, so a black
tray ("000000FF") came out blue ("0000ff") and never matched the black filament in a two-colour
3MF. On an RFID slot the slicer fallback then had nothing to fall back to, and the use was lost.

Run: python3 -m pytest tests/test_tray_colour_rrggbbaa.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "appdaemon", "apps"))

from filament_iq.consumption_engine import SlotInput, decide_consumption
from filament_iq.threemf_parser import (
    match_filaments_to_slots,
    normalize_color,
    normalize_color_hex,
    parse_slice_info_file,
)


@pytest.mark.parametrize("raw, want", [
    ("000000FF", "000000"),     # black: was "0000ff" (blue)
    ("00AE42FF", "00ae42"),     # Bambu green: was "ae42ff"
    ("FF6A13FF", "ff6a13"),     # orange: was "6a13ff"
    ("FFFFFFFF", "ffffff"),
    ("8E9089FF", "8e9089"),     # gray: right before too (no leading 00/ff)
    ("161616FF", "161616"),
    ("000000", "000000"),       # 6-char values pass through
    ("8e9089", "8e9089"),
    ("#000000FF", "000000"),    # a leading #
    ("#00ae42", "00ae42"),
    ("00ae42ff", "00ae42"),     # lower case
    ("  #FF6A13FF  ", "ff6a13"),
    ("", ""),
    (None, ""),
    ("12345", ""),
    ("zzzzzz", ""),
    ("000000FFAA", ""),
])
def test_tray_colours_are_rrggbbaa(raw, want):
    assert normalize_color_hex(raw) == want


@pytest.mark.parametrize("raw", ["000000FF", "00AE42FF", "FF6A13FF", "8E9089FF", "#161616", "#000000FF"])
def test_tray_and_3mf_colours_normalize_the_same_way(raw):
    # 3MF slice_info colours (#RRGGBB or #RRGGBBAA) go through normalize_color; a tray colour
    # must come out the same, or the exact-colour match can never fire.
    assert normalize_color_hex(raw) == normalize_color(raw)


SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="index" value="1"/>
    <metadata key="weight" value="2.28"/>
    <filament id="1" tray_info_idx="GFA00" type="PLA" color="#8E9089" used_m="0.64" used_g="1.95"/>
    <filament id="2" tray_info_idx="GFL99" type="PLA" color="#000000" used_m="0.11" used_g="{black_g}"/>
  </plate>
</config>
"""


def _two_colour_print(tmp_path, black_g):
    """11-12 14:34: gray PLA on a non-RFID slot (4), black PLA on an RFID slot (1); a second black
    PLA spool sits in slot 3, unused. Tray colours as the Bambu integration reports them."""
    p = tmp_path / "slice_info.config"
    p.write_text(SLICE_INFO.format(black_g=black_g))
    filaments = parse_slice_info_file(str(p))
    trays = {1: ("000000FF", "PLA", 901), 3: ("000000FF", "PLA", 902), 4: ("8E9089FF", "PLA", 903)}
    slot_data = {s: {"color_hex": normalize_color_hex(c), "material": m.lower(), "spool_id": sid,
                     "lot_nr_color": ""} for s, (c, m, sid) in trays.items()}
    return filaments, slot_data


def test_two_colour_print_matches_both_slots(tmp_path):
    filaments, slot_data = _two_colour_print(tmp_path, black_g=0.32)
    matches, unmatched = match_filaments_to_slots(filaments, slot_data, trays_used={1, 4})
    got = {m["slot"]: (m["spool_id"], m["used_g"], m["method"]) for m in matches}
    assert unmatched == []
    assert got == {4: (903, 1.95, "exact_color_material"), 1: (901, 0.32, "exact_color_material")}


def test_the_old_reading_left_black_unmatched(tmp_path):
    # What the 14:34 print logged (3MF_UNMATCHED count=1 total_g=0.32): with the tray read as blue,
    # black PLA can't match slot 1.
    filaments, slot_data = _two_colour_print(tmp_path, black_g=0.32)
    slot_data[1]["color_hex"] = slot_data[3]["color_hex"] = "0000ff"
    matches, unmatched = match_filaments_to_slots(filaments, slot_data, trays_used={1, 4})
    assert [m["slot"] for m in matches] == [4]
    assert [round(f["used_g"], 2) for f in unmatched] == [0.32]


def _rfid_black(threemf_g):
    # The RFID gauge moved up (520 -> 540): no usable delta, so only the slicer figure can count.
    return SlotInput(slot=1, spool_id=901, is_rfid=True, tray_empty=False, tray_active_seconds=458.0,
                     start_g=520.0, end_g=540.0, threemf_used_g=threemf_g,
                     threemf_method="exact_color_material", spoolman_remaining=520.0, normal_finish=True)


def test_rfid_zero_delta_falls_back_to_the_matched_slicer_grams(tmp_path):
    filaments, slot_data = _two_colour_print(tmp_path, black_g=3.4)
    matches, _ = match_filaments_to_slots(filaments, slot_data, trays_used={1, 4})
    black = next(m for m in matches if m["slot"] == 1)
    d = decide_consumption([_rfid_black(black["used_g"])])[0]
    assert (d.method, d.consumption_g, d.skip_reason) == ("3mf", 3.4, "RFID_DELTA_ZERO_SLICER_FALLBACK")


def test_rfid_slicer_fallback_still_needs_the_minimum():
    # Pins today's behaviour, unchanged by this fix: the fallback only takes a slicer figure of at least
    # min_consumption_g (2 g), so the 0.32 g of black on 11-12 is still not charged. A non-RFID slot's
    # slicer figure is exempt from the minimum. Whether RFID should be too is a separate decision.
    d = decide_consumption([_rfid_black(0.32)])[0]
    assert d.method == "no_evidence" and d.skip_reason.startswith("BELOW_MIN")
