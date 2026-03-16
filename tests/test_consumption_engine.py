"""
test_consumption_engine.py — Unit tests for the pure consumption decision engine.

Tests decide_consumption(inputs: List[SlotInput]) -> List[SlotDecision].
No AppDaemon, no Spoolman, no mocking required. These tests run in
plain Python with zero infrastructure.

Coverage:
  - All 12 core scenarios (parametrized table)
  - RFID edge cases: negative delta clamped, missing start_g, zero end_g
  - Non-RFID edge cases: missing spoolman_remaining, zero remaining
  - Sanity gates: below min, above max, exactly at boundaries
  - Multi-slot: mixed RFID+nonRFID, both depleted, independent decisions
  - Confidence: correct level assigned per method and threemf_method
  - Output order matches input order
"""

import os
import sys

_APPS = os.path.join(os.path.dirname(__file__), "..", "appdaemon", "apps")
if _APPS not in sys.path:
    sys.path.insert(0, _APPS)

import pytest

from filament_iq.consumption_engine import SlotInput, SlotDecision, decide_consumption


def _make_slot(**overrides) -> SlotInput:
    """Build a SlotInput with safe RFID defaults. Override only what the test varies."""
    defaults = dict(
        slot=1,
        spool_id=10,
        is_rfid=True,
        tray_empty=False,
        tray_active_seconds=300.0,
        start_g=900.0,
        end_g=800.0,
        threemf_used_g=None,
        threemf_method=None,
        spoolman_remaining=None,
    )
    defaults.update(overrides)
    return SlotInput(**defaults)


# ── 12-scenario core matrix ──────────────────────────────────────────


@pytest.mark.parametrize("inputs_dict,expected", [
    pytest.param(
        dict(is_rfid=True, tray_empty=False, start_g=900, end_g=800),
        dict(method="rfid_delta", consumption_g=100.0, confidence="high"),
        id="rfid_normal",
    ),
    pytest.param(
        dict(is_rfid=True, tray_empty=True, start_g=900, end_g=50),
        dict(method="rfid_delta_depleted", consumption_g=900.0, confidence="high"),
        id="rfid_depleted_tray_empty_overrides_end_g",
    ),
    pytest.param(
        dict(is_rfid=True, tray_empty=False, start_g=900, end_g=0.0),
        dict(method="rfid_delta_depleted", consumption_g=900.0, confidence="high"),
        id="rfid_depleted_zero_end_g",
    ),
    pytest.param(
        dict(is_rfid=True, tray_empty=False, start_g=900, end_g=None),
        dict(method="no_evidence"),
        id="rfid_no_end_g_not_empty_is_data_loss",
    ),
    pytest.param(
        dict(is_rfid=True, start_g=None, end_g=800),
        dict(method="no_evidence"),
        id="rfid_no_start_g_is_data_loss",
    ),
    pytest.param(
        dict(is_rfid=True, start_g=800, end_g=900),
        dict(method="no_evidence"),  # clamped to 0g, then BELOW_MIN gate
        id="rfid_negative_delta_clamped_to_zero",
    ),
    pytest.param(
        dict(is_rfid=False, tray_empty=False, start_g=None, end_g=None,
             threemf_used_g=96.5, threemf_method="exact_color_material"),
        dict(method="3mf", consumption_g=96.5, confidence="high"),
        id="nonrfid_3mf_normal",
    ),
    pytest.param(
        dict(is_rfid=False, tray_empty=True,
             threemf_used_g=450.0, threemf_method="exact_color_material",
             spoolman_remaining=420.0),
        dict(method="3mf_depleted", consumption_g=450.0, confidence="medium"),
        id="nonrfid_3mf_depleted_3mf_value_larger",
    ),
    pytest.param(
        dict(is_rfid=False, tray_empty=True,
             threemf_used_g=38.2, threemf_method="exact_color_material",
             spoolman_remaining=432.0),
        dict(method="3mf_depleted", consumption_g=432.0, confidence="medium"),
        id="nonrfid_3mf_depleted_spoolman_value_larger",
    ),
    pytest.param(
        dict(is_rfid=False, tray_empty=True,
             threemf_used_g=None, spoolman_remaining=432.0),
        dict(method="depleted_nonrfid", consumption_g=432.0, confidence="low"),
        id="nonrfid_depleted_no_3mf",
    ),
    pytest.param(
        dict(is_rfid=False, tray_empty=False, threemf_used_g=None),
        dict(method="no_evidence"),
        id="nonrfid_no_3mf_not_empty_is_no_evidence",
    ),
    pytest.param(
        dict(is_rfid=False, tray_empty=True,
             threemf_used_g=None, spoolman_remaining=None),
        dict(method="no_evidence"),
        id="nonrfid_depleted_no_spoolman_is_no_evidence",
    ),
])
def test_decision_engine_core_scenarios(inputs_dict, expected):
    inp = _make_slot(**inputs_dict)
    decisions = decide_consumption([inp])
    d = decisions[0]
    assert d.method == expected["method"]
    if "consumption_g" in expected:
        assert abs(d.consumption_g - expected["consumption_g"]) < 0.1
    if "confidence" in expected:
        assert d.confidence == expected["confidence"]
    if "skip_reason" in expected:
        assert expected["skip_reason"] in (d.skip_reason or "")
    # DATA_LOSS checks for specific scenarios
    if expected["method"] == "no_evidence" and "data_loss" in inputs_dict.get("id", ""):
        assert d.skip_reason is not None
    if inputs_dict.get("is_rfid") and inputs_dict.get("start_g") is None:
        assert "DATA_LOSS" in (d.skip_reason or "")
    if (inputs_dict.get("is_rfid") and inputs_dict.get("end_g") is None
            and not inputs_dict.get("tray_empty", False)
            and inputs_dict.get("start_g") is not None):
        assert "DATA_LOSS" in (d.skip_reason or "")
    if (not inputs_dict.get("is_rfid", True)
            and inputs_dict.get("tray_empty")
            and inputs_dict.get("spoolman_remaining") is None
            and inputs_dict.get("threemf_used_g") is None):
        assert "DEPLETED_BUT_NO_SPOOLMAN" in (d.skip_reason or "")


