"""Tests for threemf_parser module.

Run: python3 -m pytest tests/test_threemf_parser.py -v
"""
import os
import sys
import tempfile
import unicodedata
import zipfile

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "apps", "filament_iq")
)

from threemf_parser import (
    color_distance,
    find_best_3mf,
    match_filaments_to_slots,
    normalize_color,
    normalize_material,
    normalize_task_name,
    parse_3mf_filaments,
)


# ── Color Normalization ─────────────────────────────────────────────


class TestNormalizeColor:
    def test_8char_hex_drops_alpha(self):
        assert normalize_color("#00AE42FF") == "00ae42"

    def test_6char_hex(self):
        assert normalize_color("000000") == "000000"

    def test_with_hash_6char(self):
        assert normalize_color("#939393") == "939393"

    def test_8char_no_hash(self):
        assert normalize_color("161616FF") == "161616"

    def test_empty_string(self):
        assert normalize_color("") == ""

    def test_none(self):
        assert normalize_color(None) == ""

    def test_invalid_short(self):
        assert normalize_color("xyz") == ""

    def test_invalid_length_5(self):
        assert normalize_color("12345") == ""

    def test_uppercase_normalized_to_lower(self):
        assert normalize_color("#AABBCC") == "aabbcc"

    def test_mixed_case(self):
        assert normalize_color("AaBbCcFF") == "aabbcc"

    def test_whitespace_stripped(self):
        assert normalize_color("  #00AE42FF  ") == "00ae42"


# ── Material Normalization ───────────────────────────────────────────


class TestNormalizeMaterial:
    def test_uppercase(self):
        assert normalize_material("PLA") == "pla"

    def test_whitespace(self):
        assert normalize_material("  PETG  ") == "petg"

    def test_empty(self):
        assert normalize_material("") == ""

    def test_none(self):
        assert normalize_material(None) == ""

    def test_mixed_case(self):
        assert normalize_material("PLA-CF") == "pla-cf"


# ── Task Name Normalization ──────────────────────────────────────────


class TestNormalizeTaskName:
    def test_strip_3mf(self):
        assert normalize_task_name("My Print.3mf") == "my print"

    def test_strip_gcode_3mf(self):
        assert normalize_task_name("My Print.gcode.3mf") == "my print"

    def test_strip_gcode(self):
        assert normalize_task_name("My Print.gcode") == "my print"

    def test_underscores_and_dashes(self):
        assert normalize_task_name("My_Cool-Print") == "my cool print"

    def test_multiple_separators(self):
        assert normalize_task_name("My___Cool---Print") == "my cool print"

    def test_empty(self):
        assert normalize_task_name("") == ""

    def test_none(self):
        assert normalize_task_name(None) == ""

    def test_complex_bambu_name(self):
        result = normalize_task_name(
            "Overture_v2_-_0.2mm_layer,_2_walls,_15%_infill.gcode.3mf"
        )
        assert "overture v2" in result

    def test_normalize_task_name_with_emoji(self):
        """Unicode/emoji in filename is normalized for matching."""
        result = normalize_task_name("● 5x6 Drawer Set.3mf")
        assert "5x6 drawer set" in result

    def test_nfc_vs_nfd_umlaut(self):
        """NFC ö (U+00F6) and NFD o+combining-umlaut (U+006F U+0308) must match."""
        nfc = "Gehäuse-Deckel.3mf"  # ä as single codepoint
        nfd = unicodedata.normalize("NFD", "Gehäuse-Deckel.3mf")  # ä decomposed
        assert normalize_task_name(nfc) == normalize_task_name(nfd)

    def test_en_dash_vs_hyphen(self):
        """En dash (U+2013) and ASCII hyphen must normalize identically."""
        with_en_dash = "Part A\u2013Part B.3mf"
        with_hyphen = "Part A-Part B.3mf"
        assert normalize_task_name(with_en_dash) == normalize_task_name(with_hyphen)

    def test_em_dash_vs_hyphen(self):
        """Em dash (U+2014) and ASCII hyphen must normalize identically."""
        with_em_dash = "Part A\u2014Part B.3mf"
        with_hyphen = "Part A-Part B.3mf"
        assert normalize_task_name(with_em_dash) == normalize_task_name(with_hyphen)

    def test_unicode_symbols_stripped(self):
        """Unicode symbols ●★◉ are stripped but letters preserved."""
        result = normalize_task_name("●● 4x6 Double Height Drawer Set.3mf")
        assert result == "4x6 double height drawer set"

    def test_unicode_star_stripped(self):
        result = normalize_task_name("★ Special Print ◉.3mf")
        assert result == "special print"

    def test_clean_ascii(self):
        assert normalize_task_name("5x4x9U Box.3mf") == "5x4x9u box"

    def test_umlauts_preserved(self):
        """German umlauts should survive normalization."""
        result = normalize_task_name("Düsenhalter_für_Büro.3mf")
        assert "düsenhalter" in result
        assert "für" in result
        assert "büro" in result

    def test_slicer_suffix_preserved(self):
        """Slicer settings like 0.2mm, 15% should survive."""
        result = normalize_task_name("Box_0.2mm_layer,_2_walls,_15%_infill.gcode.3mf")
        assert "0.2mm" in result
        assert "15%" in result


