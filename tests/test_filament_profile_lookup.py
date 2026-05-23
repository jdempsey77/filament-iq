"""
test_filament_profile_lookup.py — Tests for FilamentProfileLookup (Phase 2).
"""

import json
import os
import sys
import types
from unittest import mock

import pytest

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

from filament_iq.filament_profile_lookup import FilamentProfileLookup
from filament_iq.filament_profiles import FilamentProfile


# ── Testable harness ──────────────────────────────────────────────────────

class TestableFilamentProfileLookup(FilamentProfileLookup):
    def __init__(self, args=None):
        a = {
            "spoolman_url": "http://fake:7912",
            "filament_profiles_path": "/fake/filaments.json",
        }
        a.update(args or {})
        super().__init__(None, "test_lookup", None, a, None, None, None)
        self.spoolman_url = a["spoolman_url"]
        self.verifications_path = a.get(
            "verifications_path",
            "/nonexistent/profile_verifications.json",
        )
        self.profiles_client = None
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


def _lookup(app, request_id, filament_id):
    app._on_lookup_request(
        "filament_iq_profile_lookup_request",
        {"request_id": request_id, "filament_id": filament_id},
        {},
    )


def _verify(app, filament_id, action, **extra):
    app._on_verify(
        "filament_iq_profile_verify",
        {"filament_id": filament_id, "action": action, **extra},
        {},
    )


def _verified_entry(profile_id=128, profile_url=None, profile_name="Bambu Lab · PLA · Light Gray"):
    if profile_url is None:
        profile_url = f"https://3dfilamentprofiles.com/filament/details/{profile_id}"
    return {
        "status": "verified",
        "profile_id": profile_id,
        "profile_url": profile_url,
        "profile_name": profile_name,
        "verified_at": "2026-01-01T00:00:00Z",
        "scorer_version": "1.0",
    }


def _write_verifications(path, filaments_dict):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "filaments": filaments_dict}, fh)


# ── Lookup tests ──────────────────────────────────────────────────────────

def test_lookup_returns_verified_from_file(tmp_path):
    """Verified entry in file → response immediately, scorer NOT called."""
    vpath = str(tmp_path / "pv.json")
    _write_verifications(vpath, {"7": _verified_entry(profile_id=128)})

    app = TestableFilamentProfileLookup(args={"verifications_path": vpath})
    mock_client = mock.MagicMock()
    app.profiles_client = mock_client

    _lookup(app, "r1", 7)

    responses = [e for e in app._events_fired
                 if e["event"] == "filament_iq_profile_lookup_response"]
    assert len(responses) == 1
    r = responses[0]
    assert r["matched"] is True
    assert r["status"] == "verified"
    assert r["profile_id"] == 128
    assert r["request_id"] == "r1"
    assert r["filament_id"] == 7

    mock_client.lookup.assert_not_called()


def test_lookup_returns_no_profile_exists(tmp_path):
    """no_profile_exists entry → matched=False, scorer NOT called."""
    vpath = str(tmp_path / "pv.json")
    _write_verifications(vpath, {"7": {
        "status": "no_profile_exists",
        "profile_id": None,
        "profile_url": None,
        "profile_name": None,
        "verified_at": "2026-01-01T00:00:00Z",
        "scorer_version": "1.0",
    }})

    app = TestableFilamentProfileLookup(args={"verifications_path": vpath})
    mock_client = mock.MagicMock()
    app.profiles_client = mock_client

    _lookup(app, "r2", 7)

    responses = [e for e in app._events_fired
                 if e["event"] == "filament_iq_profile_lookup_response"]
    assert len(responses) == 1
    r = responses[0]
    assert r["matched"] is False
    assert r["status"] == "no_profile_exists"

    mock_client.lookup.assert_not_called()


def test_lookup_runs_scorer_when_unverified(tmp_path):
    """No entry for filament → scorer runs, response has status='candidate'."""
    vpath = str(tmp_path / "pv.json")
    _write_verifications(vpath, {})  # empty — no entry for filament 7

    app = TestableFilamentProfileLookup(args={"verifications_path": vpath})

    mock_profile = FilamentProfile(
        matched=True, confidence="high",
        temp_min=220, temp_max=240,
        bed_temp_min=60, bed_temp_max=60,
        flow_ratio=0.95, max_volumetric_speed=12.0,
        source="user",
        profile_id=128,
    )
    mock_client = mock.MagicMock()
    mock_client.lookup.return_value = mock_profile
    app.profiles_client = mock_client

    mock_filament = {
        "id": 7,
        "name": "PLA Basic Light Gray",
        "material": "PLA",
        "vendor": {"id": 1, "name": "Bambu Lab"},
    }
    app._fetch_filament = mock.Mock(return_value=mock_filament)

    _lookup(app, "r3", 7)

    mock_client.lookup.assert_called_once()

    responses = [e for e in app._events_fired
                 if e["event"] == "filament_iq_profile_lookup_response"]
    assert len(responses) == 1
    r = responses[0]
    assert r["matched"] is True
    assert r["status"] == "candidate"
    assert r["profile_id"] == 128


