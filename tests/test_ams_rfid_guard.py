#!/usr/bin/env python3
"""
Tests for ams_rfid_guard — RFID determinism auditor.
Run: python -m pytest tests/test_ams_rfid_guard.py -v
"""

import datetime
import os
import sys
import types

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

from filament_iq.ams_rfid_guard import AmsRfidGuard, ReasonCode


# ── test harness ──────────────────────────────────────────────────────

_DEFAULT_TEST_ARGS = {
    "printer_serial": "01p00c5a3101668",
    "printer_model": "p1s",
    "spoolman_url": "http://192.0.2.1:7912",
    "enabled": True,
    "dry_run": False,
    "notify_cooldown_minutes": 360,
}


class _TestableGuard(AmsRfidGuard):
    """AmsRfidGuard with mocked I/O."""

    def __init__(self, args=None, spools=None):
        a = dict(_DEFAULT_TEST_ARGS)
        a.update(args or {})
        super().__init__(None, "test_guard", None, a, None, None, None)
        self._mock_spools = spools or []
        self._log_calls = []
        self._service_calls = []
        self._patch_calls = []
        self._now = datetime.datetime(2026, 3, 11, 12, 0, 0)

        # Run initialize logic inline (minus run_every/listen_state)
        self.enabled = bool(a.get("enabled", True))
        self.spoolman_base_url = str(
            a.get("spoolman_url", "")
        ).rstrip("/")
        self.scan_interval_seconds = int(a.get("scan_interval_seconds", 300))
        self.dry_run = bool(a.get("dry_run", False))
        self.notify_cooldown_minutes = int(a.get("notify_cooldown_minutes", 360))
        self.cache_sensor = ""
        self.use_cache_trigger = False
        self.missing_ha_spool_uuid_mode = str(
            a.get("missing_ha_spool_uuid_mode", "quarantine")
        ).strip().lower()
        import re
        raw_patterns = a.get("rfid_managed_patterns", ["bambu", "bambu lab"])
        if isinstance(raw_patterns, str):
            raw_patterns = [p.strip() for p in raw_patterns.split(",") if p.strip()]
        self.rfid_managed_patterns = [
            re.compile(p, re.IGNORECASE) for p in raw_patterns if p
        ]
        self._last_notify_by_key = {}

    def initialize(self):
        pass

    def log(self, msg, level="INFO"):
        self._log_calls.append((msg, level))

    def call_service(self, service, **kwargs):
        self._service_calls.append({"service": service, **kwargs})

    def run_every(self, *a, **kw):
        pass

    def listen_state(self, *a, **kw):
        pass

    def datetime(self):
        return self._now

    def _spoolman_get(self, path):
        """Mock: return configured spool list."""
        return self._mock_spools

    def _spoolman_patch(self, path, payload):
        """Mock: capture patch calls."""
        self._patch_calls.append({"path": path, "payload": payload})
        return {}


def _has_log(app, substring):
    return any(substring in msg for msg, _ in app._log_calls)


def _spool(sid, location="AMS1_Slot1", tag_uid=None, ha_spool_uuid=None,
           lot_nr=None, filament_name="Bambu PLA", vendor_name="Bambu Lab"):
    """Build a minimal Spoolman spool dict."""
    extra = {}
    if tag_uid:
        extra["rfid_tag_uid"] = tag_uid
    if ha_spool_uuid:
        extra["ha_spool_uuid"] = ha_spool_uuid
    return {
        "id": sid,
        "location": location,
        "lot_nr": lot_nr or "",
        "extra": extra,
        "filament": {
            "name": filament_name,
            "vendor": {"name": vendor_name},
        },
    }


# ── R2 #1: RFID Guard tests ──────────────────────────────────────────

class TestGuardNoViolation:
    """Guard should NOT fire when spool has valid identity."""

    def test_spool_with_lot_nr_passes(self):
        """Spool in AMS with lot_nr = no violation."""
        spools = [_spool(1, location="AMS1_Slot1", lot_nr="LOT123")]
        app = _TestableGuard(spools=spools)
        app._run_scan({})
        assert not _has_log(app, "RFID_GUARD quarantine")
        assert not _has_log(app, "WARN_ONLY")
        assert len(app._patch_calls) == 0

    def test_spool_with_ha_spool_uuid_passes(self):
        """Spool in AMS with ha_spool_uuid = no violation."""
        spools = [_spool(1, location="AMS1_Slot1", ha_spool_uuid="abc-123")]
        app = _TestableGuard(spools=spools)
        app._run_scan({})
        assert not _has_log(app, "RFID_GUARD quarantine")
        assert len(app._patch_calls) == 0

    def test_spool_not_in_ams_ignored(self):
        """Spools on Shelf are never checked for violations."""
        spools = [_spool(1, location="Shelf")]
        app = _TestableGuard(spools=spools)
        app._run_scan({})
        assert not _has_log(app, "RFID_GUARD quarantine")
        assert not _has_log(app, "WARN_ONLY")

    def test_non_bambu_filament_no_identity_passes(self):
        """Non-Bambu filament without identity = no violation."""
        spools = [_spool(1, location="AMS1_Slot1", filament_name="Generic PLA",
                         vendor_name="Overture")]
        app = _TestableGuard(spools=spools)
        app._run_scan({})
        assert not _has_log(app, "RFID_GUARD quarantine")
        assert len(app._patch_calls) == 0