# ── Color Distance ───────────────────────────────────────────────────


class TestColorDistance:
    def test_identical_black(self):
        assert color_distance("000000", "000000") == 0.0

    def test_identical_white(self):
        assert color_distance("ffffff", "ffffff") == 0.0

    def test_black_white_max_distance(self):
        dist = color_distance("000000", "ffffff")
        assert abs(dist - 441.67) < 1.0

    def test_similar_grays(self):
        dist = color_distance("939393", "8e9089")
        assert dist < 20

    def test_different_colors(self):
        dist = color_distance("00ae42", "000000")
        assert dist > 100

    def test_invalid_first(self):
        assert color_distance("", "000000") == 999.0

    def test_invalid_second(self):
        assert color_distance("000000", "xyz") == 999.0

    def test_both_invalid(self):
        assert color_distance("", "") == 999.0


# ── File Matching ────────────────────────────────────────────────────


class TestFindBest3mf:
    def test_exact_match(self):
        files = ["My Print.3mf", "Other.3mf"]
        assert find_best_3mf(files, "My Print") == "My Print.3mf"

    def test_contains_match_file_contains_task(self):
        files = ["My Cool Print v2.3mf", "Other.3mf"]
        assert find_best_3mf(files, "My Cool Print") == "My Cool Print v2.3mf"

    def test_contains_match_task_contains_file(self):
        files = ["Short.3mf", "Other.3mf"]
        assert find_best_3mf(files, "Short Name With More Words") == "Short.3mf"

    def test_fallback_newest(self):
        files = ["a.3mf", "b.3mf", "c.3mf"]
        assert find_best_3mf(files, "nonexistent") == "c.3mf"

    def test_no_task_name_returns_newest(self):
        files = ["a.3mf", "b.3mf"]
        assert find_best_3mf(files, "") == "b.3mf"

    def test_none_task_name(self):
        files = ["a.3mf", "b.3mf"]
        assert find_best_3mf(files, None) == "b.3mf"

    def test_empty_list(self):
        assert find_best_3mf([], "anything") is None

    def test_case_insensitive(self):
        files = ["MY PRINT.3mf"]
        assert find_best_3mf(files, "my print") == "MY PRINT.3mf"

    def test_extension_normalization(self):
        files = ["My Print.gcode.3mf"]
        assert find_best_3mf(files, "My Print.3mf") == "My Print.gcode.3mf"

    def test_find_best_3mf_with_emoji(self):
        """find_best_3mf matches filenames with emoji/unicode."""
        files = ["● 5x6 Drawer Set.3mf", "other.3mf"]
        result = find_best_3mf(files, "● 5x6 Drawer Set")
        assert result == "● 5x6 Drawer Set.3mf"


# ── Filament-to-Slot Matching ────────────────────────────────────────


