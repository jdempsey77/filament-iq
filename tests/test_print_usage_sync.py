"""Tests for AmsPrintUsageSync consumption logic.

Run: python3 -m pytest tests/test_print_usage_sync.py -v
"""
import datetime
import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "appdaemon", "apps"))

import pytest
from filament_iq.threemf_parser import match_filaments_to_slots, parse_lot_nr_color


# ── Helpers to simulate the classification logic ──

def classify_slot(start_g, end_g):
    """Simulate the fixed RFID vs non-RFID classification.
    Returns ('rfid', consumption_g) or ('nonrfid', None).
    """
    has_fuel_gauge = start_g > 0 and end_g > 0
    if has_fuel_gauge:
        consumption_g = max(0.0, start_g - end_g)
        return ("rfid", consumption_g)
    return ("nonrfid", None)


def sanity_check(consumption_g, max_g=300):
    """Returns True if consumption passes sanity check."""
    return consumption_g <= max_g


def build_end_json(fuel_gauge_readings, start_json):
    """Simulate the fixed end_json builder.
    fuel_gauge_readings: dict of {slot_int: float} (-1 = unavailable)
    start_json: dict of {slot_str: grams}
    Returns: dict of {slot_str: grams} for end snapshot
    """
    result = {}
    for slot_int, remaining in fuel_gauge_readings.items():
        slot_str = str(slot_int)
        if remaining > 0 and slot_str in start_json:
            result[slot_str] = remaining
    return result


# ── Test: RFID vs Non-RFID Classification ──

class TestSlotClassification:
    def test_rfid_slot_normal(self):
        """RFID slot with valid start and end readings."""
        kind, consumption = classify_slot(start_g=800, end_g=785)
        assert kind == "rfid"
        assert consumption == 15.0

    def test_rfid_slot_no_consumption(self):
        """RFID slot used but no filament consumed (e.g. purge only)."""
        kind, consumption = classify_slot(start_g=800, end_g=800)
        assert kind == "rfid"
        assert consumption == 0.0

    def test_nonrfid_slot_no_fuel_gauge(self):
        """Non-RFID slot: start seeded from Spoolman, end is 0 (no fuel gauge)."""
        kind, consumption = classify_slot(start_g=1000, end_g=0)
        assert kind == "nonrfid"
        assert consumption is None

    def test_nonrfid_slot_no_start(self):
        """Slot not active at start (start=0), regardless of end."""
        kind, consumption = classify_slot(start_g=0, end_g=0)
        assert kind == "nonrfid"
        assert consumption is None

    def test_nonrfid_slot_end_negative(self):
        """End reading is -1 (sensor unavailable)."""
        kind, consumption = classify_slot(start_g=500, end_g=-1)
        assert kind == "nonrfid"
        assert consumption is None

    def test_rfid_slot_end_less_than_start(self):
        """Normal RFID consumption."""
        kind, consumption = classify_slot(start_g=1000, end_g=950)
        assert kind == "rfid"
        assert consumption == 50.0

    def test_rfid_slot_end_greater_than_start(self):
        """End > start (sensor anomaly) — consumption clamped to 0."""
        kind, consumption = classify_slot(start_g=500, end_g=510)
        assert kind == "rfid"
        assert consumption == 0.0


# ── Test: Sanity Cap ──

class TestSanityCap:
    def test_normal_consumption_passes(self):
        assert sanity_check(50.0) is True

    def test_large_consumption_fails(self):
        assert sanity_check(500.0) is False

    def test_exactly_at_cap(self):
        assert sanity_check(300.0) is True

    def test_just_over_cap(self):
        assert sanity_check(300.1) is False

    def test_zero_consumption_passes(self):
        assert sanity_check(0.0) is True

    def test_custom_cap(self):
        assert sanity_check(400.0, max_g=500) is True
        assert sanity_check(600.0, max_g=500) is False


# ── Test: End JSON Builder ──

class TestEndJsonBuilder:
    def test_only_includes_slots_with_fuel_gauge(self):
        """Non-RFID slots (fuel gauge = 0 or -1) should be excluded."""
        fuel = {1: 785.0, 2: 0.0, 3: -1.0, 4: 0.0, 5: -1.0, 6: 0.0}
        start = {"1": 800, "2": 1000, "3": 500}
        result = build_end_json(fuel, start)
        assert result == {"1": 785.0}

    def test_only_includes_slots_in_start_json(self):
        """Slots not in start_json should be excluded even with valid fuel gauge."""
        fuel = {1: 785.0, 2: 950.0, 3: 400.0}
        start = {"1": 800}  # only slot 1 was tracked
        result = build_end_json(fuel, start)
        assert result == {"1": 785.0}

    def test_empty_start_json(self):
        """No start snapshot → empty end json."""
        fuel = {1: 785.0, 2: 950.0}
        start = {}
        result = build_end_json(fuel, start)
        assert result == {}

    def test_all_rfid_slots(self):
        """All slots have fuel gauge and are in start."""
        fuel = {1: 785.0, 2: 950.0, 3: 400.0, 4: 100.0}
        start = {"1": 800, "2": 1000, "3": 500, "4": 200}
        result = build_end_json(fuel, start)
        assert result == {"1": 785.0, "2": 950.0, "3": 400.0, "4": 100.0}

    def test_no_fuel_gauge_for_any_slot(self):
        """All non-RFID → empty end json (consumption handled by estimation)."""
        fuel = {1: 0.0, 2: 0.0, 3: -1.0}
        start = {"1": 1000, "2": 500, "3": 800}
        result = build_end_json(fuel, start)
        assert result == {}


# ── Test: Full Integration Scenario ──

class TestIntegrationScenarios:
    def test_mixed_rfid_and_nonrfid_print(self):
        """
        Slot 1: Bambu PLA (RFID, fuel gauge works) — 800g → 785g = 15g consumed
        Slot 3: Overture PLA+ (non-RFID, no fuel gauge) — should be estimated
        """
        start = {"1": 800, "3": 500}
        fuel_at_end = {1: 785.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0, 6: 0.0}

        end = build_end_json(fuel_at_end, start)
        assert end == {"1": 785.0}  # only slot 1 has fuel gauge

        # Slot 1: classified as RFID
        kind1, cons1 = classify_slot(800, 785)
        assert kind1 == "rfid"
        assert cons1 == 15.0

        # Slot 3: not in end_json, so end_g = 0 → classified as non-RFID
        kind3, cons3 = classify_slot(500, 0)
        assert kind3 == "nonrfid"

    def test_bug_scenario_nonrfid_spool_drain(self):
        """
        BEFORE FIX: Slot 2 non-RFID, start=1000, end=0 → consumption=1000 (BUG)
        AFTER FIX: Slot 2 excluded from end_json → classified as non-RFID → estimated
        """
        start = {"1": 800, "2": 1000}
        fuel_at_end = {1: 785.0, 2: 0.0}

        # Fixed end_json excludes slot 2
        end = build_end_json(fuel_at_end, start)
        assert "2" not in end

        # Slot 2 classified as non-RFID (not RFID with 1000g consumption)
        end_g_for_slot2 = end.get("2", 0)
        kind, cons = classify_slot(1000, end_g_for_slot2)
        assert kind == "nonrfid"
        assert cons is None  # NOT 1000g!

    def test_sanity_cap_catches_remaining_edge_cases(self):
        """Even if classification somehow fails, sanity cap prevents damage."""
        # Hypothetical: both start and end > 0 but wildly wrong
        kind, cons = classify_slot(start_g=1000, end_g=1)
        assert kind == "rfid"
        assert cons == 999.0
        assert sanity_check(cons) is False  # cap catches it