# ── Sanity gates ─────────────────────────────────────────────────────


class TestSanityGates:

    def test_above_max_becomes_no_evidence(self):
        inp = _make_slot(start_g=1500, end_g=400)  # delta=1100 > max 1000
        d = decide_consumption([inp])[0]
        assert d.method == "no_evidence"
        assert "SANITY_CAP" in d.skip_reason

    def test_below_min_becomes_no_evidence(self):
        inp = _make_slot(start_g=900, end_g=898.5)  # delta=1.5 < min 2.0
        d = decide_consumption([inp])[0]
        assert d.method == "no_evidence"
        assert "BELOW_MIN" in d.skip_reason

    def test_exactly_at_min_passes(self):
        inp = _make_slot(start_g=900, end_g=898.0)  # delta=2.0
        d = decide_consumption([inp])[0]
        assert d.method != "no_evidence"

    def test_exactly_at_max_passes(self):
        inp = _make_slot(start_g=1000, end_g=0.0)  # depleted, consumption=1000
        d = decide_consumption([inp], max_consumption_g=1000.0)[0]
        assert d.method != "no_evidence"

    # ── 3MF exempt from min_consumption_g ──

    def test_3mf_below_min_still_writes(self):
        """3MF match at 0.5g → write proceeds (exempt from floor)."""
        inp = _make_slot(is_rfid=False, tray_empty=False,
                         threemf_used_g=0.5, threemf_method="exact_color_material")
        d = decide_consumption([inp])[0]
        assert d.method == "3mf"
        assert abs(d.consumption_g - 0.5) < 0.01

    def test_3mf_depleted_below_min_still_writes(self):
        """3MF depleted at 1.0g → write proceeds (exempt from floor)."""
        inp = _make_slot(is_rfid=False, tray_empty=True,
                         threemf_used_g=1.0, threemf_method="exact_color_material",
                         spoolman_remaining=1.0)
        d = decide_consumption([inp])[0]
        assert d.method == "3mf_depleted"
        assert abs(d.consumption_g - 1.0) < 0.01

    def test_rfid_delta_below_min_suppressed(self):
        """RFID delta at 0.5g → suppressed by floor."""
        inp = _make_slot(is_rfid=True, start_g=900, end_g=899.5)
        d = decide_consumption([inp])[0]
        assert d.method == "no_evidence"
        assert "BELOW_MIN" in d.skip_reason

    def test_rfid_delta_depleted_below_min_suppressed(self):
        """RFID depleted at 0.5g start → suppressed by floor."""
        inp = _make_slot(is_rfid=True, tray_empty=True, start_g=0.5, end_g=0.0)
        d = decide_consumption([inp])[0]
        assert d.method == "no_evidence"
        assert "BELOW_MIN" in d.skip_reason

    def test_depleted_nonrfid_below_min_suppressed(self):
        """depleted_nonrfid at 0.5g → suppressed by floor."""
        inp = _make_slot(is_rfid=False, tray_empty=True,
                         threemf_used_g=None, spoolman_remaining=0.5)
        d = decide_consumption([inp])[0]
        assert d.method == "no_evidence"
        assert "BELOW_MIN" in d.skip_reason

    def test_3mf_at_zero_still_passes(self):
        """3MF match at 0.0g → passes min gate but filtered by used_g <= 0 in parser."""
        # Note: 0g 3MF entries are filtered by the parser (used_g <= 0 skipped).
        # If one reaches the engine, it should still not be suppressed by min_g.
        inp = _make_slot(is_rfid=False, tray_empty=False,
                         threemf_used_g=0.0, threemf_method="exact_color_material")
        d = decide_consumption([inp])[0]
        assert d.method == "3mf"
        assert d.consumption_g == 0.0