class TestMatchFilamentsToSlots:
    def setup_method(self):
        self.filaments = [
            {"index": 0, "used_g": 1.29, "color_hex": "00ae42", "material": "pla"},
            {"index": 1, "used_g": 1.51, "color_hex": "000000", "material": "pla"},
            {"index": 2, "used_g": 0.61, "color_hex": "939393", "material": "pla"},
        ]
        # TODO: spool_id values are example (1, 2, 3, 4); substitute with your Spoolman spool IDs.
        self.slot_data = {
            1: {"color_hex": "00ae42", "material": "pla", "spool_id": 1},
            2: {"color_hex": "000000", "material": "pla", "spool_id": 2},
            3: {"color_hex": "1a1a1a", "material": "pla", "spool_id": 3},
            4: {"color_hex": "8e9089", "material": "pla", "spool_id": 4},
        }

    def test_exact_color_match_green(self):
        matches, _ = match_filaments_to_slots(self.filaments, self.slot_data)
        slot_map = {m["slot"]: m for m in matches}
        assert 1 in slot_map
        assert slot_map[1]["used_g"] == 1.29
        assert slot_map[1]["method"] == "exact_color_material"

    def test_exact_color_match_black(self):
        matches, _ = match_filaments_to_slots(self.filaments, self.slot_data)
        slot_map = {m["slot"]: m for m in matches}
        assert 2 in slot_map
        assert slot_map[2]["used_g"] == 1.51

    def test_close_color_match_gray(self):
        matches, _ = match_filaments_to_slots(self.filaments, self.slot_data)
        slot_map = {m["slot"]: m for m in matches}
        assert 4 in slot_map
        assert slot_map[4]["used_g"] == 0.61
        assert "close_color" in slot_map[4]["method"]

    def test_all_matched_no_unmatched(self):
        matches, unmatched = match_filaments_to_slots(
            self.filaments, self.slot_data
        )
        assert len(unmatched) == 0
        assert len(matches) == 3

    def test_trays_used_filter(self):
        matches, _ = match_filaments_to_slots(
            self.filaments, self.slot_data, trays_used={1, 2, 4}
        )
        assert len(matches) == 3
        assert all(m["slot"] in {1, 2, 4} for m in matches)

    def test_trays_used_excludes_needed_slot(self):
        matches, unmatched = match_filaments_to_slots(
            self.filaments, self.slot_data, trays_used={2, 4}
        )
        assert len(unmatched) == 1
        assert unmatched[0]["color_hex"] == "00ae42"

    def test_material_mismatch_no_match(self):
        petg_filaments = [
            {
                "index": 0,
                "used_g": 5.0,
                "color_hex": "000000",
                "material": "petg",
            },
        ]
        matches, unmatched = match_filaments_to_slots(
            petg_filaments, self.slot_data
        )
        assert len(matches) == 0
        assert len(unmatched) == 1

    def test_same_color_two_slots_first_wins(self):
        filaments = [
            {"index": 0, "used_g": 5.0, "color_hex": "000000", "material": "pla"},
            {"index": 1, "used_g": 3.0, "color_hex": "000000", "material": "pla"},
        ]
        slot_data = {
            2: {"color_hex": "000000", "material": "pla", "spool_id": 2},
            3: {"color_hex": "000000", "material": "pla", "spool_id": 3},
        }
        matches, _ = match_filaments_to_slots(filaments, slot_data)
        assert len(matches) == 2
        assert matches[0]["slot"] == 2
        assert matches[1]["slot"] == 3

    def test_zero_usage_skipped(self):
        filaments = [
            {"index": 0, "used_g": 0.0, "color_hex": "00ae42", "material": "pla"},
            {"index": 1, "used_g": 5.0, "color_hex": "000000", "material": "pla"},
        ]
        matches, _ = match_filaments_to_slots(filaments, self.slot_data)
        assert len(matches) == 1
        assert matches[0]["slot"] == 2

    def test_material_only_single_candidate(self):
        filaments = [
            {"index": 0, "used_g": 5.0, "color_hex": "ff0000", "material": "petg"},
        ]
        slot_data = {
            6: {"color_hex": "000000", "material": "petg", "spool_id": 6},
        }
        matches, _ = match_filaments_to_slots(filaments, slot_data)
        assert len(matches) == 1
        assert matches[0]["slot"] == 6
        assert matches[0]["method"] == "material_only_single"

    def test_material_only_multiple_candidates_no_match(self):
        filaments = [
            {"index": 0, "used_g": 5.0, "color_hex": "ff0000", "material": "petg"},
        ]
        slot_data = {
            5: {"color_hex": "000000", "material": "petg", "spool_id": 5},
            6: {"color_hex": "161616", "material": "petg", "spool_id": 6},
        }
        matches, unmatched = match_filaments_to_slots(filaments, slot_data)
        assert len(matches) == 0
        assert len(unmatched) == 1

    def test_empty_filaments_list(self):
        matches, unmatched = match_filaments_to_slots([], self.slot_data)
        assert matches == []
        assert unmatched == []

    def test_empty_slot_data(self):
        matches, unmatched = match_filaments_to_slots(self.filaments, {})
        assert matches == []
        assert len(unmatched) == 3

    def test_spool_id_zero_excluded(self):
        slot_data = {
            1: {"color_hex": "00ae42", "material": "pla", "spool_id": 0},
            2: {"color_hex": "000000", "material": "pla", "spool_id": 2},
        }
        filaments = [
            {"index": 0, "used_g": 1.29, "color_hex": "00ae42", "material": "pla"},
        ]
        matches, unmatched = match_filaments_to_slots(filaments, slot_data)
        assert len(matches) == 0
        assert len(unmatched) == 1

    def test_slot_used_once_only(self):
        filaments = [
            {"index": 0, "used_g": 5.0, "color_hex": "000000", "material": "pla"},
            {"index": 1, "used_g": 3.0, "color_hex": "010101", "material": "pla"},
        ]
        slot_data = {
            2: {"color_hex": "000000", "material": "pla", "spool_id": 2},
        }
        matches, unmatched = match_filaments_to_slots(filaments, slot_data)
        assert len(matches) == 1
        assert len(unmatched) == 1
        assert matches[0]["slot"] == 2
        assert matches[0]["used_g"] == 5.0