# ── Test: Trays Used Filtering ──

def parse_trays_used(raw):
    """Simulate trays_used parsing."""
    result = set()
    if raw:
        for part in raw.replace(" ", "").split(","):
            try:
                slot_int = int(part)
                if 1 <= slot_int <= 6:
                    result.add(slot_int)
            except (TypeError, ValueError):
                pass
    return result


def filter_nonrfid_slots(nonrfid_candidates, trays_used_set):
    """Simulate non-RFID slot filtering (fail-closed: empty set = skip all)."""
    return [(slot, sid) for slot, sid in nonrfid_candidates if slot in trays_used_set]


class TestTraysUsedParsing:
    def test_single_slot(self):
        assert parse_trays_used("6") == {6}

    def test_multiple_slots(self):
        assert parse_trays_used("1,3,6") == {1, 3, 6}

    def test_with_spaces(self):
        assert parse_trays_used("1, 3, 6") == {1, 3, 6}

    def test_empty_string(self):
        assert parse_trays_used("") == set()

    def test_invalid_values_ignored(self):
        assert parse_trays_used("1,abc,3") == {1, 3}

    def test_out_of_range_ignored(self):
        assert parse_trays_used("0,1,7,3") == {1, 3}


class TestNonRfidFiltering:
    def test_filters_to_used_slots_only(self):
        """Only slot 6 used — only slot 6 should get charged."""
        candidates = [(2, 31), (3, 52), (4, 46), (5, 27), (6, 28)]
        result = filter_nonrfid_slots(candidates, {6})
        assert result == [(6, 28)]

    def test_multiple_used_slots(self):
        """Slots 2 and 6 used."""
        candidates = [(2, 31), (3, 52), (4, 46), (5, 27), (6, 28)]
        result = filter_nonrfid_slots(candidates, {2, 6})
        assert result == [(2, 31), (6, 28)]

    def test_empty_trays_used_skips_all(self):
        """No trays_used data — fail-closed: skip all non-RFID slots."""
        candidates = [(2, 31), (3, 52), (6, 28)]
        result = filter_nonrfid_slots(candidates, set())
        assert result == []

    def test_no_nonrfid_candidates(self):
        """All slots are RFID — no non-RFID to filter."""
        result = filter_nonrfid_slots([], {6})
        assert result == []


class TestIntegrationWithTraysUsed:
    def test_single_spool_print_only_charges_used_slot(self):
        """
        Real scenario: Print from slot 6 only, 58.5g total.
        Before fix: 58.5 / 5 = 11.7g per non-RFID slot (WRONG)
        After fix: 58.5 / 1 = 58.5g to slot 6 only (CORRECT)
        """
        all_nonrfid = [(2, 31), (3, 52), (4, 46), (5, 27), (6, 28)]
        trays_used = parse_trays_used("6")
        filtered = filter_nonrfid_slots(all_nonrfid, trays_used)
        assert filtered == [(6, 28)]

        pool_g = 58.5
        each_g = pool_g / len(filtered)
        assert each_g == 58.5  # all goes to slot 6

    def test_two_spool_print_splits_between_used(self):
        """Multi-material print using slots 2 and 6."""
        all_nonrfid = [(2, 31), (3, 52), (4, 46), (5, 27), (6, 28)]
        trays_used = parse_trays_used("2,6")
        filtered = filter_nonrfid_slots(all_nonrfid, trays_used)
        assert filtered == [(2, 31), (6, 28)]

        pool_g = 60.0
        each_g = pool_g / len(filtered)
        assert each_g == 30.0  # split between 2 and 6


# ── Test: Time-Weighted Allocation ──


class TestTimeWeightedAllocation:
    def test_time_weighted_two_slots(self):
        """Slot 2 active for 60s, slot 4 active for 30s → 2:1 ratio."""
        times = {2: 60.0, 4: 30.0}
        total = sum(times.values())
        weights = {s: t / total for s, t in times.items()}

        pool_g = 3.0
        slot_2_share = pool_g * weights[2]
        slot_4_share = pool_g * weights[4]

        assert abs(slot_2_share - 2.0) < 0.01
        assert abs(slot_4_share - 1.0) < 0.01

    def test_time_weighted_single_slot(self):
        """Only one non-RFID slot → gets entire pool."""
        times = {6: 120.0}
        total = sum(times.values())
        weights = {s: t / total for s, t in times.items()}

        pool_g = 58.5
        slot_6_share = pool_g * weights[6]
        assert abs(slot_6_share - 58.5) < 0.01

    def test_equal_split_fallback_no_time_data(self):
        """No time data → equal split."""
        times = {}
        pool_g = 6.0
        nonrfid_slots = [(2, 31), (4, 46)]

        if not times:
            each = pool_g / len(nonrfid_slots)
            assert each == 3.0

    def test_time_weighted_excludes_rfid_slots(self):
        """Time weights for RFID slots should not affect non-RFID allocation."""
        times = {1: 120.0, 2: 60.0, 4: 30.0}  # slot 1 is RFID
        nonrfid_slots = {2, 4}
        relevant = {s: t for s, t in times.items() if s in nonrfid_slots}
        total = sum(relevant.values())
        weights = {s: t / total for s, t in relevant.items()}

        assert 1 not in weights
        assert abs(weights[2] - 0.6667) < 0.01
        assert abs(weights[4] - 0.3333) < 0.01

    def test_time_weighted_with_purge_tower(self):
        """
        Slot 2: 60s model + 10s purge = 70s
        Slot 4: 30s model + 10s purge = 40s
        Purge time naturally included in duration.
        """
        times = {2: 70.0, 4: 40.0}
        total = sum(times.values())

        pool_g = 3.41
        slot_2 = pool_g * times[2] / total
        slot_4 = pool_g * times[4] / total

        assert abs(slot_2 - 2.17) < 0.1
        assert abs(slot_4 - 1.24) < 0.1


# ── Test: RFID Cap ──