class TestGuardViolation:
    """Guard fires on RFID-managed filament missing identity."""

    def test_bambu_filament_no_identity_quarantined(self):
        """Bambu filament in AMS with no lot_nr or ha_spool_uuid → quarantine."""
        spools = [_spool(1, location="AMS1_Slot1", filament_name="Bambu PLA",
                         vendor_name="Bambu Lab")]
        app = _TestableGuard(spools=spools)
        app._run_scan({})
        assert _has_log(app, "RFID_GUARD quarantine spool_id=1")
        assert len(app._patch_calls) == 1
        assert app._patch_calls[0]["payload"] == {"location": "QUARANTINE"}

    def test_tag_uid_no_identity_quarantined(self):
        """Spool with rfid_tag_uid but no lot_nr/uuid → quarantine."""
        spools = [_spool(1, location="AMS128_Slot1", tag_uid="AABB0011",
                         filament_name="Generic PLA", vendor_name="Overture")]
        app = _TestableGuard(spools=spools)
        app._run_scan({})
        assert _has_log(app, "RFID_GUARD quarantine spool_id=1")
        assert len(app._patch_calls) == 1

    def test_violation_sends_notification(self):
        """Quarantine triggers a persistent_notification."""
        spools = [_spool(1, location="AMS1_Slot1")]
        app = _TestableGuard(spools=spools)
        app._run_scan({})
        notif_calls = [c for c in app._service_calls
                       if c["service"] == "persistent_notification/create"]
        assert len(notif_calls) == 1
        assert "Quarantined" in notif_calls[0]["title"]

    def test_dry_run_no_patch(self):
        """dry_run=True logs warning but does NOT patch Spoolman."""
        spools = [_spool(1, location="AMS1_Slot1")]
        app = _TestableGuard(args={"dry_run": True}, spools=spools)
        app._run_scan({})
        assert len(app._patch_calls) == 0
        assert _has_log(app, "DRY_RUN")


class TestGuardNotifyDedup:
    """_last_notify_by_key prevents duplicate notifications within cooldown."""

    def test_second_scan_within_cooldown_no_notify(self):
        """Same violation scanned twice within cooldown → only one notification."""
        spools = [_spool(1, location="AMS1_Slot1")]
        app = _TestableGuard(spools=spools)

        # First scan
        app._run_scan({})
        first_notif_count = len([c for c in app._service_calls
                                 if c["service"] == "persistent_notification/create"])
        assert first_notif_count == 1

        # Second scan — same spool, within cooldown
        app._patch_calls.clear()
        app._mock_spools = [_spool(1, location="AMS1_Slot1")]
        app._run_scan({})
        second_notif_count = len([c for c in app._service_calls
                                  if c["service"] == "persistent_notification/create"])
        # Still only 1 notification total (dedup blocked the second)
        assert second_notif_count == 1

    def test_scan_after_cooldown_sends_notify(self):
        """Same violation after cooldown expires → new notification."""
        spools = [_spool(1, location="AMS1_Slot1")]
        app = _TestableGuard(spools=spools)

        # First scan
        app._run_scan({})
        assert len([c for c in app._service_calls
                    if c["service"] == "persistent_notification/create"]) == 1

        # Advance time past cooldown (360 min default)
        app._now += datetime.timedelta(minutes=361)
        app._mock_spools = [_spool(1, location="AMS1_Slot1")]
        app._run_scan({})
        assert len([c for c in app._service_calls
                    if c["service"] == "persistent_notification/create"]) == 2


class TestGuardWarnOnly:
    """missing_ha_spool_uuid_mode=warn_only logs but does not quarantine."""

    def test_warn_only_no_quarantine_patch(self):
        """warn_only mode: violation logged as WARN_ONLY, no PATCH to Spoolman."""
        spools = [_spool(1, location="AMS1_Slot1", tag_uid="AABB0011")]
        app = _TestableGuard(
            args={"missing_ha_spool_uuid_mode": "warn_only"},
            spools=spools,
        )
        app._run_scan({})
        assert _has_log(app, "WARN_ONLY")
        assert len(app._patch_calls) == 0


