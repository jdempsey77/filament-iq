"""Tier 4 of match_filaments_to_slots: pair leftover 3MF filaments with leftover used trays
(IN-1148, IN-1155). Shapes are the two 11-13 prints that left spool 119's red unrecorded."""

from filament_iq.threemf_parser import LEFTOVER_CLEAR_MARGIN, match_filaments_to_slots


def _ams():
    """The AMS as it was on 11-13: five PLA trays plus a PETG one (so material-only never fires)."""
    return {
        1: {"color_hex": "e0e0e0", "material": "PLA", "spool_id": 97},
        2: {"color_hex": "f72323", "material": "PLA", "spool_id": 119},   # Generic PLA+ red
        3: {"color_hex": "000000", "material": "PLA", "spool_id": 98},
        4: {"color_hex": "ffffff", "material": "PLA", "spool_id": 124},
        5: {"color_hex": "3f8e43", "material": "PLA", "spool_id": 10},    # Bambu Mistletoe Green
        6: {"color_hex": "808080", "material": "PETG", "spool_id": 56},
        7: {"color_hex": "1e90ff", "material": "PLA", "spool_id": 106},
    }


def _fil(index, color, used_g, material="PLA"):
    return {"index": index, "color_hex": color, "material": material, "used_g": used_g}


def _by_slot(matches):
    return {m["slot"]: m for m in matches}


def test_cube_b_one_leftover_pairs_red_with_slot_2():
    filaments = [_fil(2, "ffffff", 2.30), _fil(4, "c8181d", 36.62)]
    matches, unmatched = match_filaments_to_slots(filaments, _ams(), trays_used={2, 4})
    got = _by_slot(matches)
    assert unmatched == []
    assert got[4]["method"] == "exact_color_material"
    assert got[2]["method"] == "leftover_tray_material"
    assert got[2]["spool_id"] == 119 and got[2]["used_g"] == 36.62 and got[2]["filament_index"] == 4


def test_sign_two_leftovers_pair_by_nearest_colour():
    filaments = [_fil(1, "000000", 65.67), _fil(2, "ffffff", 2.29),
                 _fil(3, "00ae42", 1.83), _fil(4, "c8181d", 2.12)]
    matches, unmatched = match_filaments_to_slots(filaments, _ams(), trays_used={2, 3, 4, 5})
    got = _by_slot(matches)
    assert unmatched == []
    assert got[3]["method"] == got[4]["method"] == "exact_color_material"
    assert (got[2]["spool_id"], got[2]["used_g"], got[2]["method"]) == (119, 2.12, "leftover_tray_material")
    assert (got[5]["spool_id"], got[5]["used_g"], got[5]["method"]) == (10, 1.83, "leftover_tray_material")


def test_two_leftovers_without_a_clear_nearest_stay_unmatched():
    # Two blues against a red and an orange-red tray: every distance is large and close together.
    ams = _ams()
    ams[5] = {"color_hex": "e05020", "material": "PLA", "spool_id": 10}
    filaments = [_fil(1, "2020c0", 5.0), _fil(2, "3030b0", 5.0)]
    matches, unmatched = match_filaments_to_slots(filaments, ams, trays_used={2, 5})
    assert matches == []
    assert len(unmatched) == 2


def test_two_leftovers_with_the_same_nearest_tray_stay_unmatched():
    # Both reds are clearly nearest the red tray, so neither can be told apart for the green one.
    filaments = [_fil(1, "c8181d", 5.0), _fil(2, "b00000", 5.0)]
    matches, unmatched = match_filaments_to_slots(filaments, _ams(), trays_used={2, 5})
    assert matches == []
    assert len(unmatched) == 2


def test_equidistant_leftover_stays_unmatched_and_takes_the_others_with_it():
    # Filament 1 sits exactly between the two leftover trays (margin 0); filament 2 is clear
    # enough on its own (margin 48 < 60 still fails). All or nothing: both stay unmatched.
    ams = _ams()
    ams[5] = {"color_hex": "a00000", "material": "PLA", "spool_id": 10}
    ams[2] = {"color_hex": "000000", "material": "PLA", "spool_id": 119}
    ams[3] = {"color_hex": "404040", "material": "PLA", "spool_id": 98}
    filaments = [_fil(1, "500000", 5.0), _fil(2, "0000f0", 5.0)]
    matches, unmatched = match_filaments_to_slots(filaments, ams, trays_used={2, 5})
    assert matches == []
    assert len(unmatched) == 2