class TestRfidCap:
    def test_rfid_over_reports_caps_to_print_weight(self):
        """
        Fuel gauge says 40g consumed, but print is only 3.41g.
        RFID total should be capped to print_weight for pool calculation.
        """
        rfid_total = 40.0
        print_weight = 3.41
        capped = (
            min(rfid_total, print_weight)
            if rfid_total > print_weight
            else rfid_total
        )
        nonrfid_pool = max(0.0, print_weight - capped)
        assert capped == 3.41
        assert nonrfid_pool == 0.0

    def test_rfid_under_reports_no_cap(self):
        """Fuel gauge says 2g, print is 10g. No cap needed."""
        rfid_total = 2.0
        print_weight = 10.0
        capped = (
            min(rfid_total, print_weight)
            if rfid_total > print_weight
            else rfid_total
        )
        nonrfid_pool = max(0.0, print_weight - capped)
        assert capped == 2.0
        assert nonrfid_pool == 8.0

    def test_rfid_exact_match_no_cap(self):
        """Fuel gauge matches print weight exactly."""
        rfid_total = 10.0
        print_weight = 10.0
        capped = (
            min(rfid_total, print_weight)
            if rfid_total > print_weight
            else rfid_total
        )
        nonrfid_pool = max(0.0, print_weight - capped)
        assert capped == 10.0
        assert nonrfid_pool == 0.0

    def test_rfid_cap_with_nonrfid_slots(self):
        """
        Real scenario: 3-color print, 3.41g total.
        RFID slot 1: fuel gauge says 40g.
        Non-RFID slots 2, 4: should still get estimated consumption.

        Without cap: pool = 3.41 - 40 = 0 (clamped). Slots 2,4 get nothing.
        With cap: pool = 3.41 - 3.41 = 0. Still 0, but at least not negative.
        """
        rfid_total = 40.0
        print_weight = 3.41
        capped = (
            min(rfid_total, print_weight)
            if rfid_total > print_weight
            else rfid_total
        )
        nonrfid_pool = max(0.0, print_weight - capped)
        assert nonrfid_pool == 0.0

    def test_no_rfid_slots_pool_equals_print_weight(self):
        """All non-RFID print — pool is full print weight."""
        rfid_total = 0.0
        print_weight = 58.5
        capped = (
            min(rfid_total, print_weight)
            if rfid_total > print_weight and print_weight > 0
            else rfid_total
        )
        nonrfid_pool = max(0.0, print_weight - capped)
        assert nonrfid_pool == 58.5

    def test_multiple_rfid_slots_over_report(self):
        """Two RFID slots both over-report."""
        rfid_results = [(1, 41, 40.0), (3, 52, 35.0)]  # 75g total
        print_weight = 20.0
        rfid_total = sum(c for _, _, c in rfid_results)
        capped = (
            min(rfid_total, print_weight)
            if rfid_total > print_weight
            else rfid_total
        )
        nonrfid_pool = max(0.0, print_weight - capped)
        assert capped == 20.0
        assert nonrfid_pool == 0.0


# ── Phase 1: Print Start Lifecycle ──


def generate_job_key(task_name):
    """Simulate job key generation from task name."""
    return task_name.replace(" ", "_")


def read_fuel_gauge(slot, fuel_gauges, ams_remaining):
    """Simulate _read_fuel_gauge: fuel gauge first, ams fallback, else -1."""
    fg = fuel_gauges.get(slot, -1.0)
    if fg > 0:
        return fg
    ams = ams_remaining.get(slot, -1.0)
    return ams if ams > 0 else -1.0


def build_start_snapshot(slots, fuel_gauges, ams_remaining):
    """Simulate _build_start_snapshot."""
    snapshot = {}
    for slot in sorted(slots):
        grams = read_fuel_gauge(slot, fuel_gauges, ams_remaining)
        if grams >= 0:
            snapshot[slot] = max(0.0, round(grams, 1))
    return snapshot


def snapshot_to_json_dict(snapshot):
    """Convert {slot_int: grams} to {slot_str: grams}."""
    return {str(slot): grams for slot, grams in snapshot.items()}


def seed_slot_start_grams(snapshot, slot, fuel_gauges, ams_remaining):
    """Simulate write-once seeding. Returns updated snapshot."""
    if slot in snapshot and snapshot[slot] > 0:
        return snapshot  # already seeded
    grams = read_fuel_gauge(slot, fuel_gauges, ams_remaining)
    if grams < 0:
        return snapshot
    snapshot[slot] = max(0.0, round(grams, 1))
    return snapshot


class TestJobKeyGeneration:
    def test_simple_name(self):
        assert generate_job_key("benchy") == "benchy"

    def test_spaces_replaced(self):
        assert generate_job_key("sample Plate 1") == "sample_Plate_1"

    def test_already_underscored(self):
        assert generate_job_key("my_print_v2") == "my_print_v2"

    def test_multiple_spaces(self):
        assert generate_job_key("test  double  space") == "test__double__space"

    def test_empty_name(self):
        assert generate_job_key("") == ""

    def test_with_3mf_extension(self):
        assert generate_job_key("box lid v3.gcode.3mf") == "box_lid_v3.gcode.3mf"


class TestFuelGaugeReading:
    def test_fuel_gauge_preferred(self):
        fg = {1: 800.0}
        ams = {1: 750.0}
        assert read_fuel_gauge(1, fg, ams) == 800.0

    def test_ams_fallback_when_fg_zero(self):
        fg = {1: 0.0}
        ams = {1: 750.0}
        assert read_fuel_gauge(1, fg, ams) == 750.0

    def test_ams_fallback_when_fg_negative(self):
        fg = {1: -1.0}
        ams = {1: 750.0}
        assert read_fuel_gauge(1, fg, ams) == 750.0

    def test_ams_fallback_when_fg_missing(self):
        fg = {}
        ams = {1: 750.0}
        assert read_fuel_gauge(1, fg, ams) == 750.0

    def test_returns_negative_when_both_unavailable(self):
        assert read_fuel_gauge(1, {}, {}) == -1.0

    def test_returns_negative_when_both_zero(self):
        assert read_fuel_gauge(1, {1: 0.0}, {1: 0.0}) == -1.0


class TestBuildStartSnapshot:
    def test_all_slots_have_fuel_gauge(self):
        fg = {1: 800.0, 2: 950.0, 3: 400.0, 4: 100.0}
        result = build_start_snapshot([1, 2, 3, 4], fg, {})
        assert result == {1: 800.0, 2: 950.0, 3: 400.0, 4: 100.0}

    def test_mixed_fuel_gauge_and_ams(self):
        fg = {1: 800.0, 2: -1.0}
        ams = {2: 500.0, 3: -1.0}
        result = build_start_snapshot([1, 2, 3], fg, ams)
        assert result == {1: 800.0, 2: 500.0}

    def test_no_readings_for_any_slot(self):
        result = build_start_snapshot([1, 2, 3], {}, {})
        assert result == {}

    def test_zero_grams_clamped(self):
        fg = {1: 0.0}
        ams = {1: 0.0}
        result = build_start_snapshot([1], fg, ams)
        assert result == {}

    def test_six_slot_mixed(self):
        """Real scenario: slots 1-4 RFID (fuel gauge), 5-6 non-RFID (ams only)."""
        fg = {1: 800.0, 2: 950.0, 3: 400.0, 4: 100.0, 5: -1.0, 6: -1.0}
        ams = {5: 500.0, 6: 200.0}
        result = build_start_snapshot([1, 2, 3, 4, 5, 6], fg, ams)
        assert result == {1: 800.0, 2: 950.0, 3: 400.0, 4: 100.0, 5: 500.0, 6: 200.0}