class TestGuardSpoolmanUnavailable:
    """Guard handles Spoolman being unreachable gracefully."""

    def test_fetch_failure_logs_warning(self):
        """If _spoolman_get raises, scan logs warning and returns cleanly."""
        app = _TestableGuard()

        def _fail(path):
            raise ConnectionError("Connection refused")

        app._spoolman_get = _fail
        app._run_scan({})
        assert _has_log(app, "all endpoints failed")


class TestReasonCode:
    """ReasonCode resolve covers known and unknown values."""

    def test_known_reason(self):
        assert ReasonCode.resolve("RFID_TAG_MANUAL") == "RFID_TAG_MANUAL"

    def test_unknown_reason(self):
        assert ReasonCode.resolve("BOGUS") == "UNKNOWN"


# ── helper method tests ──────────────────────────────────────────────

class TestJsonTextToStr:
    """_json_text_to_str edge cases."""

    def test_none_returns_empty(self):
        app = _TestableGuard()
        assert app._json_text_to_str(None) == ""

    def test_empty_string_returns_empty(self):
        app = _TestableGuard()
        assert app._json_text_to_str("") == ""

    def test_json_encoded_string(self):
        app = _TestableGuard()
        assert app._json_text_to_str('"hello"') == "hello"

    def test_json_null_returns_empty(self):
        app = _TestableGuard()
        assert app._json_text_to_str("null") == ""

    def test_plain_string(self):
        app = _TestableGuard()
        assert app._json_text_to_str("AABB0011") == "AABB0011"

    def test_quoted_string_stripped(self):
        app = _TestableGuard()
        result = app._json_text_to_str('"quoted"')
        assert result == "quoted"


class TestSafeInt:
    """_safe_int edge cases."""

    def test_valid_int(self):
        app = _TestableGuard()
        assert app._safe_int("42") == 42

    def test_none_returns_default(self):
        app = _TestableGuard()
        assert app._safe_int(None) == 0

    def test_invalid_returns_default(self):
        app = _TestableGuard()
        assert app._safe_int("abc", 99) == 99

    def test_float_string_fails(self):
        app = _TestableGuard()
        assert app._safe_int("3.14") == 0


class TestGetTagUid:
    """_get_tag_uid edge cases."""

    def test_non_dict_extra(self):
        app = _TestableGuard()
        assert app._get_tag_uid("not_a_dict") == ""

    def test_rfid_uid_fallback(self):
        app = _TestableGuard()
        assert app._get_tag_uid({"rfid_uid": "AABB"}) == "AABB"

    def test_tag_uid_uppercase(self):
        app = _TestableGuard()
        assert app._get_tag_uid({"rfid_tag_uid": "aabb0011"}) == "AABB0011"


class TestGetHaSpoolUuid:
    """_get_ha_spool_uuid edge cases."""

    def test_non_dict_extra(self):
        app = _TestableGuard()
        assert app._get_ha_spool_uuid("not_a_dict") == ""

    def test_ha_uuid_fallback(self):
        app = _TestableGuard()
        assert app._get_ha_spool_uuid({"ha_uuid": "xyz-123"}) == "xyz-123"

    def test_empty_value(self):
        app = _TestableGuard()
        assert app._get_ha_spool_uuid({"ha_spool_uuid": ""}) == ""


class TestIsQuarantined:
    """_is_quarantined detects QUARANTINE location."""

    def test_quarantine_location(self):
        app = _TestableGuard()
        assert app._is_quarantined({"location": "QUARANTINE"}) is True

    def test_lowercase_quarantine(self):
        app = _TestableGuard()
        assert app._is_quarantined({"location": "quarantine"}) is True

    def test_ams_location_not_quarantined(self):
        app = _TestableGuard()
        assert app._is_quarantined({"location": "AMS1_Slot1"}) is False

    def test_none_location(self):
        app = _TestableGuard()
        assert app._is_quarantined({"location": None}) is False