# ── Confidence levels ────────────────────────────────────────────────


class TestConfidenceLevels:

    def test_exact_color_material_is_high(self):
        inp = _make_slot(is_rfid=False, tray_empty=False,
                         threemf_used_g=50, threemf_method="exact_color_material")
        d = decide_consumption([inp])[0]
        assert d.confidence == "high"

    def test_close_color_material_is_medium(self):
        inp = _make_slot(is_rfid=False, tray_empty=False,
                         threemf_used_g=50, threemf_method="close_color_material")
        d = decide_consumption([inp])[0]
        assert d.confidence == "medium"

    def test_lot_nr_color_material_is_medium(self):
        inp = _make_slot(is_rfid=False, tray_empty=False,
                         threemf_used_g=50, threemf_method="lot_nr_color_material")
        d = decide_consumption([inp])[0]
        assert d.confidence == "medium"

    def test_material_only_single_is_medium(self):
        inp = _make_slot(is_rfid=False, tray_empty=False,
                         threemf_used_g=50, threemf_method="material_only_single")
        d = decide_consumption([inp])[0]
        assert d.confidence == "medium"

    def test_single_filament_force_is_high(self):
        inp = _make_slot(is_rfid=False, tray_empty=False,
                         threemf_used_g=50, threemf_method="single_filament_force")
        d = decide_consumption([inp])[0]
        assert d.confidence == "high"

    def test_depleted_nonrfid_always_low(self):
        inp = _make_slot(is_rfid=False, tray_empty=True,
                         threemf_used_g=None, spoolman_remaining=400)
        d = decide_consumption([inp])[0]
        assert d.confidence == "low"

    def test_no_evidence_confidence_is_none(self):
        inp = _make_slot(is_rfid=False, tray_empty=False, threemf_used_g=None)
        d = decide_consumption([inp])[0]
        assert d.confidence == "none"


# ── Multi-slot ───────────────────────────────────────────────────────


class TestMultiSlot:

    def test_rfid_and_nonrfid_decided_independently(self):
        inputs = [
            _make_slot(slot=1, spool_id=10, is_rfid=True,
                       start_g=900, end_g=800),
            _make_slot(slot=3, spool_id=30, is_rfid=False, tray_empty=False,
                       start_g=None, end_g=None,
                       threemf_used_g=96.5, threemf_method="exact_color_material"),
        ]
        decisions = decide_consumption(inputs)
        assert len(decisions) == 2
        assert decisions[0].method == "rfid_delta"
        assert decisions[1].method == "3mf"

    def test_both_depleted_mixed(self):
        inputs = [
            _make_slot(slot=1, spool_id=10, is_rfid=True,
                       tray_empty=True, start_g=900, end_g=50),
            _make_slot(slot=3, spool_id=30, is_rfid=False,
                       tray_empty=True, threemf_used_g=None,
                       spoolman_remaining=432),
        ]
        decisions = decide_consumption(inputs)
        assert len(decisions) == 2
        assert decisions[0].method == "rfid_delta_depleted"
        assert decisions[1].method == "depleted_nonrfid"

    def test_output_order_matches_input_order(self):
        inputs = [
            _make_slot(slot=3, spool_id=30, start_g=900, end_g=800),
            _make_slot(slot=1, spool_id=10, start_g=500, end_g=400),
            _make_slot(slot=5, spool_id=50, start_g=700, end_g=600),
        ]
        decisions = decide_consumption(inputs)
        assert [d.slot for d in decisions] == [3, 1, 5]

    def test_no_evidence_slot_included_in_output(self):
        inputs = [
            _make_slot(slot=1, spool_id=10, is_rfid=True,
                       start_g=900, end_g=800),
            _make_slot(slot=3, spool_id=30, is_rfid=False,
                       tray_empty=False, threemf_used_g=None),
        ]
        decisions = decide_consumption(inputs)
        assert len(decisions) == 2
        assert decisions[0].method == "rfid_delta"
        assert decisions[1].method == "no_evidence"