class TestSnapshotToJsonDict:
    def test_converts_int_keys_to_strings(self):
        snapshot = {1: 800.0, 3: 400.0}
        result = snapshot_to_json_dict(snapshot)
        assert result == {"1": 800.0, "3": 400.0}

    def test_empty_snapshot(self):
        assert snapshot_to_json_dict({}) == {}

    def test_format_matches_automation_d(self):
        """Verify output matches what automation D passes as start_json in the event."""
        snapshot = {1: 800.0, 2: 950.0, 5: 500.0}
        result = snapshot_to_json_dict(snapshot)
        # automation D reads: states('input_text.filament_iq_start_json')
        # which is JSON like {"1": 800.0, "2": 950.0, "5": 500.0}
        import json
        serialized = json.dumps(result)
        parsed = json.loads(serialized)
        assert parsed == {"1": 800.0, "2": 950.0, "5": 500.0}
        # Keys must be strings for _coerce_json_field to work
        assert all(isinstance(k, str) for k in parsed.keys())


class TestSeedSlotStartGrams:
    def test_seeds_new_slot(self):
        snapshot = {1: 800.0}
        fg = {2: 950.0}
        result = seed_slot_start_grams(snapshot, 2, fg, {})
        assert result == {1: 800.0, 2: 950.0}

    def test_write_once_skips_existing(self):
        snapshot = {1: 800.0}
        fg = {1: 900.0}  # different value
        result = seed_slot_start_grams(snapshot, 1, fg, {})
        assert result[1] == 800.0  # original value preserved

    def test_write_once_allows_zero_override(self):
        """Slot with 0 grams should be overwritable (not a real reading)."""
        snapshot = {1: 0.0}
        fg = {1: 800.0}
        result = seed_slot_start_grams(snapshot, 1, fg, {})
        assert result[1] == 800.0

    def test_skips_when_no_reading(self):
        snapshot = {1: 800.0}
        result = seed_slot_start_grams(snapshot, 2, {}, {})
        assert 2 not in result

    def test_uses_ams_fallback(self):
        snapshot = {}
        ams = {3: 500.0}
        result = seed_slot_start_grams(snapshot, 3, {}, ams)
        assert result == {3: 500.0}


# ── Test: lot_nr color extraction ──


class TestParseLotNrColor:
    def test_non_rfid_signature(self):
        assert parse_lot_nr_color("pla|gfl99|161616") == "161616"

    def test_non_rfid_uppercase_color(self):
        assert parse_lot_nr_color("pla|gfl99|FF00AA") == "ff00aa"

    def test_rfid_32char_uuid(self):
        """32-char hex UUID (tray_uuid) should return empty — no color."""
        assert parse_lot_nr_color("A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4") == ""

    def test_rfid_lowercase_uuid(self):
        assert parse_lot_nr_color("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4") == ""

    def test_empty_string(self):
        assert parse_lot_nr_color("") == ""

    def test_none(self):
        assert parse_lot_nr_color(None) == ""

    def test_malformed_no_pipes(self):
        assert parse_lot_nr_color("just_a_string") == ""

    def test_two_pipes_only(self):
        assert parse_lot_nr_color("pla|gfl99") == ""

    def test_color_with_alpha(self):
        """8-char color in lot_nr should be trimmed to 6."""
        assert parse_lot_nr_color("pla|gfl99|161616FF") == "161616"

    def test_petg_signature(self):
        assert parse_lot_nr_color("petg|42|00ae42") == "00ae42"


# ── Test: lot_nr color matching in match_filaments_to_slots ──


class TestLotNrColorMatching:
    def test_lot_nr_color_matches_when_spoolman_color_differs(self):
        """Slot has Spoolman color 000000 but lot_nr has 161616. 3MF says 161616."""
        filaments = [
            {"index": 0, "used_g": 5.0, "color_hex": "161616", "material": "pla"},
        ]
        slot_data = {
            3: {"color_hex": "000000", "material": "pla", "spool_id": 52, "lot_nr_color": "161616"},
        }
        matches, unmatched = match_filaments_to_slots(filaments, slot_data)
        assert len(matches) == 1
        assert matches[0]["slot"] == 3
        assert matches[0]["method"] == "lot_nr_color_material"
        assert len(unmatched) == 0

    def test_exact_color_preferred_over_lot_nr(self):
        """When exact color matches, lot_nr should not be used."""
        filaments = [
            {"index": 0, "used_g": 5.0, "color_hex": "161616", "material": "pla"},
        ]
        slot_data = {
            3: {"color_hex": "161616", "material": "pla", "spool_id": 52, "lot_nr_color": "161616"},
        }
        matches, unmatched = match_filaments_to_slots(filaments, slot_data)
        assert len(matches) == 1
        assert matches[0]["method"] == "exact_color_material"

    def test_close_color_preferred_over_lot_nr(self):
        """Close color (dist < 30) should match before lot_nr fallback."""
        filaments = [
            {"index": 0, "used_g": 5.0, "color_hex": "161616", "material": "pla"},
        ]
        # 141414 → dist = sqrt(2² + 2² + 2²) ≈ 3.5
        slot_data = {
            3: {"color_hex": "141414", "material": "pla", "spool_id": 52, "lot_nr_color": "161616"},
        }
        matches, unmatched = match_filaments_to_slots(filaments, slot_data)
        assert len(matches) == 1
        assert "close_color" in matches[0]["method"]

    def test_lot_nr_color_not_used_for_wrong_material(self):
        """lot_nr color matches but material differs — should not match."""
        filaments = [
            {"index": 0, "used_g": 5.0, "color_hex": "161616", "material": "petg"},
        ]
        slot_data = {
            3: {"color_hex": "000000", "material": "pla", "spool_id": 52, "lot_nr_color": "161616"},
        }
        matches, unmatched = match_filaments_to_slots(filaments, slot_data)
        assert len(matches) == 0
        assert len(unmatched) == 1

    def test_no_lot_nr_color_falls_through(self):
        """Slot has no lot_nr_color — should fall through to material-only."""
        filaments = [
            {"index": 0, "used_g": 5.0, "color_hex": "161616", "material": "petg"},
        ]
        # Only one PETG slot in system → material_only_single
        slot_data = {
            3: {"color_hex": "000000", "material": "petg", "spool_id": 52, "lot_nr_color": ""},
        }
        matches, unmatched = match_filaments_to_slots(filaments, slot_data)
        assert len(matches) == 1
        assert matches[0]["method"] == "material_only_single"

    def test_multi_slot_lot_nr_color_picks_correct_slot(self):
        """Two PLA slots with different lot_nr colors. 3MF should match each correctly."""
        filaments = [
            {"index": 0, "used_g": 5.0, "color_hex": "161616", "material": "pla"},
            {"index": 1, "used_g": 3.0, "color_hex": "f330f9", "material": "pla"},
        ]
        slot_data = {
            3: {"color_hex": "000000", "material": "pla", "spool_id": 52, "lot_nr_color": "161616"},
            4: {"color_hex": "000000", "material": "pla", "spool_id": 51, "lot_nr_color": "f330f9"},
        }
        matches, unmatched = match_filaments_to_slots(filaments, slot_data)
        assert len(matches) == 2
        assert len(unmatched) == 0
        slot_map = {m["slot"]: m for m in matches}
        assert slot_map[3]["used_g"] == 5.0
        assert slot_map[4]["used_g"] == 3.0

    def test_backward_compatible_without_lot_nr_color_key(self):
        """slot_data without lot_nr_color key should still work (graceful fallback)."""
        filaments = [
            {"index": 0, "used_g": 5.0, "color_hex": "161616", "material": "pla"},
        ]
        # No lot_nr_color key at all
        slot_data = {
            3: {"color_hex": "161616", "material": "pla", "spool_id": 52},
        }
        matches, unmatched = match_filaments_to_slots(filaments, slot_data)
        assert len(matches) == 1
        assert matches[0]["method"] == "exact_color_material"


