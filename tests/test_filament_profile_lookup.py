"""
test_filament_profile_lookup.py — Tests for FilamentProfileLookup.
Spoolman is the source of truth for verification status.
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


def _spoolman_filament(filament_id, profile_url=None, profile_name=None):
    """Build a fake Spoolman filament dict. profile_url is stored JSON-encoded."""
    extra = {}
    if profile_url is not None:
        extra["profile_url"] = json.dumps(profile_url, ensure_ascii=False)
    if profile_name is not None:
        extra["profile_name"] = json.dumps(profile_name, ensure_ascii=False)
    return {
        "id": filament_id,
        "name": "PLA Basic Light Gray",
        "material": "PLA",
        "vendor": {"id": 1, "name": "Bambu Lab"},
        "extra": extra,
    }


def _mock_get(filaments):
    """Return a mock requests.get response yielding a filament list."""
    resp = mock.MagicMock()
    resp.json.return_value = filaments
    resp.raise_for_status = mock.MagicMock()
    return resp


# ── Bulk status tests ─────────────────────────────────────────────────────

def test_bulk_status_derives_verified_from_spoolman():
    """Filaments with non-empty profile_url in extra → verified; others → unverified."""
    app = TestableFilamentProfileLookup()

    filaments = [
        _spoolman_filament(1, profile_url="https://3dfilamentprofiles.com/filament/details/128"),
        _spoolman_filament(2),                   # no profile_url → unverified
        _spoolman_filament(3, profile_url=""),    # empty → unverified
        {"id": 4, "extra": None},                # null extra → unverified
    ]

    with mock.patch(
        "filament_iq.filament_profile_lookup.requests.get",
        return_value=_mock_get(filaments),
    ):
        app._on_bulk_status_request(
            "filament_iq_profile_bulk_status_request",
            {"request_id": "r1"},
            {},
        )

    events = [e for e in app._events_fired
              if e["event"] == "filament_iq_profile_bulk_status_response"]
    assert len(events) == 1
    statuses = events[0]["statuses"]
    assert statuses["1"] == "verified"
    assert statuses["2"] == "unverified"
    assert statuses["3"] == "unverified"
    assert statuses["4"] == "unverified"
    assert events[0]["request_id"] == "r1"


def test_bulk_status_strips_json_encoded_quotes():
    """extra.profile_url stored as JSON string ('"url"') is correctly stripped."""
    app = TestableFilamentProfileLookup()

    # Spoolman stores values JSON-encoded; profile_url looks like '"https://..."'
    filaments = [{"id": 5, "extra": {"profile_url": '"https://example.com/128"'}}]

    with mock.patch(
        "filament_iq.filament_profile_lookup.requests.get",
        return_value=_mock_get(filaments),
    ):
        app._on_bulk_status_request(
            "filament_iq_profile_bulk_status_request", {}, {}
        )

    events = [e for e in app._events_fired
              if e["event"] == "filament_iq_profile_bulk_status_response"]
    assert events[0]["statuses"]["5"] == "verified"


def test_bulk_status_spoolman_failure_skips_event_no_prior():
    """Spoolman network error with no prior statuses → WARNING logged, no event fired."""
    app = TestableFilamentProfileLookup()
    app._last_bulk_statuses = {}

    with mock.patch(
        "filament_iq.filament_profile_lookup.requests.get",
        side_effect=Exception("connection refused"),
    ):
        app._on_bulk_status_request(
            "filament_iq_profile_bulk_status_request",
            {"request_id": "r2"},
            {},
        )

    events = [e for e in app._events_fired
              if e["event"] == "filament_iq_profile_bulk_status_response"]
    assert len(events) == 0  # no prior statuses → skip firing

    warnings = [msg for msg, lvl in app._log_calls if lvl == "WARNING"]
    assert any("BULK_STATUS_ERROR" in m for m in warnings)


# ── Lookup tests ──────────────────────────────────────────────────────────

def test_lookup_returns_verified_from_spoolman():
    """Filament with profile_url in Spoolman extra → verified response, scorer NOT called."""
    app = TestableFilamentProfileLookup()
    mock_client = mock.MagicMock()
    app.profiles_client = mock_client

    filament = _spoolman_filament(
        7,
        profile_url="https://3dfilamentprofiles.com/filament/details/128",
        profile_name="Bambu Lab · PLA · Light Gray",
    )
    app._fetch_filament = mock.Mock(return_value=filament)

    _lookup(app, "r1", 7)

    responses = [e for e in app._events_fired
                 if e["event"] == "filament_iq_profile_lookup_response"]
    assert len(responses) == 1
    r = responses[0]
    assert r["matched"] is True
    assert r["status"] == "verified"
    assert r["profile_url"] == "https://3dfilamentprofiles.com/filament/details/128"
    assert r["profile_name"] == "Bambu Lab · PLA · Light Gray"
    assert r["request_id"] == "r1"
    assert r["filament_id"] == 7

    mock_client.lookup.assert_not_called()


def test_lookup_runs_scorer_when_spoolman_has_no_url():
    """Filament with no profile_url in Spoolman extra → scorer runs, candidate response."""
    app = TestableFilamentProfileLookup()

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

    filament = _spoolman_filament(7)  # no profile_url
    app._fetch_filament = mock.Mock(return_value=filament)

    _lookup(app, "r2", 7)

    mock_client.lookup.assert_called_once()

    responses = [e for e in app._events_fired
                 if e["event"] == "filament_iq_profile_lookup_response"]
    assert len(responses) == 1
    r = responses[0]
    assert r["matched"] is True
    assert r["status"] == "candidate"
    assert r["profile_id"] == 128


def test_lookup_fetch_failure_returns_unverified():
    """Spoolman fetch failure → unverified response, no scorer call."""
    app = TestableFilamentProfileLookup()
    mock_client = mock.MagicMock()
    app.profiles_client = mock_client
    app._fetch_filament = mock.Mock(return_value=None)

    _lookup(app, "r3", 7)

    responses = [e for e in app._events_fired
                 if e["event"] == "filament_iq_profile_lookup_response"]
    assert len(responses) == 1
    assert responses[0]["matched"] is False
    assert responses[0]["status"] == "unverified"
    mock_client.lookup.assert_not_called()


def test_lookup_scorer_no_match_returns_unverified():
    """Scorer returns no match → unverified response."""
    app = TestableFilamentProfileLookup()

    mock_profile = FilamentProfile(
        matched=False, confidence="none",
        temp_min=None, temp_max=None,
        bed_temp_min=None, bed_temp_max=None,
        flow_ratio=None, max_volumetric_speed=None,
        source=None,
        profile_id=None,
    )
    mock_client = mock.MagicMock()
    mock_client.lookup.return_value = mock_profile
    app.profiles_client = mock_client

    app._fetch_filament = mock.Mock(return_value=_spoolman_filament(7))

    _lookup(app, "r4", 7)

    responses = [e for e in app._events_fired
                 if e["event"] == "filament_iq_profile_lookup_response"]
    assert responses[0]["matched"] is False
    assert responses[0]["status"] == "unverified"


# ── Verify tests ──────────────────────────────────────────────────────────

def test_verify_confirm_patches_spoolman_and_fires_success():
    """confirm → patches profile_url + profile_name to Spoolman, fires success."""
    app = TestableFilamentProfileLookup()

    fake_get = mock.MagicMock()
    fake_get.json.return_value = {"id": 7, "extra": {}}
    fake_get.raise_for_status = mock.MagicMock()
    fake_patch = mock.MagicMock()
    fake_patch.raise_for_status = mock.MagicMock()

    with mock.patch("filament_iq.filament_profile_lookup.requests.get",
                    return_value=fake_get), \
         mock.patch("filament_iq.filament_profile_lookup.requests.patch",
                    return_value=fake_patch) as mock_patch:
        _verify(
            app, 7, "confirm",
            profile_id=128,
            profile_url="https://3dfilamentprofiles.com/filament/details/128",
            profile_name="Bambu Lab · PLA Basic · Light Gray",
        )

    patched_extra = mock_patch.call_args[1]["json"]["extra"]
    assert patched_extra["profile_url"] == '"https://3dfilamentprofiles.com/filament/details/128"'
    assert patched_extra["profile_name"] == '"Bambu Lab · PLA Basic · Light Gray"'

    results = [e for e in app._events_fired
               if e["event"] == "filament_iq_profile_verify_result"]
    assert len(results) == 1
    assert results[0]["success"] is True
    assert results[0]["filament_id"] == 7


def test_verify_reject_clears_spoolman_and_fires_success():
    """reject → patches profile_url=null + profile_name=null, fires success."""
    app = TestableFilamentProfileLookup()

    fake_get = mock.MagicMock()
    fake_get.json.return_value = {
        "id": 7,
        "extra": {"profile_url": '"https://example.com"', "other": "x"},
    }
    fake_get.raise_for_status = mock.MagicMock()
    fake_patch = mock.MagicMock()
    fake_patch.raise_for_status = mock.MagicMock()

    with mock.patch("filament_iq.filament_profile_lookup.requests.get",
                    return_value=fake_get), \
         mock.patch("filament_iq.filament_profile_lookup.requests.patch",
                    return_value=fake_patch) as mock_patch:
        _verify(app, 7, "reject")

    patched_extra = mock_patch.call_args[1]["json"]["extra"]
    assert "profile_url" not in patched_extra  # None → key removed
    assert "profile_name" not in patched_extra  # None → key removed
    assert patched_extra["other"] == "x"  # unrelated key preserved

    results = [e for e in app._events_fired
               if e["event"] == "filament_iq_profile_verify_result"]
    assert results[0]["success"] is True


def test_verify_no_match_clears_spoolman_and_fires_success():
    """no_match → removes profile_url and profile_name from extra, fires success."""
    app = TestableFilamentProfileLookup()

    fake_get = mock.MagicMock()
    fake_get.json.return_value = {"id": 7, "extra": {}}
    fake_get.raise_for_status = mock.MagicMock()
    fake_patch = mock.MagicMock()
    fake_patch.raise_for_status = mock.MagicMock()

    with mock.patch("filament_iq.filament_profile_lookup.requests.get",
                    return_value=fake_get), \
         mock.patch("filament_iq.filament_profile_lookup.requests.patch",
                    return_value=fake_patch) as mock_patch:
        _verify(app, 7, "no_match")

    patched_extra = mock_patch.call_args[1]["json"]["extra"]
    assert "profile_url" not in patched_extra  # None → key removed
    assert "profile_name" not in patched_extra  # None → key removed

    results = [e for e in app._events_fired
               if e["event"] == "filament_iq_profile_verify_result"]
    assert results[0]["success"] is True


def test_patch_spoolman_extra_preserves_existing_keys():
    """confirm preserves unrelated existing extra keys when patching."""
    app = TestableFilamentProfileLookup()

    fake_get = mock.MagicMock()
    fake_get.json.return_value = {"id": 7, "extra": {"other_key": "preserved"}}
    fake_get.raise_for_status = mock.MagicMock()
    fake_patch = mock.MagicMock()
    fake_patch.raise_for_status = mock.MagicMock()

    with mock.patch("filament_iq.filament_profile_lookup.requests.get",
                    return_value=fake_get), \
         mock.patch("filament_iq.filament_profile_lookup.requests.patch",
                    return_value=fake_patch) as mock_patch:
        _verify(
            app, 7, "confirm",
            profile_id=128,
            profile_url="https://3dfilamentprofiles.com/filament/details/128",
            profile_name="Bambu Lab · PLA Basic · Light Gray",
        )

    patched_extra = mock_patch.call_args[1]["json"]["extra"]
    assert patched_extra["other_key"] == "preserved"
    assert patched_extra["profile_url"] == '"https://3dfilamentprofiles.com/filament/details/128"'
    assert patched_extra["profile_name"] == '"Bambu Lab · PLA Basic · Light Gray"'

    results = [e for e in app._events_fired
               if e["event"] == "filament_iq_profile_verify_result"]
    assert results[0]["success"] is True


def test_patch_spoolman_extra_failure_fires_verify_failure():
    """A network error in _patch_spoolman_extra propagates and fires verify failure."""
    app = TestableFilamentProfileLookup()

    with mock.patch("filament_iq.filament_profile_lookup.requests.get",
                    side_effect=Exception("network down")):
        _verify(
            app, 7, "confirm",
            profile_id=128,
            profile_url="https://3dfilamentprofiles.com/filament/details/128",
            profile_name="Bambu Lab · PLA Basic · Light Gray",
        )

    error_logs = [msg for msg, lvl in app._log_calls if lvl == "ERROR"]
    assert any("PROFILE_VERIFY_FAILED" in m for m in error_logs)

    results = [e for e in app._events_fired
               if e["event"] == "filament_iq_profile_verify_result"]
    assert results[0]["success"] is False


def test_verify_invalid_payload_fires_failure():
    """Invalid filament_id or unknown action → PROFILE_VERIFY_SKIP, success=False."""
    app = TestableFilamentProfileLookup()

    _verify(app, 0, "confirm")  # filament_id=0 is invalid

    results = [e for e in app._events_fired
               if e["event"] == "filament_iq_profile_verify_result"]
    assert len(results) == 1
    assert results[0]["success"] is False

    app._events_fired.clear()
    app._log_calls.clear()

    _verify(app, 7, "unknown_action")

    results = [e for e in app._events_fired
               if e["event"] == "filament_iq_profile_verify_result"]
    assert results[0]["success"] is False