def test_margin_is_applied_at_the_limit():
    # One leftover filament with two same-material leftover trays can't happen (counts must be
    # equal), so check the limit with two filaments whose margins straddle LEFTOVER_CLEAR_MARGIN.
    ams = _ams()
    ams[2] = {"color_hex": "000000", "material": "PLA", "spool_id": 119}
    ams[5] = {"color_hex": "ff0000", "material": "PLA", "spool_id": 10}
    # distances from 000000 / ff0000: filament r -> (r, 255 - r); margin = 255 - 2r
    clear = int((255 - LEFTOVER_CLEAR_MARGIN) // 2)          # margin just over the limit
    murky = clear + 1                                       # margin just under
    near_red = f"{255 - clear:02x}0000"
    ok, _ = match_filaments_to_slots([_fil(1, f"{clear:02x}0000", 5.0), _fil(2, near_red, 5.0)],
                                     ams, trays_used={2, 5})
    assert {m["slot"] for m in ok} == {2, 5}
    bad, left = match_filaments_to_slots([_fil(1, f"{murky:02x}0000", 5.0), _fil(2, near_red, 5.0)],
                                         ams, trays_used={2, 5})
    assert bad == [] and len(left) == 2


def test_cross_material_stays_unmatched():
    filaments = [_fil(2, "ffffff", 2.30), _fil(4, "c8181d", 36.62, material="PETG")]
    matches, unmatched = match_filaments_to_slots(filaments, _ams(), trays_used={2, 4})
    assert [m["slot"] for m in matches] == [4]
    assert [f["index"] for f in unmatched] == [4]


def test_more_leftover_trays_than_filaments_stays_unmatched():
    filaments = [_fil(4, "c8181d", 36.62)]
    matches, unmatched = match_filaments_to_slots(filaments, _ams(), trays_used={2, 5})
    assert matches == []
    assert len(unmatched) == 1


def test_more_leftover_filaments_than_trays_stays_unmatched():
    filaments = [_fil(2, "ffffff", 2.30), _fil(3, "00ae42", 1.83), _fil(4, "c8181d", 36.62)]
    matches, unmatched = match_filaments_to_slots(filaments, _ams(), trays_used={2, 4})
    assert [m["slot"] for m in matches] == [4]
    assert len(unmatched) == 2


def test_no_trays_used_never_pairs():
    # Rehydrated prints pass trays_used=None: no idea which trays ran, so no pairing.
    filaments = [_fil(2, "ffffff", 2.30), _fil(4, "c8181d", 36.62)]
    matches, unmatched = match_filaments_to_slots(filaments, _ams(), trays_used=None)
    assert all(m["method"] != "leftover_tray_material" for m in matches)
    assert [f["index"] for f in unmatched] == [4]


def test_leftover_tray_without_a_spool_stays_unmatched():
    ams = _ams()
    ams[2] = {"color_hex": "f72323", "material": "PLA", "spool_id": 0}
    filaments = [_fil(2, "ffffff", 2.30), _fil(4, "c8181d", 36.62)]
    matches, unmatched = match_filaments_to_slots(filaments, ams, trays_used={2, 4})
    assert [m["slot"] for m in matches] == [4]
    assert [f["index"] for f in unmatched] == [4]


def test_never_pairs_to_a_tray_the_print_did_not_use():
    # Slot 2 matches the red but was not used; slot 7 (blue) was. One leftover each, same material:
    # the pairing goes to slot 7, the only used tray left, never to slot 2.
    filaments = [_fil(2, "ffffff", 2.30), _fil(4, "c8181d", 36.62)]
    matches, unmatched = match_filaments_to_slots(filaments, _ams(), trays_used={4, 7})
    assert unmatched == []
    assert 2 not in _by_slot(matches)
    assert _by_slot(matches)[7]["method"] == "leftover_tray_material"


# ── IN-1160 (gate follow-up on #128): a stale 3MF can't vouch for an elimination match ──

import sys as _sys
import types as _types

if "hassapi" not in _sys.modules:
    _h = _types.ModuleType("hassapi")

    class _FakeHass:
        def __init__(self, *a, **k):
            self.args = {}

    _h.Hass = _FakeHass
    _sys.modules["hassapi"] = _h

from filament_iq.ams_print_usage_sync import stale_elimination_slots  # noqa: E402

START = 1_794_601_786.0
MATCHED = {2: (36.62, "leftover_tray_material"), 4: (2.30, "exact_color_material"),
           5: (1.0, "single_filament_force")}


def test_stale_3mf_drops_leftover_and_force_matches_but_keeps_colour_matches():
    assert sorted(stale_elimination_slots(MATCHED, START - 31, START)) == [2, 5]


def test_fresh_3mf_keeps_everything():
    assert stale_elimination_slots(MATCHED, START - 30, START) == []
    assert stale_elimination_slots(MATCHED, START + 600, START) == []


def test_only_colour_matches_from_a_stale_3mf_are_kept():
    assert stale_elimination_slots({4: (2.3, "exact_color_material")}, 0.0, START) == []
