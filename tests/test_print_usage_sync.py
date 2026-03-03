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
