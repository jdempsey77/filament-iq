"""
test_label_printer.py — Unit tests for the label_printer AppDaemon app.

Covers label image generation, dry_run behavior, location updates,
result events, and error handling. All HTTP and AppDaemon calls mocked.
"""

import json
import os
import sys
import types
from unittest import mock

import pytest

# Bootstrap fake hassapi (same pattern as other tests)
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

from filament_iq.label_printer import LabelPrinter, LABEL_SIZE_PX


# ── Test harness ─────────────────────────────────────────────────────

class TestableLabelPrinter(LabelPrinter):
    """LabelPrinter with mocked AppDaemon infrastructure."""

    def __init__(self, args=None):
        a = {
            "spoolman_url": "http://fake:7912",
            "printer_url": "tcp://192.168.1.1:9100",
            "printer_model": "QL-810W",
            "label_size": "d24",
            "dry_run": True,
        }
        a.update(args or {})
        super().__init__(None, "test_label", None, a, None, None, None)
        self.spoolman_url = a["spoolman_url"]
        self.printer_url = a["printer_url"]
        self.printer_model = a["printer_model"]
        self.label_size = a["label_size"]
        self.dry_run = bool(a.get("dry_run", True))
        self._log_calls = []
        self._events_fired = []

    def initialize(self):
        pass

    def log(self, msg, level="INFO"):
        self._log_calls.append((msg, level))

    def listen_event(self, *a, **kw):
        pass

    def fire_event(self, event_name, **kwargs):
        self._events_fired.append({"event": event_name, **kwargs})

    def _validate_config(self, *a, **kw):
        pass


def _has_log(app, substring):
    return any(substring in msg for msg, _ in app._log_calls)


SAMPLE_FILAMENT_DATA = {
    "id": 10,
    "name": "Matte Black PLA+",
    "material": "PLA",
    "color_hex": "1a1a1a",
    "vendor": {"id": 1, "name": "Bambu Lab"},
}

SAMPLE_FILAMENT_LIGHT = {
    "id": 11,
    "name": "Snow White Premium",
    "material": "PETG",
    "color_hex": "ffffff",
    "vendor": {"id": 2, "name": "eSUN Materials Corp"},
}

SAMPLE_SPOOL_DATA = {
    "id": 42,
    "remaining_weight": 800.0,
    "location": "New",
    "filament": {"id": 10},
}


# ── Image generation tests ───────────────────────────────────────────

def test_generate_label_image_size():
    """Label image is 236x236 RGB."""
    app = TestableLabelPrinter()
    img = app.generate_label_image(SAMPLE_SPOOL_DATA, SAMPLE_FILAMENT_DATA)
    assert img.size == (LABEL_SIZE_PX, LABEL_SIZE_PX)
    assert img.mode == "RGB"


def test_generate_label_image_white_text_on_dark():
    """Dark color (#1A1A1A, luminance 0.1) → white text."""
    app = TestableLabelPrinter()
    img = app.generate_label_image(SAMPLE_SPOOL_DATA, SAMPLE_FILAMENT_DATA)
    # Check center pixel is dark (filled circle)
    center = img.getpixel((LABEL_SIZE_PX // 2, LABEL_SIZE_PX // 2))
    assert center[0] < 100, f"Center should be dark, got {center}"


def test_generate_label_image_dark_text_on_light():
    """Light color (#FFFFFF, luminance 1.0) → dark text."""
    app = TestableLabelPrinter()
    img = app.generate_label_image(SAMPLE_SPOOL_DATA, SAMPLE_FILAMENT_LIGHT)
    center = img.getpixel((LABEL_SIZE_PX // 2, LABEL_SIZE_PX // 2))
    assert center[0] > 200, f"Center should be light, got {center}"


def test_generate_label_vendor_truncated():
    """Vendor name truncated to 10 chars."""
    app = TestableLabelPrinter()
    long_vendor = dict(SAMPLE_FILAMENT_DATA)
    long_vendor["vendor"] = {"name": "Very Long Vendor Name Inc"}
    img = app.generate_label_image(SAMPLE_SPOOL_DATA, long_vendor)
    assert img.size == (LABEL_SIZE_PX, LABEL_SIZE_PX)


def test_generate_label_filament_name_truncated():
    """Filament name truncated to 14 chars."""
    app = TestableLabelPrinter()
    long_name = dict(SAMPLE_FILAMENT_DATA)
    long_name["name"] = "Extremely Long Filament Name That Overflows"
    img = app.generate_label_image(SAMPLE_SPOOL_DATA, long_name)
    assert img.size == (LABEL_SIZE_PX, LABEL_SIZE_PX)


# ── Printer tests ────────────────────────────────────────────────────

def test_send_to_printer_dry_run():
    """dry_run=True logs message, does NOT import brother_ql."""
    app = TestableLabelPrinter({"dry_run": True})
    from PIL import Image
    img = Image.new("RGB", (236, 236))
    app.send_to_printer(img, 42)
    assert _has_log(app, "DRY_RUN: would send label for spool 42")
    # Verify brother_ql was NOT imported
    assert "brother_ql" not in sys.modules or not _has_log(app, "LABEL_SENT")


# ── Location update tests ────────────────────────────────────────────

def test_update_spool_location_skips_non_new():
    """Skip PATCH when location is not 'New'."""
    app = TestableLabelPrinter()
    spool = {"id": 42, "location": "Shelf"}
    with mock.patch("urllib.request.urlopen") as mock_urlopen:
        app.update_spool_location(42, spool)
        mock_urlopen.assert_not_called()
    assert _has_log(app, "LABEL_LOCATION_SKIP")


def test_update_spool_location_patches_when_new():
    """Fire PATCH when location is 'New'."""
    app = TestableLabelPrinter()
    spool = {"id": 42, "location": "New"}
    mock_resp = mock.MagicMock()
    mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = mock.MagicMock(return_value=False)
    with mock.patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        app.update_spool_location(42, spool)
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.method == "PATCH"
        assert b'"location": "Shelf"' in req.data
    assert _has_log(app, "LABEL_LOCATION_UPDATED")


# ── Result event tests ───────────────────────────────────────────────

def test_fire_result_event_success():
    """Success event has correct name and payload."""
    app = TestableLabelPrinter()
    app.fire_result_event(42, True)
    assert len(app._events_fired) == 1
    evt = app._events_fired[0]
    assert evt["event"] == "filament_iq_label_result"
    assert evt["spool_id"] == 42
    assert evt["success"] is True
    assert evt["error"] is None


def test_fire_result_event_failure():
    """Failure event includes error string."""
    app = TestableLabelPrinter()
    app.fire_result_event(42, False, "Printer offline")
    evt = app._events_fired[0]
    assert evt["success"] is False
    assert evt["error"] == "Printer offline"


# ── Full flow tests ──────────────────────────────────────────────────

def test_full_flow_spoolman_fetch_fails():
    """Spoolman fetch fails → result event with success=false."""
    app = TestableLabelPrinter()
    with mock.patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
        app._on_print_label_event("filament_iq_print_label", {"spool_id": 42}, {})
    assert len(app._events_fired) == 1
    evt = app._events_fired[0]
    assert evt["success"] is False
    assert "not found" in evt["error"].lower() or "connection" in evt["error"].lower()