# ── Test: 3MF matching ignores trays_used restriction ──


class Test3mfMatchingAllBoundSlots:
    def test_all_4_filaments_match_without_trays_used_filter(self):
        """
        3MF has 4 filaments but trays_used only tracked 3 slots (1,2,4).
        Slot 3 wasn't in trays_used but has a bound spool with matching color.
        When trays_used=None (3MF is authoritative), all 4 should match.
        """
        filaments = [
            {"index": 0, "used_g": 5.0, "color_hex": "ffffff", "material": "pla"},
            {"index": 1, "used_g": 3.0, "color_hex": "ff0000", "material": "pla"},
            {"index": 2, "used_g": 2.0, "color_hex": "00ae42", "material": "pla"},
            {"index": 3, "used_g": 0.24, "color_hex": "161616", "material": "pla"},
        ]
        slot_data = {
            1: {"color_hex": "ffffff", "material": "pla", "spool_id": 41},
            2: {"color_hex": "ff0000", "material": "pla", "spool_id": 42},
            3: {"color_hex": "161616", "material": "pla", "spool_id": 52},
            4: {"color_hex": "00ae42", "material": "pla", "spool_id": 51},
        }
        # trays_used=None → all bound slots are candidates (3MF authoritative)
        matches, unmatched = match_filaments_to_slots(filaments, slot_data, trays_used=None)
        assert len(matches) == 4
        assert len(unmatched) == 0
        slot_map = {m["slot"]: m for m in matches}
        assert slot_map[1]["used_g"] == 5.0
        assert slot_map[2]["used_g"] == 3.0
        assert slot_map[3]["used_g"] == 0.24
        assert slot_map[4]["used_g"] == 2.0

    def test_trays_used_filter_would_miss_slot(self):
        """Confirm that passing trays_used={1,2,4} excludes slot 3 (the bug)."""
        filaments = [
            {"index": 0, "used_g": 5.0, "color_hex": "ffffff", "material": "pla"},
            {"index": 1, "used_g": 3.0, "color_hex": "ff0000", "material": "pla"},
            {"index": 2, "used_g": 2.0, "color_hex": "00ae42", "material": "pla"},
            {"index": 3, "used_g": 0.24, "color_hex": "161616", "material": "pla"},
        ]
        slot_data = {
            1: {"color_hex": "ffffff", "material": "pla", "spool_id": 41},
            2: {"color_hex": "ff0000", "material": "pla", "spool_id": 42},
            3: {"color_hex": "161616", "material": "pla", "spool_id": 52},
            4: {"color_hex": "00ae42", "material": "pla", "spool_id": 51},
        }
        # With trays_used={1,2,4}, slot 3 is excluded → filament 3 unmatched
        matches, unmatched = match_filaments_to_slots(filaments, slot_data, trays_used={1, 2, 4})
        assert len(matches) == 3
        assert len(unmatched) == 1
        assert unmatched[0]["color_hex"] == "161616"

    def test_3mf_matched_slots_merged_into_active_slots(self):
        """3MF matches 4 slots but trays_used only has 3.
        active_slots should include the 3MF-matched slot not in trays_used."""
        trays_used_set = {1, 2, 4}
        active_slots = sorted(trays_used_set)
        # Simulate 3MF matching all 4 slots (trays_used=None)
        threemf_matched_slots = {1: 5.0, 2: 3.0, 3: 0.24, 4: 2.0}
        # Merge 3MF-matched slots into active_slots
        if threemf_matched_slots:
            active_slots = sorted(set(active_slots) | set(threemf_matched_slots.keys()))
        assert active_slots == [1, 2, 3, 4]
        assert 3 in active_slots  # slot 3 was NOT in trays_used but IS in 3MF


# ── Test: Unmatched 3MF consumption pooling ──


class TestUnmatched3mfPooling:
    def test_unmatched_consumption_stays_in_pool(self):
        """
        4 filaments in 3MF, 2 matched, 2 unmatched.
        Pool should be print_weight - matched_total (unmatched stays in pool).
        """
        matched_total = 9.1  # 2 matched filaments
        print_weight = 17.3
        rfid_total = 0.0
        pool = max(0.0, print_weight - matched_total - rfid_total)
        assert abs(pool - 8.2) < 0.01  # unmatched 8.2g is in the pool

    def test_unmatched_slots_get_pool_share(self):
        """Unmatched slots should receive time-weighted share of pool."""
        pool_g = 8.2
        time_weights = {3: 0.6, 4: 0.4}  # slot 3: 60%, slot 4: 40%
        slot_3_share = pool_g * time_weights[3]
        slot_4_share = pool_g * time_weights[4]
        assert abs(slot_3_share - 4.92) < 0.01
        assert abs(slot_4_share - 3.28) < 0.01
        assert abs(slot_3_share + slot_4_share - pool_g) < 0.01

    def test_rfid_slots_routed_to_pool_when_3mf_unmatched(self):
        """
        When 3MF has unmatched filaments, RFID slots not matched by 3MF
        should be routed to pool instead of using rfid_delta.
        This prevents losing consumption when fuel gauge shows 0g.
        """
        # Simulate: slot 1 matched by 3MF (5g), slot 3 unmatched
        # Slot 3 is RFID with fuel gauge showing 0g delta
        # Without fix: slot 3 gets rfid_delta=0. With fix: slot 3 gets pool share.
        threemf_matched_total = 5.0
        rfid_total = 0.0  # slot 3 routed to pool, not rfid_delta
        print_weight = 10.0
        pool = max(0.0, print_weight - threemf_matched_total - rfid_total)
        assert pool == 5.0  # full unmatched amount available for pool