class TestIsRfidManagedFilament:
    """_is_rfid_managed_filament detection paths."""

    def test_rfid_managed_extra_flag(self):
        """extra.rfid_managed=True → RFID managed."""
        app = _TestableGuard()
        fil = {"extra": {"rfid_managed": True}, "name": "Generic PLA"}
        assert app._is_rfid_managed_filament(fil) is True

    def test_vendor_dict_with_name(self):
        """vendor as dict with name 'Bambu Lab' → RFID managed."""
        app = _TestableGuard()
        fil = {"vendor": {"name": "Bambu Lab"}, "name": "PLA"}
        assert app._is_rfid_managed_filament(fil) is True

    def test_vendor_as_string(self):
        """vendor as string 'bambu' → RFID managed."""
        app = _TestableGuard()
        fil = {"vendor": "bambu lab", "name": "PLA"}
        assert app._is_rfid_managed_filament(fil) is True

    def test_non_rfid_filament(self):
        """Non-Bambu vendor, no rfid_managed → NOT managed."""
        app = _TestableGuard()
        fil = {"vendor": {"name": "Overture"}, "name": "PLA", "extra": {}}
        assert app._is_rfid_managed_filament(fil) is False

    def test_non_dict_filament(self):
        """Non-dict input → False."""
        app = _TestableGuard()
        assert app._is_rfid_managed_filament("not_a_dict") is False

    def test_name_pattern_match(self):
        """Filament name matches rfid_managed_patterns → managed."""
        app = _TestableGuard()
        fil = {"name": "Bambu PLA Silk", "vendor": {"name": "Unknown"}}
        assert app._is_rfid_managed_filament(fil) is True


class TestCheckViolationEdgeCases:
    """_check_violation with non-dict extra/filament."""

    def test_non_dict_extra(self):
        """Spool with extra as string → treated as empty extra."""
        app = _TestableGuard()
        spool = _spool(1, location="AMS1_Slot1")
        spool["extra"] = "corrupted"
        result = app._check_violation(spool)
        # Bambu Lab vendor still triggers violation
        assert result is not None

    def test_non_dict_filament(self):
        """Spool with filament as string → treated as empty filament."""
        app = _TestableGuard()
        spool = {"id": 1, "location": "AMS1_Slot1", "extra": {},
                 "filament": "corrupted", "lot_nr": ""}
        result = app._check_violation(spool)
        # No Bambu filament, no tag_uid → no violation
        assert result is None

    def test_none_extra(self):
        """extra=None → no crash."""
        app = _TestableGuard()
        spool = _spool(1, location="AMS1_Slot1")
        spool["extra"] = None
        result = app._check_violation(spool)
        assert result is not None  # Bambu filament with no identity


class TestQuarantinePatchFailure:
    """_quarantine_spool handles PATCH failure."""

    def test_patch_failure_returns_false(self):
        app = _TestableGuard()
        def _fail(path, payload):
            raise ConnectionError("refused")
        app._spoolman_patch = _fail
        spool = _spool(1, location="AMS1_Slot1")
        violation = {"reason": "RFID_TAG_MANUAL", "filament_name": "PLA",
                     "location": "AMS1_Slot1", "tag_uid": "AA", "ha_spool_uuid": ""}
        result = app._quarantine_spool(spool, violation)
        assert result is False
        assert _has_log(app, "quarantine PATCH failed")

    def test_quarantine_invalid_spool_id(self):
        app = _TestableGuard()
        spool = {"id": 0}
        result = app._quarantine_spool(spool, {"reason": "TEST"})
        assert result is False


class TestDisabledGuard:
    """Guard disabled via config."""

    def test_disabled_scan_noop(self):
        """enabled=False → _run_scan does nothing."""
        spools = [_spool(1, location="AMS1_Slot1")]
        app = _TestableGuard(args={"enabled": False}, spools=spools)
        app._run_scan({})
        assert len(app._patch_calls) == 0
        # No scan complete log (since it returns early)
        assert not _has_log(app, "scan complete")


class TestScanQuarantinedSkipped:
    """Quarantined spools are skipped during scan."""

    def test_quarantined_spool_skipped(self):
        """Spool in QUARANTINE location → skipped, not checked."""
        spools = [_spool(1, location="QUARANTINE")]
        app = _TestableGuard(spools=spools)
        app._run_scan({})
        assert len(app._patch_calls) == 0


class TestMaybeNotifyException:
    """_maybe_notify handles call_service failure."""

    def test_notify_service_fails(self):
        """If persistent_notification/create raises, warning logged."""
        app = _TestableGuard()
        def _fail(service, **kwargs):
            raise RuntimeError("HA unavailable")
        app.call_service = _fail
        violation = {
            "reason": "RFID_TAG_MANUAL", "filament_name": "PLA",
            "violation": "test", "expected": "test", "found": "test",
            "tag_uid": "AA", "ha_spool_uuid": "",
        }
        app._maybe_notify(1, violation)
        assert _has_log(app, "notify failed")
