"""Tests for AmsPrintUsageSync consumption logic.

Run: python3 -m pytest tests/test_print_usage_sync.py -v
"""
import pytest


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
        # TODO: spool_id values are example (1-6); substitute with your Spoolman spool IDs.
        candidates = [(2, 2), (3, 3), (4, 4), (5, 5), (6, 6)]
        result = filter_nonrfid_slots(candidates, {6})
        assert result == [(6, 6)]

    def test_multiple_used_slots(self):
        """Slots 2 and 6 used."""
        # TODO: spool_id values are example (1-6); substitute with your Spoolman spool IDs.
        candidates = [(2, 2), (3, 3), (4, 4), (5, 5), (6, 6)]
        result = filter_nonrfid_slots(candidates, {2, 6})
        assert result == [(2, 2), (6, 6)]

    def test_empty_trays_used_skips_all(self):
        """No trays_used data — fail-closed: skip all non-RFID slots."""
        candidates = [(2, 2), (3, 3), (6, 6)]
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
        all_nonrfid = [(2, 2), (3, 3), (4, 4), (5, 5), (6, 6)]
        trays_used = parse_trays_used("6")
        filtered = filter_nonrfid_slots(all_nonrfid, trays_used)
        assert filtered == [(6, 6)]

        pool_g = 58.5
        each_g = pool_g / len(filtered)
        assert each_g == 58.5  # all goes to slot 6

    def test_two_spool_print_splits_between_used(self):
        """Multi-material print using slots 2 and 6."""
        all_nonrfid = [(2, 2), (3, 3), (4, 4), (5, 5), (6, 6)]
        trays_used = parse_trays_used("2,6")
        filtered = filter_nonrfid_slots(all_nonrfid, trays_used)
        assert filtered == [(2, 2), (6, 6)]

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
        nonrfid_slots = [(2, 2), (4, 4)]

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
        rfid_results = [(1, 1, 40.0), (3, 3, 35.0)]  # 75g total
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