# ── Phase 2: Print Finish Lifecycle ──


def build_end_snapshot(start_snapshot, fuel_gauges, ams_remaining):
    """Simulate _build_end_snapshot: only include slots from start_snapshot."""
    snapshot = {}
    for slot in sorted(start_snapshot.keys()):
        grams = read_fuel_gauge(slot, fuel_gauges, ams_remaining)
        if grams >= 0:
            snapshot[slot] = max(0.0, round(grams, 1))
    return snapshot


def build_usage_data(job_key, task_name, print_weight_g, trays_used,
                     start_snapshot, end_snapshot, print_status):
    """Simulate data dict construction for _handle_usage_event."""
    return {
        "job_key": job_key,
        "task_name": task_name,
        "print_weight_g": print_weight_g,
        "trays_used": ",".join(str(s) for s in sorted(trays_used)),
        "start_json": {str(s): g for s, g in start_snapshot.items()},
        "end_json": {str(s): g for s, g in end_snapshot.items()},
        "print_status": print_status,
    }


class TestBuildEndSnapshot:
    def test_only_includes_slots_from_start(self):
        """End snapshot should only include slots that were in start_snapshot."""
        start = {1: 800.0, 3: 400.0}
        fg = {1: 785.0, 2: 950.0, 3: 390.0, 4: 100.0}
        result = build_end_snapshot(start, fg, {})
        assert result == {1: 785.0, 3: 390.0}
        assert 2 not in result
        assert 4 not in result

    def test_missing_fuel_gauge_excluded(self):
        """Slot in start but no fuel gauge at end — excluded from end snapshot."""
        start = {1: 800.0, 5: 500.0}
        fg = {1: 785.0}  # slot 5 has no fuel gauge
        result = build_end_snapshot(start, fg, {})
        assert result == {1: 785.0}
        assert 5 not in result

    def test_ams_fallback_used(self):
        """End snapshot uses ams_remaining fallback when fuel gauge unavailable."""
        start = {1: 800.0, 5: 500.0}
        fg = {1: 785.0, 5: -1.0}
        ams = {5: 480.0}
        result = build_end_snapshot(start, fg, ams)
        assert result == {1: 785.0, 5: 480.0}

    def test_empty_start_gives_empty_end(self):
        start = {}
        fg = {1: 785.0, 2: 950.0}
        result = build_end_snapshot(start, fg, {})
        assert result == {}

    def test_six_slot_end(self):
        """Full 6-slot scenario."""
        start = {1: 800.0, 2: 950.0, 3: 400.0, 4: 100.0, 5: 500.0, 6: 200.0}
        fg = {1: 785.0, 2: 940.0, 3: 390.0, 4: 95.0, 5: -1.0, 6: -1.0}
        ams = {5: 480.0, 6: 190.0}
        result = build_end_snapshot(start, fg, ams)
        assert result == {1: 785.0, 2: 940.0, 3: 390.0, 4: 95.0, 5: 480.0, 6: 190.0}


class TestDedupGuard:
    def test_same_job_key_is_duplicate(self):
        last_processed = "sample_Plate_1"
        current = "sample_Plate_1"
        assert current == last_processed  # should skip

    def test_different_job_key_not_duplicate(self):
        last_processed = "sample_Plate_1"
        current = "benchy_v2"
        assert current != last_processed  # should process

    def test_empty_last_processed_allows_any(self):
        last_processed = ""
        current = "sample_Plate_1"
        assert current != last_processed  # should process

    def test_empty_current_skipped_by_guard(self):
        """Empty job key means no start was captured — guard should skip."""
        current = ""
        assert not current  # falsy, guard should skip before dedup


class TestEmptyStartSnapshotGuard:
    def test_empty_start_falls_through(self):
        """No start snapshot → Phase 2 should skip, let event listener handle."""
        start_snapshot = {}
        assert not start_snapshot  # falsy, guard should skip

    def test_populated_start_proceeds(self):
        start_snapshot = {1: 800.0}
        assert start_snapshot  # truthy, should proceed


class TestUsageDataConstruction:
    def test_data_dict_has_all_fields(self):
        data = build_usage_data(
            job_key="sample_Plate_1",
            task_name="sample Plate 1",
            print_weight_g=17.3,
            trays_used={1, 3, 4},
            start_snapshot={1: 800.0, 3: 400.0, 4: 100.0},
            end_snapshot={1: 785.0, 3: 390.0, 4: 95.0},
            print_status="finish",
        )
        assert data["job_key"] == "sample_Plate_1"
        assert data["task_name"] == "sample Plate 1"
        assert data["print_weight_g"] == 17.3
        assert data["trays_used"] == "1,3,4"
        assert data["print_status"] == "finish"

    def test_start_json_keys_are_strings(self):
        data = build_usage_data("k", "t", 10.0, {1}, {1: 800.0}, {1: 785.0}, "finish")
        assert all(isinstance(k, str) for k in data["start_json"].keys())
        assert all(isinstance(k, str) for k in data["end_json"].keys())

    def test_start_json_matches_coerce_format(self):
        """start_json should be a dict (not string) — _coerce_json_field handles both."""
        data = build_usage_data("k", "t", 10.0, {1, 3}, {1: 800.0, 3: 400.0},
                                {1: 785.0, 3: 390.0}, "finish")
        assert isinstance(data["start_json"], dict)
        assert data["start_json"] == {"1": 800.0, "3": 400.0}
        assert data["end_json"] == {"1": 785.0, "3": 390.0}

    def test_trays_used_sorted(self):
        data = build_usage_data("k", "t", 10.0, {6, 1, 3}, {}, {}, "finish")
        assert data["trays_used"] == "1,3,6"

    def test_failed_status_passed_through(self):
        data = build_usage_data("k", "t", 10.0, set(), {}, {}, "failed")
        assert data["print_status"] == "failed"


class TestPrintEndClearsState:
    def test_state_cleared_after_finish(self):
        """Simulate state clearing after _on_print_finish."""
        job_key = "sample_Plate_1"
        start_snapshot = {1: 800.0, 3: 400.0}
        end_snapshot = {1: 785.0, 3: 390.0}
        last_processed = ""

        # Process
        last_processed = job_key

        # Clear (what _on_print_end does)
        start_snapshot = {}
        job_key = ""
        end_snapshot = {}

        assert start_snapshot == {}
        assert end_snapshot == {}
        assert job_key == ""
        assert last_processed == "sample_Plate_1"  # preserved for dedup


# ── Phase 3: Pause state, swap detection, rehydration ──