# ── Real Print Scenarios ─────────────────────────────────────────────


class TestMatchingWithRealPrintData:
    def test_4_colors_tower_print(self):
        filaments = [
            {"index": 0, "used_g": 1.29, "color_hex": "00ae42", "material": "pla"},
            {"index": 1, "used_g": 1.51, "color_hex": "000000", "material": "pla"},
            {"index": 2, "used_g": 0.61, "color_hex": "939393", "material": "pla"},
        ]
        slot_data = {
            1: {"color_hex": "00ae42", "material": "pla", "spool_id": 1},
            2: {"color_hex": "000000", "material": "pla", "spool_id": 2},
            3: {"color_hex": "1a1a1a", "material": "pla", "spool_id": 3},
            4: {"color_hex": "8e9089", "material": "pla", "spool_id": 4},
        }
        trays_used = {1, 2, 4}

        matches, unmatched = match_filaments_to_slots(
            filaments, slot_data, trays_used
        )
        assert len(matches) == 3
        assert len(unmatched) == 0

        slot_map = {m["slot"]: m for m in matches}
        assert slot_map[1]["used_g"] == 1.29
        assert slot_map[1]["spool_id"] == 1
        assert slot_map[2]["used_g"] == 1.51
        assert slot_map[2]["spool_id"] == 2
        assert slot_map[4]["used_g"] == 0.61
        assert slot_map[4]["spool_id"] == 4

    def test_single_spool_petg_print(self):
        filaments = [
            {"index": 0, "used_g": 6.75, "color_hex": "161616", "material": "petg"},
        ]
        slot_data = {
            5: {"color_hex": "000000", "material": "petg", "spool_id": 5},
            6: {"color_hex": "161616", "material": "petg", "spool_id": 6},
        }
        trays_used = {6}

        matches, _ = match_filaments_to_slots(
            filaments, slot_data, trays_used
        )
        assert len(matches) == 1
        assert matches[0]["slot"] == 6
        assert matches[0]["used_g"] == 6.75

    def test_mixed_material_print(self):
        filaments = [
            {"index": 0, "used_g": 10.0, "color_hex": "000000", "material": "pla"},
            {"index": 1, "used_g": 15.0, "color_hex": "000000", "material": "petg"},
        ]
        slot_data = {
            2: {"color_hex": "000000", "material": "pla", "spool_id": 2},
            6: {"color_hex": "161616", "material": "petg", "spool_id": 6},
        }
        trays_used = {2, 6}

        matches, _ = match_filaments_to_slots(
            filaments, slot_data, trays_used
        )
        assert len(matches) == 2
        slot_map = {m["slot"]: m for m in matches}
        assert slot_map[2]["used_g"] == 10.0
        assert slot_map[6]["used_g"] == 15.0

    def test_all_same_color_different_materials(self):
        filaments = [
            {"index": 0, "used_g": 5.0, "color_hex": "000000", "material": "pla"},
            {"index": 1, "used_g": 8.0, "color_hex": "000000", "material": "petg"},
            {"index": 2, "used_g": 3.0, "color_hex": "000000", "material": "tpu"},
        ]
        slot_data = {
            2: {"color_hex": "000000", "material": "pla", "spool_id": 2},
            5: {"color_hex": "000000", "material": "petg", "spool_id": 5},
            6: {"color_hex": "000000", "material": "tpu", "spool_id": 6},
        }
        matches, unmatched = match_filaments_to_slots(filaments, slot_data)
        assert len(matches) == 3
        assert len(unmatched) == 0


