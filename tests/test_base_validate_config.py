#!/usr/bin/env python3
"""
Tests for FilamentIQBase._validate_config() and _check_spoolman_connectivity().
Run: python -m pytest tests/test_base_validate_config.py -v
"""

import os
import sys
import types
from unittest import mock

import pytest

# Bootstrap fake hassapi before importing module (no appdaemon dep)
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

_APPS = os.path.join(os.path.dirname(__file__), "..", "appdaemon", "apps")
if _APPS not in sys.path:
    sys.path.insert(0, _APPS)

from filament_iq.base import FilamentIQBase


# ── test harness ──────────────────────────────────────────────────────

class _TestableBase(FilamentIQBase):
    """FilamentIQBase with captured log output."""

    def __init__(self, args=None):
        super().__init__(None, "test_base", None, args or {}, None, None, None)
        self._log_calls = []

    def log(self, msg, level="INFO"):
        self._log_calls.append((level, msg))


# ── tests ─────────────────────────────────────────────────────────────

class TestValidateConfig:

    def test_valid_config_passes(self):
        """All keys correct → no exception, CONFIG_VALID logged."""
        app = _TestableBase(args={
            "spoolman_url": "http://192.0.2.1:7912",
            "max_consumption_g": 1000.0,
            "dry_run": False,
        })
        app._validate_config(
            required_keys=["spoolman_url"],
            typed_keys={"max_consumption_g": (float, 1000.0), "dry_run": (bool, False)},
            range_keys={"max_consumption_g": (1.0, None)},
        )
        assert any("CONFIG_VALID" in msg for _, msg in app._log_calls)

    def test_missing_required_key(self):
        """Required key absent → ValueError with key name."""
        app = _TestableBase(args={})
        with pytest.raises(ValueError, match="spoolman_url"):
            app._validate_config(required_keys=["spoolman_url"])

    def test_wrong_type_float(self):
        """max_consumption_g: 'banana' → ValueError mentioning key."""
        app = _TestableBase(args={
            "spoolman_url": "http://192.0.2.1:7912",
            "max_consumption_g": "banana",
        })
        with pytest.raises(ValueError, match="max_consumption_g"):
            app._validate_config(
                required_keys=["spoolman_url"],
                typed_keys={"max_consumption_g": (float, 1000.0)},
            )

    def test_wrong_type_int(self):
        """scan_interval_seconds: 'fast' → ValueError mentioning key."""
        app = _TestableBase(args={
            "spoolman_url": "http://192.0.2.1:7912",
            "scan_interval_seconds": "fast",
        })
        with pytest.raises(ValueError, match="scan_interval_seconds"):
            app._validate_config(
                required_keys=["spoolman_url"],
                typed_keys={"scan_interval_seconds": (int, 300)},
            )

    def test_wrong_type_bool(self):
        """dry_run: 'yes' (string) → ValueError mentioning key."""
        app = _TestableBase(args={
            "spoolman_url": "http://192.0.2.1:7912",
            "dry_run": "yes",
        })
        with pytest.raises(ValueError, match="dry_run"):
            app._validate_config(
                required_keys=["spoolman_url"],
                typed_keys={"dry_run": (bool, False)},
            )

    def test_out_of_range_below_min(self):
        """max_consumption_g: -5.0 → ValueError mentioning >= 1.0."""
        app = _TestableBase(args={
            "spoolman_url": "http://192.0.2.1:7912",
            "max_consumption_g": -5.0,
        })
        with pytest.raises(ValueError, match="max_consumption_g"):
            app._validate_config(
                required_keys=["spoolman_url"],
                typed_keys={"max_consumption_g": (float, 1000.0)},
                range_keys={"max_consumption_g": (1.0, None)},
            )

    def test_zero_interval_rejected(self):
        """scan_interval_seconds: 0 → ValueError (must be >= 1)."""
        app = _TestableBase(args={
            "spoolman_url": "http://192.0.2.1:7912",
            "scan_interval_seconds": 0,
        })
        with pytest.raises(ValueError, match="scan_interval_seconds"):
            app._validate_config(
                required_keys=["spoolman_url"],
                typed_keys={"scan_interval_seconds": (int, 300)},
                range_keys={"scan_interval_seconds": (1, None)},
            )

    def test_optional_key_absent_no_error(self):
        """Typed key not in config → no error, validation passes."""
        app = _TestableBase(args={
            "spoolman_url": "http://192.0.2.1:7912",
        })
        app._validate_config(
            required_keys=["spoolman_url"],
            typed_keys={"max_consumption_g": (float, 1000.0)},
            range_keys={"max_consumption_g": (1.0, None)},
        )
        assert any("CONFIG_VALID" in msg for _, msg in app._log_calls)