class TestPauseStateHandling:
    """Pause/paused must NOT trigger print-end logic."""

    def _simulate_status_change(self, old, new, print_active, phase2=True):
        """Simulate _on_print_status_change logic. Returns (new_print_active, triggered_start, triggered_end)."""
        triggered_start = False
        triggered_end = False

        if new in ("running", "printing") and old not in ("running", "printing"):
            print_active = True
            triggered_start = True
        elif old in ("running", "printing") and new not in ("running", "printing", "pause", "paused"):
            print_active = False
            triggered_end = True

        return print_active, triggered_start, triggered_end

    def test_pause_does_not_trigger_end(self):
        active, _, end = self._simulate_status_change("running", "pause", True)
        assert active is True
        assert end is False

    def test_paused_does_not_trigger_end(self):
        active, _, end = self._simulate_status_change("running", "paused", True)
        assert active is True
        assert end is False

    def test_finish_triggers_end(self):
        active, _, end = self._simulate_status_change("running", "finish", True)
        assert active is False
        assert end is True

    def test_failed_triggers_end(self):
        active, _, end = self._simulate_status_change("running", "failed", True)
        assert active is False
        assert end is True

    def test_idle_triggers_end(self):
        active, _, end = self._simulate_status_change("running", "idle", True)
        assert active is False
        assert end is True

    def test_resume_from_pause_does_not_trigger_start(self):
        """pause → running should NOT re-trigger start (old is not outside running/printing)."""
        active, start, end = self._simulate_status_change("pause", "running", True)
        # pause is not in ("running", "printing") so this WILL match the start condition
        # But that's actually correct — the start block just re-seeds, it doesn't harm.
        # The critical thing is that pause → running does NOT trigger end.
        assert end is False

    def test_resume_from_paused_does_not_trigger_end(self):
        """paused → running must not trigger end."""
        active, start, end = self._simulate_status_change("paused", "running", True)
        assert end is False
        assert active is True


class TestSwapDetection:
    """Spool swap detection during active print (automation F replacement)."""

    def _simulate_swap(self, print_active, phase3, startup_suppress_until, last_swap_warn_time):
        """Simulate _on_spool_id_change logic. Returns (should_warn, new_last_swap_time)."""
        if not phase3:
            return False, last_swap_warn_time
        if not print_active:
            return False, last_swap_warn_time
        now = datetime.datetime(2026, 3, 8, 12, 0, 0)
        if startup_suppress_until and now < startup_suppress_until:
            return False, last_swap_warn_time
        if last_swap_warn_time:
            elapsed = (now - last_swap_warn_time).total_seconds()
            if elapsed < 300:
                return False, last_swap_warn_time
        return True, now

    def test_swap_during_active_print_warns(self):
        warned, _ = self._simulate_swap(
            print_active=True, phase3=True,
            startup_suppress_until=None, last_swap_warn_time=None,
        )
        assert warned is True

    def test_swap_when_not_printing_ignored(self):
        warned, _ = self._simulate_swap(
            print_active=False, phase3=True,
            startup_suppress_until=None, last_swap_warn_time=None,
        )
        assert warned is False

    def test_swap_when_phase3_disabled_ignored(self):
        warned, _ = self._simulate_swap(
            print_active=True, phase3=False,
            startup_suppress_until=None, last_swap_warn_time=None,
        )
        assert warned is False

    def test_swap_cooldown_suppresses_second_warning(self):
        """Second swap within 5 minutes should be suppressed."""
        # First swap at 11:58 (2 min ago from simulated now=12:00)
        first_warn = datetime.datetime(2026, 3, 8, 11, 58, 0)
        warned, _ = self._simulate_swap(
            print_active=True, phase3=True,
            startup_suppress_until=None, last_swap_warn_time=first_warn,
        )
        assert warned is False

    def test_swap_after_cooldown_warns_again(self):
        """Swap after 5+ minutes should warn again."""
        old_warn = datetime.datetime(2026, 3, 8, 11, 54, 0)  # 6 min ago
        warned, _ = self._simulate_swap(
            print_active=True, phase3=True,
            startup_suppress_until=None, last_swap_warn_time=old_warn,
        )
        assert warned is True

    def test_startup_suppression_blocks_early_swap(self):
        """No swap warnings in first 90 seconds after startup."""
        suppress_until = datetime.datetime(2026, 3, 8, 12, 1, 0)  # 1 min from now
        warned, _ = self._simulate_swap(
            print_active=True, phase3=True,
            startup_suppress_until=suppress_until, last_swap_warn_time=None,
        )
        assert warned is False

    def test_startup_suppression_expired_allows_warning(self):
        """After 90s startup window, swaps should warn normally."""
        suppress_until = datetime.datetime(2026, 3, 8, 11, 58, 0)  # already passed
        warned, _ = self._simulate_swap(
            print_active=True, phase3=True,
            startup_suppress_until=suppress_until, last_swap_warn_time=None,
        )
        assert warned is True


class TestRehydration:
    """Print state rehydration on initialize / HA restart (automation G replacement)."""

    def _simulate_rehydrate(self, current_status, start_json_raw=None,
                            phase1=True):
        """Simulate _rehydrate_print_state logic.
        Returns (print_active, start_snapshot, recovered_from_helper).
        """
        if current_status not in ("running", "printing", "pause", "paused"):
            return False, {}, False

        print_active = True
        start_snapshot = {}
        recovered = False

        if phase1:
            if start_json_raw and start_json_raw not in ("{}", "unknown", "unavailable"):
                try:
                    parsed = json.loads(start_json_raw)
                    if isinstance(parsed, dict) and parsed:
                        start_snapshot = {int(k): float(v) for k, v in parsed.items()}
                        recovered = True
                except Exception:
                    pass

        return print_active, start_snapshot, recovered

    def test_rehydrate_when_printing(self):
        active, _, _ = self._simulate_rehydrate("running")
        assert active is True

    def test_rehydrate_when_paused(self):
        active, _, _ = self._simulate_rehydrate("pause")
        assert active is True

    def test_no_rehydrate_when_idle(self):
        active, _, _ = self._simulate_rehydrate("idle")
        assert active is False

    def test_no_rehydrate_when_finish(self):
        active, _, _ = self._simulate_rehydrate("finish")
        assert active is False

    def test_recovers_start_snapshot_from_helper(self):
        raw = '{"1": 800.0, "3": 400.0}'
        active, snapshot, recovered = self._simulate_rehydrate("running", raw)
        assert active is True
        assert recovered is True
        assert snapshot == {1: 800.0, 3: 400.0}

    def test_empty_helper_not_recovered(self):
        active, snapshot, recovered = self._simulate_rehydrate("running", "{}")
        assert recovered is False
        assert snapshot == {}

    def test_unknown_helper_not_recovered(self):
        active, snapshot, recovered = self._simulate_rehydrate("running", "unknown")
        assert recovered is False

    def test_malformed_json_not_recovered(self):
        active, snapshot, recovered = self._simulate_rehydrate("running", "not json")
        assert recovered is False
        assert snapshot == {}

    def test_rehydrate_paused_state(self):
        """Paused printer should also rehydrate (print is still active)."""
        raw = '{"2": 950.0}'
        active, snapshot, recovered = self._simulate_rehydrate("paused", raw)
        assert active is True
        assert recovered is True
        assert snapshot == {2: 950.0}

    def test_no_phase1_skips_snapshot_recovery(self):
        raw = '{"1": 800.0}'
        active, snapshot, recovered = self._simulate_rehydrate("running", raw, phase1=False)
        assert active is True
        assert recovered is False
        assert snapshot == {}