# ── 3MF Parsing Edge Cases ───────────────────────────────────────────


class TestParse3mfFilaments:
    def _make_3mf(self, xml_content, config_path="Metadata/slice_info.config"):
        tmp = tempfile.NamedTemporaryFile(suffix=".3mf", delete=False)
        with zipfile.ZipFile(tmp.name, "w") as zf:
            zf.writestr(config_path, xml_content)
        return tmp.name

    def test_single_filament(self):
        xml = """<?xml version="1.0"?>
        <config>
            <plate>
                <filament id="0" type="PLA" color="#00AE42FF" used_g="1.29" used_m="0.43"/>
            </plate>
        </config>"""
        path = self._make_3mf(xml)
        try:
            fils = parse_3mf_filaments(path)
            assert len(fils) == 1
            assert fils[0]["used_g"] == 1.29
            assert fils[0]["color_hex"] == "00ae42"
            assert fils[0]["material"] == "pla"
        finally:
            os.unlink(path)

    def test_multiple_filaments(self):
        xml = """<?xml version="1.0"?>
        <config>
            <plate>
                <filament id="0" type="PLA" color="#00AE42FF" used_g="1.29" used_m="0.43"/>
                <filament id="1" type="PLA" color="#000000FF" used_g="1.51" used_m="0.51"/>
                <filament id="2" type="PLA" color="#939393FF" used_g="0.61" used_m="0.21"/>
            </plate>
        </config>"""
        path = self._make_3mf(xml)
        try:
            fils = parse_3mf_filaments(path)
            assert len(fils) == 3
            assert fils[0]["index"] == 0
            assert fils[1]["index"] == 1
            assert fils[2]["index"] == 2
            assert abs(sum(f["used_g"] for f in fils) - 3.41) < 0.01
        finally:
            os.unlink(path)

    def test_missing_slice_info(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".3mf", delete=False)
        with zipfile.ZipFile(tmp.name, "w") as zf:
            zf.writestr("Metadata/other.config", "<config/>")
        try:
            fils = parse_3mf_filaments(tmp.name)
            assert fils == []
        finally:
            os.unlink(tmp.name)

    def test_invalid_zip(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".3mf", delete=False)
        tmp.write(b"this is not a zip file")
        tmp.close()
        try:
            fils = parse_3mf_filaments(tmp.name)
            assert fils == []
        finally:
            os.unlink(tmp.name)

    def test_malformed_xml(self):
        path = self._make_3mf("this is not xml <<<<")
        try:
            fils = parse_3mf_filaments(path)
            assert fils == []
        finally:
            os.unlink(path)

    def test_filament_missing_used_g(self):
        xml = """<?xml version="1.0"?>
        <config>
            <plate>
                <filament id="0" type="PLA" color="#000000FF"/>
            </plate>
        </config>"""
        path = self._make_3mf(xml)
        try:
            fils = parse_3mf_filaments(path)
            assert len(fils) == 1
            assert fils[0]["used_g"] == 0.0
        finally:
            os.unlink(path)

    def test_sorted_by_index(self):
        xml = """<?xml version="1.0"?>
        <config>
            <plate>
                <filament id="2" type="PLA" color="#939393FF" used_g="0.61"/>
                <filament id="0" type="PLA" color="#00AE42FF" used_g="1.29"/>
                <filament id="1" type="PLA" color="#000000FF" used_g="1.51"/>
            </plate>
        </config>"""
        path = self._make_3mf(xml)
        try:
            fils = parse_3mf_filaments(path)
            assert [f["index"] for f in fils] == [0, 1, 2]
        finally:
            os.unlink(path)

    def test_case_insensitive_config_path(self):
        xml = """<?xml version="1.0"?>
        <config><plate>
            <filament id="0" type="PLA" color="#000000FF" used_g="5.0"/>
        </plate></config>"""
        path = self._make_3mf(xml, config_path="Metadata/Slice_info.config")
        try:
            fils = parse_3mf_filaments(path)
            assert len(fils) == 1
        finally:
            os.unlink(path)


