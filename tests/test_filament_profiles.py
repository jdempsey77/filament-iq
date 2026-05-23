"""
test_filament_profiles.py — Tests for FilamentProfilesClient and FilamentProfile.

All tests are standalone (no AppDaemon dependency).
"""
import json
import os
import sys
import types
from unittest import mock

import pytest

# Bootstrap fake hassapi so label_printer can be imported for the fallback test
if "hassapi" not in sys.modules:
    _hassapi = types.ModuleType("hassapi")

    class _FakeHass:
        def __init__(self, ad=None, name=None, logger=None, args=None,
                     config=None, app_config=None, global_vars=None):
            self.args = args or {}

        def log(self, msg, level="INFO"):
            pass

    _hassapi.Hass = _FakeHass
    sys.modules["hassapi"] = _hassapi

_APPS = os.path.join(os.path.dirname(__file__), "..", "apps")
if _APPS not in sys.path:
    sys.path.insert(0, _APPS)

from filament_iq.filament_profiles import FilamentProfile, FilamentProfilesClient
from filament_iq.label_printer import LabelPrinter, LABEL_DIMENSIONS

LABEL_W, LABEL_H = LABEL_DIMENSIONS["29x90"]


# ── Helpers ───────────────────────────────────────────────────────────

def _write_json(tmp_path, data):
    p = tmp_path / "filaments.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


SUNLU_PLA_MATTE = {
    "brand_name": "SUNLU",
    "material_key": "pla",
    "material_type_key": "matte",
    "user_properties": {
        "nozzle_temperature_range_low": 220,
        "nozzle_temperature_range_high": 240,
        "bed_temperature": 60,
        "flow_ratio": 0.95,
        "max_volumetric_speed": 12.0,
    },
    "default_properties": {},
}


# ── Client init tests ─────────────────────────────────────────────────

def test_client_missing_file():
    """Missing file → available=False, no exception."""
    client = FilamentProfilesClient("/nonexistent/filaments.json")
    assert client.available is False


def test_client_empty_data(tmp_path):
    """Empty filaments list → available=True, lookup returns matched=False."""
    path = _write_json(tmp_path, {"filaments": []})
    client = FilamentProfilesClient(path)
    assert client.available is True
    result = client.lookup("SUNLU", "PLA", "PLA Matte Black")
    assert result.matched is False


def test_client_corrupt_data(tmp_path):
    """Malformed JSON → available=False, no exception."""
    p = tmp_path / "bad.json"
    p.write_text("{ this is not json }", encoding="utf-8")
    client = FilamentProfilesClient(str(p))
    assert client.available is False


# ── Lookup / confidence tests ─────────────────────────────────────────

def test_match_high_confidence(tmp_path):
    """SUNLU/PLA/'PLA Matte Black' against SUNLU PLA Matte record → confidence 'high'."""
    path = _write_json(tmp_path, {"filaments": [SUNLU_PLA_MATTE]})
    client = FilamentProfilesClient(path)
    assert client.available is True

    result = client.lookup("SUNLU", "PLA", "PLA Matte Black")
    assert result.matched is True
    assert result.confidence == "high"
    assert result.temp_min == 220
    assert result.temp_max == 240
    assert result.bed_temp_min == 60
    assert result.flow_ratio == pytest.approx(0.95)
    assert result.max_volumetric_speed == pytest.approx(12.0)
    assert result.source == "user"


def test_match_no_brand(tmp_path):
    """Unknown brand → matched=False."""
    path = _write_json(tmp_path, {"filaments": [SUNLU_PLA_MATTE]})
    client = FilamentProfilesClient(path)
    result = client.lookup("UnknownBrandXYZ", "PLA", "PLA Matte Black")
    assert result.matched is False


def test_match_medium_confidence(tmp_path):
    """Brand + material exact match, no type keyword in name → confidence 'medium'."""
    path = _write_json(tmp_path, {"filaments": [SUNLU_PLA_MATTE]})
    client = FilamentProfilesClient(path)
    result = client.lookup("SUNLU", "PLA", "Red")  # no type keyword
    assert result.matched is True
    assert result.confidence == "medium"


def test_fallback_to_default_properties(tmp_path):
    """No user_properties → falls back to default_properties, source='community'."""
    record = {
        "brand_name": "SUNLU",
        "material_key": "pla",
        "material_type_key": "matte",
        "user_properties": {},
        "default_properties": {
            "nozzle_temperature_range_low": 210,
            "nozzle_temperature_range_high": 230,
            "bed_temperature": 55,
        },
    }
    path = _write_json(tmp_path, {"filaments": [record]})
    client = FilamentProfilesClient(path)
    result = client.lookup("SUNLU", "PLA", "PLA Matte Black")
    assert result.matched is True
    assert result.source == "community"
    assert result.temp_min == 210