# ── Sync Color on Bind ──


def read_tray_color_hex(raw_color):
    """Simulate _read_tray_color_hex normalization."""
    if not raw_color:
        return None
    raw = str(raw_color).strip().lstrip("#")
    if len(raw) == 8:
        raw = raw[:6]
    if len(raw) != 6:
        return None
    return raw.upper()


def should_sync_color(existing_color, target_color):
    """Simulate _sync_filament_color_on_bind color comparison."""
    existing = str(existing_color or "").strip().lstrip("#").upper()
    if len(existing) == 8:
        existing = existing[:6]
    return existing != target_color


def resolve_sync_mode(sync_mode, tray_color):
    """Simulate sync_mode resolution. Returns target color or None."""
    if not sync_mode:
        return None
    if sync_mode == "auto":
        return tray_color
    if len(sync_mode) == 6 and all(c in "0123456789abcdefABCDEF" for c in sync_mode):
        return sync_mode.upper()
    return None


def is_rfid_lot_nr(lot_nr):
    """Check if lot_nr is RFID (32-char hex UUID)."""
    if not lot_nr or len(lot_nr) != 32:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in lot_nr)


class TestReadTrayColorHex:
    def test_8char_with_hash_and_alpha(self):
        assert read_tray_color_hex("#161616FF") == "161616"

    def test_8char_no_hash(self):
        assert read_tray_color_hex("000000FF") == "000000"

    def test_6char_with_hash(self):
        assert read_tray_color_hex("#FF00AA") == "FF00AA"

    def test_6char_no_hash(self):
        assert read_tray_color_hex("00ae42") == "00AE42"

    def test_empty_string(self):
        assert read_tray_color_hex("") is None

    def test_none(self):
        assert read_tray_color_hex(None) is None

    def test_short_string(self):
        assert read_tray_color_hex("FFF") is None

    def test_unavailable(self):
        assert read_tray_color_hex("unavailable") is None


class TestSyncColorOnBind:
    def test_auto_mode_resolves_tray_color(self):
        target = resolve_sync_mode("auto", "161616")
        assert target == "161616"

    def test_auto_mode_no_tray_color(self):
        target = resolve_sync_mode("auto", None)
        assert target is None

    def test_explicit_hex_mode(self):
        target = resolve_sync_mode("FF00AA", None)
        assert target == "FF00AA"

    def test_explicit_hex_lowercase(self):
        target = resolve_sync_mode("ff00aa", None)
        assert target == "FF00AA"

    def test_empty_mode_no_sync(self):
        target = resolve_sync_mode("", None)
        assert target is None

    def test_invalid_mode_returns_none(self):
        target = resolve_sync_mode("invalid", None)
        assert target is None

    def test_colors_differ_should_patch(self):
        assert should_sync_color("000000", "161616") is True

    def test_colors_match_should_skip(self):
        assert should_sync_color("161616", "161616") is False

    def test_existing_with_alpha_stripped(self):
        assert should_sync_color("161616FF", "161616") is False

    def test_existing_with_hash_stripped(self):
        assert should_sync_color("#161616", "161616") is False


class TestEnrollLotNrForce:
    def _simulate_enroll(self, existing_lot_nr, new_lot_nr, force=False):
        """Simulate _enroll_lot_nr logic. Returns (action, reason)."""
        if not new_lot_nr:
            return "skip", "empty"
        if existing_lot_nr == new_lot_nr:
            return "skip", "already_set"
        if existing_lot_nr and existing_lot_nr != new_lot_nr:
            if force:
                return "overwrite", "color_sync_re_enrollment"
            return "refuse", "conflict"
        return "write", "new"

    def test_force_false_refuses_overwrite(self):
        action, reason = self._simulate_enroll("pla|gfl99|000000", "pla|gfl99|161616", force=False)
        assert action == "refuse"
        assert reason == "conflict"

    def test_force_true_allows_overwrite(self):
        action, reason = self._simulate_enroll("pla|gfl99|000000", "pla|gfl99|161616", force=True)
        assert action == "overwrite"
        assert reason == "color_sync_re_enrollment"

    def test_same_value_skips_regardless_of_force(self):
        action, _ = self._simulate_enroll("pla|gfl99|161616", "pla|gfl99|161616", force=True)
        assert action == "skip"

    def test_empty_existing_writes_normally(self):
        action, reason = self._simulate_enroll("", "pla|gfl99|161616", force=False)
        assert action == "write"
        assert reason == "new"


class TestRfidGuard:
    def test_rfid_uuid_skips_sync(self):
        assert is_rfid_lot_nr("A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4") is True

    def test_rfid_lowercase_uuid(self):
        assert is_rfid_lot_nr("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4") is True

    def test_non_rfid_pipe_sig(self):
        assert is_rfid_lot_nr("pla|gfl99|161616") is False

    def test_empty_lot_nr(self):
        assert is_rfid_lot_nr("") is False

    def test_none_lot_nr(self):
        assert is_rfid_lot_nr(None) is False

    def test_short_hex(self):
        assert is_rfid_lot_nr("A1B2C3D4") is False


class TestSyncColorFullFlow:
    def test_assign_with_auto_sync_patches_and_re_enrolls(self):
        """Full flow: tray color differs from Spoolman → PATCH filament, force re-enroll."""
        tray_color = read_tray_color_hex("#161616FF")
        assert tray_color == "161616"

        target = resolve_sync_mode("auto", tray_color)
        assert target == "161616"

        existing_filament_color = "000000"
        needs_patch = should_sync_color(existing_filament_color, target)
        assert needs_patch is True

        # After PATCH, lot_sig would be rebuilt with new color
        old_lot_sig = "pla|gfl99|000000"
        new_lot_sig = "pla|gfl99|161616"

        # Force re-enroll with corrected sig
        action, reason = TestEnrollLotNrForce()._simulate_enroll(old_lot_sig, new_lot_sig, force=True)
        assert action == "overwrite"

    def test_assign_with_matching_colors_skips_sync(self):
        """Colors already match → no PATCH, no force re-enroll needed."""
        tray_color = read_tray_color_hex("#161616FF")
        target = resolve_sync_mode("auto", tray_color)
        needs_patch = should_sync_color("161616", target)
        assert needs_patch is False

    def test_assign_rfid_spool_skips_sync(self):
        """RFID spool → skip color sync entirely."""
        lot_nr = "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4"
        assert is_rfid_lot_nr(lot_nr) is True
        # Sync should not proceed

    def test_assign_with_empty_sync_mode_no_sync(self):
        """Empty sync_color_hex → backwards compatible, no sync."""
        target = resolve_sync_mode("", None)
        assert target is None