# ── FTP Error Handling ───────────────────────────────────────────────


class TestFtpErrorHandling:
    def test_ftps_list_bad_ip(self):
        from threemf_parser import ftps_list_cache

        # TODO: Use any unreachable IP for this test (expects empty list).
        files, directory = ftps_list_cache("192.0.2.254", "badcode", timeout=3)
        assert files == []
        assert directory is None

    def test_ftps_download_bad_ip(self):
        from threemf_parser import ftps_download_3mf

        # TODO: Use any unreachable IP for this test (expects None).
        with tempfile.TemporaryDirectory() as tmp:
            result = ftps_download_3mf(
                "192.0.2.254", "badcode", "test.3mf", tmp, timeout=3
            )
            assert result is None

    def test_find_best_empty_after_filter(self):
        assert find_best_3mf([], "test") is None

    def test_ftps_list_empty_code(self):
        from threemf_parser import ftps_list_cache

        # TODO: Substitute YOUR_PRINTER_IP for live test; 192.0.2.1 (TEST-NET) for CI.
        files, directory = ftps_list_cache("192.0.2.1", "", timeout=3)
        assert isinstance(files, list)
        assert directory is None

    def test_ftps_download_nonexistent_file(self):
        from threemf_parser import ftps_download_3mf

        # TODO: Substitute YOUR_PRINTER_IP for live test; 192.0.2.1 (TEST-NET) for CI.
        with tempfile.TemporaryDirectory() as tmp:
            result = ftps_download_3mf(
                "192.0.2.1", "badcode", "nonexistent.3mf", tmp, timeout=3
            )
            assert result is None


# ── Integration: Fallback Chain ──────────────────────────────────────


class TestFallbackChainLogic:
    def test_3mf_full_match_no_fallback(self):
        filaments = [
            {"index": 0, "used_g": 1.29, "color_hex": "00ae42", "material": "pla"},
            {"index": 1, "used_g": 1.51, "color_hex": "000000", "material": "pla"},
        ]
        slot_data = {
            1: {"color_hex": "00ae42", "material": "pla", "spool_id": 1},
            2: {"color_hex": "000000", "material": "pla", "spool_id": 2},
        }
        matches, unmatched = match_filaments_to_slots(filaments, slot_data)
        assert len(matches) == 2
        assert len(unmatched) == 0

    def test_3mf_partial_match_needs_fallback(self):
        filaments = [
            {"index": 0, "used_g": 1.29, "color_hex": "00ae42", "material": "pla"},
            {"index": 1, "used_g": 1.51, "color_hex": "ff0000", "material": "pla"},
        ]
        slot_data = {
            1: {"color_hex": "00ae42", "material": "pla", "spool_id": 1},
            2: {"color_hex": "000000", "material": "pla", "spool_id": 2},
        }
        matches, unmatched = match_filaments_to_slots(filaments, slot_data)
        assert len(matches) == 1
        assert len(unmatched) == 1

    def test_3mf_zero_matches_full_fallback(self):
        filaments = [
            {"index": 0, "used_g": 5.0, "color_hex": "ff0000", "material": "abs"},
        ]
        slot_data = {
            1: {"color_hex": "00ae42", "material": "pla", "spool_id": 1},
        }
        matches, unmatched = match_filaments_to_slots(filaments, slot_data)
        assert len(matches) == 0
        assert len(unmatched) == 1

    def test_3mf_overrides_rfid_fuel_gauge(self):
        threemf_g = 1.29
        fuel_gauge_g = 40.0
        assert threemf_g < fuel_gauge_g

    def test_3mf_unavailable_uses_time_weighted(self):
        tray_times = {2: 60.0, 6: 30.0}
        pool_g = 10.0
        total = sum(tray_times.values())
        slot_2_share = pool_g * tray_times[2] / total
        slot_6_share = pool_g * tray_times[6] / total
        assert abs(slot_2_share - 6.67) < 0.1
        assert abs(slot_6_share - 3.33) < 0.1