def test_lookup_missing_file_runs_scorer(tmp_path):
    """Missing verifications file → no exception, scorer called, response fired."""
    app = TestableFilamentProfileLookup(args={
        "verifications_path": str(tmp_path / "nonexistent.json"),
    })

    mock_profile = FilamentProfile(
        matched=True, confidence="medium",
        temp_min=210, temp_max=230,
        bed_temp_min=55, bed_temp_max=55,
        flow_ratio=None, max_volumetric_speed=None,
        source="community",
        profile_id=42,
    )
    mock_client = mock.MagicMock()
    mock_client.lookup.return_value = mock_profile
    app.profiles_client = mock_client

    app._fetch_filament = mock.Mock(return_value={
        "id": 7,
        "name": "PETG Black",
        "material": "PETG",
        "vendor": {"id": 2, "name": "SUNLU"},
    })

    _lookup(app, "r4", 7)

    mock_client.lookup.assert_called_once()
    responses = [e for e in app._events_fired
                 if e["event"] == "filament_iq_profile_lookup_response"]
    assert len(responses) == 1
    assert responses[0]["matched"] is True


# ── Verify tests ──────────────────────────────────────────────────────────

def test_verify_confirm_writes_file(tmp_path):
    """confirm action writes status=verified to file, fires result with success=True."""
    vpath = str(tmp_path / "pv.json")
    _write_verifications(vpath, {})

    app = TestableFilamentProfileLookup(args={"verifications_path": vpath})

    _verify(
        app, 7, "confirm",
        profile_id=128,
        profile_url="https://3dfilamentprofiles.com/filament/details/128",
        profile_name="Bambu Lab · PLA Basic · Light Gray",
    )

    with open(vpath) as fh:
        data = json.load(fh)

    assert "7" in data["filaments"]
    entry = data["filaments"]["7"]
    assert entry["status"] == "verified"
    assert entry["profile_id"] == 128
    assert entry["profile_url"] == "https://3dfilamentprofiles.com/filament/details/128"
    assert entry["profile_name"] == "Bambu Lab · PLA Basic · Light Gray"
    assert "verified_at" in entry

    results = [e for e in app._events_fired
               if e["event"] == "filament_iq_profile_verify_result"]
    assert len(results) == 1
    assert results[0]["success"] is True
    assert results[0]["filament_id"] == 7


def test_verify_no_match_writes_file(tmp_path):
    """no_match action writes status=no_profile_exists with null profile fields."""
    vpath = str(tmp_path / "pv.json")
    _write_verifications(vpath, {})

    app = TestableFilamentProfileLookup(args={"verifications_path": vpath})

    _verify(app, 7, "no_match")

    with open(vpath) as fh:
        data = json.load(fh)

    assert "7" in data["filaments"]
    entry = data["filaments"]["7"]
    assert entry["status"] == "no_profile_exists"
    assert entry["profile_id"] is None
    assert entry["profile_url"] is None

    results = [e for e in app._events_fired
               if e["event"] == "filament_iq_profile_verify_result"]
    assert results[0]["success"] is True


def test_verify_reject_removes_entry(tmp_path):
    """reject action removes the filament entry from the file."""
    vpath = str(tmp_path / "pv.json")
    _write_verifications(vpath, {"7": _verified_entry()})

    app = TestableFilamentProfileLookup(args={"verifications_path": vpath})

    _verify(app, 7, "reject")

    with open(vpath) as fh:
        data = json.load(fh)

    assert "7" not in data["filaments"]

    results = [e for e in app._events_fired
               if e["event"] == "filament_iq_profile_verify_result"]
    assert len(results) == 1
    assert results[0]["success"] is True


def test_atomic_write_uses_tmp_then_replace(tmp_path):
    """_write_verifications must call os.replace(tmp, final) atomically."""
    vpath = str(tmp_path / "pv.json")
    app = TestableFilamentProfileLookup(args={"verifications_path": vpath})

    with mock.patch("filament_iq.filament_profile_lookup.os.replace") as mock_replace:
        app._write_verifications({"version": 1, "filaments": {}})
        mock_replace.assert_called_once_with(vpath + ".tmp", vpath)