class TestSpoolmanConnectivity:

    def test_spoolman_reachable(self):
        """Mock HTTP 200 → SPOOLMAN_REACHABLE logged."""
        app = _TestableBase(args={
            "spoolman_url": "http://192.0.2.1:7912",
        })
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = mock.MagicMock()
            app._check_spoolman_connectivity()
        assert any("SPOOLMAN_REACHABLE" in msg for _, msg in app._log_calls)
        assert not any("WARNING" == lvl for lvl, _ in app._log_calls)

    def test_spoolman_unreachable(self):
        """Mock connection error → SPOOLMAN_UNREACHABLE WARNING, no exception."""
        app = _TestableBase(args={
            "spoolman_url": "http://192.0.2.1:7912",
        })
        with mock.patch("urllib.request.urlopen", side_effect=ConnectionError("refused")):
            app._check_spoolman_connectivity()  # should NOT raise
        assert any(
            "SPOOLMAN_UNREACHABLE" in msg and lvl == "WARNING"
            for lvl, msg in app._log_calls
        )

    def test_spoolman_empty_url_returns_early(self):
        """Empty spoolman_url → no HTTP call, no log."""
        app = _TestableBase(args={})
        app._check_spoolman_connectivity()
        assert not any("SPOOLMAN" in msg for _, msg in app._log_calls)


class TestRangeValidation:

    def test_above_max_rejected(self):
        """Value above max_val → ValueError."""
        app = _TestableBase(args={
            "spoolman_url": "http://192.0.2.1:7912",
            "printer_ftps_port": 99999,
        })
        with pytest.raises(ValueError, match="printer_ftps_port"):
            app._validate_config(
                required_keys=["spoolman_url"],
                typed_keys={"printer_ftps_port": (int, 990)},
                range_keys={"printer_ftps_port": (1, 65535)},
            )

    def test_range_with_non_numeric_skipped(self):
        """Non-numeric value for range check → type error caught, range skipped."""
        app = _TestableBase(args={
            "spoolman_url": "http://192.0.2.1:7912",
            "max_consumption_g": "banana",
        })
        with pytest.raises(ValueError, match="max_consumption_g"):
            app._validate_config(
                required_keys=["spoolman_url"],
                typed_keys={"max_consumption_g": (float, 1000.0)},
                range_keys={"max_consumption_g": (1.0, None)},
            )


class TestEntityPrefix:

    def test_empty_serial_returns_model_only(self):
        """Missing printer_serial → prefix is just model."""
        app = _TestableBase(args={"printer_model": "p1s"})
        assert app._build_entity_prefix() == "p1s"

    def test_build_slot_mappings_instance_method(self):
        """_build_slot_mappings delegates to module-level function."""
        app = _TestableBase(args={
            "printer_model": "p1s",
            "printer_serial": "01p00c5a3101668",
        })
        tray_by_slot, slot_by_tray, ams_tray, canonical = app._build_slot_mappings()
        assert 1 in tray_by_slot
        assert 6 in tray_by_slot

    def test_get_all_slots(self):
        """_get_all_slots returns sorted slot list."""
        app = _TestableBase(args={
            "printer_model": "p1s",
            "printer_serial": "01p00c5a3101668",
        })
        slots = app._get_all_slots()
        assert slots == [1, 2, 3, 4, 5, 6, 7]