# ── generate_label_image fallback test ───────────────────────────────

class _TestableLabelPrinter(LabelPrinter):
    def __init__(self, args=None):
        a = {
            "spoolman_url": "http://fake:7912",
            "printer_url": "tcp://192.168.1.1:9100",
            "printer_model": "QL-810W",
            "label_size": "29x90",
            "dry_run": True,
        }
        a.update(args or {})
        super().__init__(None, "test_label", None, a, None, None, None)
        self.spoolman_url  = a["spoolman_url"]
        self.printer_url   = a["printer_url"]
        self.printer_model = a["printer_model"]
        self.label_size    = a["label_size"]
        self.dry_run       = True
        self._log_calls    = []

    def initialize(self):
        pass

    def log(self, msg, level="INFO"):
        self._log_calls.append((msg, level))

    def listen_event(self, *a, **kw):
        pass

    def fire_event(self, *a, **kw):
        pass

    def _validate_config(self, *a, **kw):
        pass


SAMPLE_SPOOL  = {"id": 58, "location": "New"}
SAMPLE_FILAMENT = {
    "id": 10,
    "name": "PLA Matte Black",
    "material": "PLA",
    "color_hex": "1a1a1a",
    "vendor": {"id": 1, "name": "SUNLU"},
}


def test_generate_label_falls_back():
    """If _generate_enhanced_label raises, generate_label_image returns standard label."""
    app = _TestableLabelPrinter()

    # Wire up a client that returns a high-confidence profile
    mock_client = mock.MagicMock()
    mock_client.available = True
    mock_client.lookup.return_value = FilamentProfile(
        matched=True, confidence="high",
        temp_min=220, temp_max=240,
        bed_temp_min=60, bed_temp_max=60,
        flow_ratio=0.95, max_volumetric_speed=12.0,
        source="user",
    )
    app.profiles_client = mock_client

    standard_result = app._generate_standard_label(SAMPLE_SPOOL, SAMPLE_FILAMENT)

    with mock.patch.object(app, "_generate_enhanced_label", side_effect=RuntimeError("render fail")):
        result = app.generate_label_image(SAMPLE_SPOOL, SAMPLE_FILAMENT)

    # Fell back → standard label dimensions
    assert result.size == standard_result.size == (LABEL_W, LABEL_H)
    assert result.mode == "RGB"
    # Warning was logged
    assert any("Enhanced label failed" in msg for msg, _ in app._log_calls)


# ── Color specificity tiebreak tests ─────────────────────────────────

def test_light_gray_scores_higher_than_gray():
    """Light Gray should score higher than Gray against 'PLA Basic Light Gray'."""
    gray_candidate = {
        "brand_name": "Bambu Lab",
        "material_key": "pla",
        "material_type_key": "basic",
        "color": "Gray (10103)",
        "user_properties": {},
        "default_properties": {},
    }
    light_gray_candidate = {
        "brand_name": "Bambu Lab",
        "material_key": "pla",
        "material_type_key": "basic",
        "color": "Light Gray (10104)",
        "user_properties": {},
        "default_properties": {},
    }

    score_gray = FilamentProfilesClient._score(
        "bambu lab", "pla", "pla basic light gray", gray_candidate
    )
    score_light_gray = FilamentProfilesClient._score(
        "bambu lab", "pla", "pla basic light gray", light_gray_candidate
    )

    assert score_light_gray > score_gray


def test_color_specificity_does_not_disrupt_non_tie():
    """Color specificity fix doesn't affect cases where the correct match wins by a large margin."""
    winner = {
        "brand_name": "SUNLU",
        "material_key": "pla",
        "material_type_key": "matte",
        "color": "Black (10001)",
        "user_properties": {},
        "default_properties": {},
    }
    loser = {
        "brand_name": "SUNLU",
        "material_key": "petg",
        "material_type_key": "basic",
        "color": "Black (10001)",
        "user_properties": {},
        "default_properties": {},
    }

    score_winner = FilamentProfilesClient._score(
        "sunlu", "pla", "pla matte black", winner
    )
    score_loser = FilamentProfilesClient._score(
        "sunlu", "pla", "pla matte black", loser
    )

    assert score_winner > score_loser
    assert score_winner - score_loser > 0.10
