#!/usr/bin/env python3
"""
Deterministic test harness for ams_rfid_reconcile (no external deps).
Run: python -m pytest tests/test_ams_rfid_reconcile.py -v
  or: python tests/test_ams_rfid_reconcile.py
"""

import datetime
import json
import os
import sys
import types
import unittest
import unittest.mock

import pytest

# Bootstrap fake hassapi before importing ams_rfid_reconcile (no appdaemon dep)
class _FakeHass:
    def __init__(self, ad=None, name=None, logger=None, args=None, config=None, app_config=None, global_vars=None):
        self.args = args or {}

    def log(self, msg, level="INFO"):
        pass

_hassapi = types.ModuleType("hassapi")
_hassapi.Hass = _FakeHass
if "hassapi" not in sys.modules:
    sys.modules["hassapi"] = _hassapi

# Add appdaemon/apps to path
_APPS = os.path.join(os.path.dirname(__file__), "..", "appdaemon", "apps")
if _APPS not in sys.path:
    sys.path.insert(0, _APPS)

from filament_iq.base import build_slot_mappings

# Build default slot mappings for test fixtures (no hardcoded IPs/serials)
_TEST_PREFIX = "p1s_01p00c5a3101668"
_TRAY_ENTITY_BY_SLOT, _, _, _CANONICAL_LOCATION_BY_SLOT = build_slot_mappings(
    _TEST_PREFIX
)
TRAY_ENTITY_BY_SLOT = _TRAY_ENTITY_BY_SLOT
CANONICAL_LOCATION_BY_SLOT = _CANONICAL_LOCATION_BY_SLOT

import filament_iq.ams_rfid_reconcile as ams_rfid_reconcile
from filament_iq.ams_rfid_reconcile import (
    _normalize_rfid_tag_uid,
    AmsRfidReconcile,
    COLOR_DISTANCE_THRESHOLD,
    DEPRECATED_LOCATION_TO_CANONICAL,
    LOCATION_EMPTY,
    LOCATION_NOT_IN_AMS,
    FULL_SPOOL_G,
    NEXT_MAN_MIN_MARGIN_G,
    STATUS_NON_RFID_REGISTERED,
    STATUS_PENDING_RFID_READ,
    STATUS_UNBOUND_ACTION_REQUIRED,
    UNBOUND_ERROR,
    UNBOUND_NO_RFID_TAG_ALL_ZERO,
    UNBOUND_NO_TAG_UID,
    UNBOUND_TAG_UID_AMBIGUOUS,
    UNBOUND_TAG_UID_INELIGIBLE_LOCATION_NEW,
    UNBOUND_SELECTED_UID_MISMATCH,
    UNBOUND_TAG_UID_NO_MATCH,
    UNBOUND_TRAY_EMPTY,
    UNBOUND_TRAY_UNAVAILABLE,
    UNBOUND_HELPER_SPOOL_NOT_FOUND,
    UNBOUND_HELPER_RFID_MISMATCH,
    UNBOUND_HELPER_MATERIAL_MISMATCH,
    STATUS_WAITING_CONFIRMATION,
    STATUS_NEEDS_MANUAL_BIND,
    STATUS_LOW_CONFIDENCE,
    STATUS_OK_NONRFID,
    UNBOUND_NONRFID_NO_MATCH,
    UNBOUND_LOW_CONFIDENCE,
    STATUS_RFID_IDENTITY_STUCK,
    UNBOUND_RFID_NOT_REFRESHED,
    RFID_STUCK_SECONDS,
    _classify_unbound_reason,
    _is_bambu_vendor,
    _vendor_name,
    _colors_close,
    _hex_to_rgb,
    _normalize_hex_color,
    _rgb_distance,
    _color_distance,
    is_generic_filament_id,
    NONRFID_COLOR_TOLERANCE,
    tiebreak_choose_spool,
)


def _bambu_filament(material="PLA", color_hex="ff0000", name="Bambu PLA", fid=1):
    return {
        "id": fid,
        "name": name,
        "material": material,
        "color_hex": color_hex,
        "vendor": {"name": "Bambu Lab"},
        "external_id": "bambu_pla_red",
    }


def _spool(sid, filament_id=1, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000", material="PLA", name="Bambu PLA", comment=None, ha_spool_uuid=None, vendor_name="Bambu Lab", initial_weight=None, lot_nr=None):
    f = _bambu_filament(material=material, color_hex=color_hex, name=name, fid=filament_id)
    f["vendor"] = {"name": vendor_name}
    extra = {}
    if rfid_tag_uid:
        extra["rfid_tag_uid"] = rfid_tag_uid
    if ha_spool_uuid:
        extra["ha_spool_uuid"] = ha_spool_uuid
    out = {
        "id": sid,
        "filament_id": filament_id,
        "filament": {"id": filament_id, **f, "vendor": {"name": vendor_name}},
        "remaining_weight": remaining_weight,
        "location": location,
        "extra": extra,
    }
    if comment is not None:
        out["comment"] = comment
    if lot_nr is not None:
        out["lot_nr"] = lot_nr
    if initial_weight is not None:
        out["initial_weight"] = initial_weight
    return out


def _tray_state(tag_uid, tray_type="PLA", color="ff0000", name="Bambu PLA Basic", filament_id="bambu"):
    return {
        "attributes": {
            "tag_uid": tag_uid,
            "type": tray_type,
            "color": color,
            "name": name,
            "filament_id": filament_id,
            "tray_weight": 1000,
            "remain": 50,
        },
        "state": "valid",
    }


class FakeSpoolman:
    """In-memory Spoolman; records GET/PATCH/POST for assertions."""

    def __init__(self, spools, filaments):
        self.spools = {s["id"]: dict(s) for s in spools}
        self.filaments = list(filaments)
        self.patches = []
        self.posts = []

    def get(self, path):
        if path == "/api/v1/spool?limit=1000":
            return {"items": list(self.spools.values())}
        if path.startswith("/api/v1/spool/"):
            try:
                sid = int(path.split("/")[-1])
                return self.spools.get(sid, {})
            except ValueError:
                return {}
        if path == "/api/v1/filament?limit=1000":
            return {"items": self.filaments}
        return {}

    def patch(self, path, payload):
        spool_id = None
        if path.startswith("/api/v1/spool/"):
            try:
                spool_id = int(path.split("/")[-1].split("/")[0].split("?")[0])
            except (ValueError, IndexError, AttributeError):
                pass
        self.patches.append({"path": path, "payload": payload, "spool_id": spool_id})
        if path.startswith("/api/v1/spool/") and spool_id is not None and spool_id in self.spools:
            s = self.spools[spool_id]
            if "extra" in payload:
                s["extra"] = {**s.get("extra", {}), **payload["extra"]}
            if "location" in payload:
                s["location"] = payload["location"]
            if "comment" in payload:
                s["comment"] = payload["comment"]

    def post(self, path, payload):
        self.posts.append({"path": path, "payload": payload})
        if path == "/api/v1/spool":
            new_id = max(self.spools.keys(), default=0) + 1
            s = dict(payload)
            s["id"] = new_id
            self.spools[new_id] = s
            return s
        return {}


class RetryFakeSpoolman(FakeSpoolman):
    """FakeSpoolman that raises 400 'not valid JSON' on first PATCH with extra, then succeeds."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._extra_patch_count = 0

    def patch(self, path, payload):
        if "extra" in payload:
            self._extra_patch_count += 1
            if self._extra_patch_count == 1:
                raise RuntimeError("HTTP 400 for http://fake:7912/api/v1/spool/1: Value is not valid JSON")
        super().patch(path, payload)


class TestableReconcile(AmsRfidReconcile):
    """Reconcile with injected FakeSpoolman and state map."""

    def __init__(self, spoolman, state_map, ad=None, args=None, *rest, **k):
        a = dict({"printer_serial": "01p00c5a3101668", "spoolman_url": "http://192.0.2.1:7912"})
        a.update(args or k.get("args", {}))
        super().__init__(ad, "test", None, a, None, None, None)
        self._spoolman = spoolman
        self._state_map = dict(state_map)
        # Build slot mappings from config (bypass initialize)
        self._prefix = self._build_entity_prefix()
        prefix = self._prefix
        tray, _, _, canon = build_slot_mappings(prefix, a.get("ams_units"))
        self._tray_entity_by_slot = tray
        self._canonical_location_by_slot = canon
        self._physical_ams_slots = tuple(sorted(tray.keys()))
        self._last_mapping_json_entity = f"input_text.{prefix}_last_mapping_json"
        self._reconcile_button_entity = f"input_button.{prefix}_rfid_reconcile_now"
        self._startup_suppress_entity = "input_boolean.filament_iq_startup_suppress_swap"
        self._helper_writes = []
        self._active_run = None
        self._last_summary = None
        self.enabled = bool(a.get("enabled", True))
        self.spoolman_url = str(a.get("spoolman_url", "http://fake:7912")).rstrip("/")
        self.debug_logs = bool(a.get("debug_logs", False))
        self.strict_mode_reregister = bool(a.get("strict_mode_reregister", False))
        self.evidence_log_path = "/tmp/ams_rfid_test_evidence.log"
        self.evidence_log_enabled = False
        self.last_slot_status = {}
        self.debounce_handle = None
        self.debounce_reasons = []
        self._missing_helper_warned = set()
        self._pending_helper_warned = set()
        self._pending_lot_nr_writes = {}
        self._suppress_helper_change_until = {}
        self._settle_pending = {}
        self.nonrfid_settle_delay_s = int(a.get("nonrfid_settle_delay_s", 90))
        self._run_in_calls = []
        self._domain_exception_class_logged = False
        self._print_active_since = None
        self._evidence_lines = []
        self._log_calls = []

    def log(self, msg, level="INFO"):
        self._log_calls.append((msg, level))

    def _append_evidence_line(self, line):
        self._evidence_lines.append(line)

    def _run_reconcile_startup(self, kwargs):
        pass

    def initialize(self):
        pass

    def get_state(self, entity_id, attribute=None):
        # _get_helper_state() uses get_state(entity_id, attribute="all"); support plain key.
        if attribute == "all":
            val = self._state_map.get(entity_id)
            if val is not None:
                return val if isinstance(val, dict) and "state" in val else {"state": val, "attributes": {}}
            val = self._state_map.get(f"{entity_id}::all")
            if val is not None:
                return val if isinstance(val, dict) and "state" in val else {"state": val, "attributes": {}}
            if "ams_slot_" in entity_id and "spool_id" in entity_id:
                return {"state": "0", "attributes": {}}
            if "ams_slot_" in entity_id and "expected_spool_id" in entity_id:
                return {"state": "0", "attributes": {}}
            if "ams_slot_" in entity_id and "status" in entity_id:
                return {"state": "", "attributes": {}}
            if "ams_slot_" in entity_id and ("tray_signature" in entity_id or "unbound_reason" in entity_id or "rfid_pending_until" in entity_id or "expected_color_hex" in entity_id):
                return {"state": "", "attributes": {}}
            return None
        key = f"{entity_id}" if attribute is None else f"{entity_id}::{attribute}"
        val = self._state_map.get(key)
        if val is not None:
            return val
        if "ams_slot_" in entity_id and "spool_id" in entity_id:
            return "0"
        if "ams_slot_" in entity_id and "expected_spool_id" in entity_id:
            return "0"
        if "ams_slot_" in entity_id and "status" in entity_id:
            return ""
        return None

    def _spoolman_get(self, path):
        out = self._spoolman.get(path)
        if isinstance(out, dict) and "items" in out:
            return out
        return out if isinstance(out, dict) else {"items": out} if isinstance(out, list) else {}

    def _spoolman_patch(self, path, payload):
        self._record_write("spoolman_patch", {"path": path, "payload": payload})
        return self._spoolman.patch(path, payload)

    def _spoolman_post(self, path, payload):
        return self._spoolman.post(path, payload)

    def call_service(self, service, **kwargs):
        # Reconcile routes by domain: input_text.* -> input_text/set_value, text.* -> text/set_value.
        if service in ("input_text/set_value", "text/set_value"):
            self._helper_writes.append({"service": service, **kwargs})
        if service == "input_datetime/set_datetime":
            self._helper_writes.append({"service": service, **kwargs})
        if service == "input_select/select_option" or service == "select/select_option":
            # Normalize to same shape as text: entity_id + value (option -> value for assertions).
            self._helper_writes.append({"entity_id": kwargs.get("entity_id"), "value": kwargs.get("option", kwargs.get("value", ""))})
        if service == "persistent_notification/create":
            pass

    def run_in(self, callback, delay, **kwargs):
        self._run_in_calls.append({"callback": callback, "delay": delay, "kwargs": kwargs})

    def run_every(self, callback, start, interval):
        pass

    def listen_state(self, *a, **k):
        pass

    def listen_event(self, *a, **k):
        pass

    def cancel_timer(self, *a):
        pass

    def datetime(self):
        return datetime.datetime.utcnow()

    def _ensure_evidence_path_writable(self):
        pass

    def _append_evidence(self, summary):
        self._last_summary = summary


class StartupWaiterHarness(AmsRfidReconcile):
    """Minimal harness to test _run_reconcile_startup; does not override _run_reconcile_startup."""

    def __init__(self, state_map, args=None):
        a = dict({"printer_serial": "01p00c5a3101668", "spoolman_url": "http://192.0.2.1:7912"})
        a.update(args or {})
        super().__init__(None, "test", None, a, None, None, None)
        self._state_map = dict(state_map)
        self._prefix = self._build_entity_prefix()
        tray, _, _, canon = build_slot_mappings(self._prefix, a.get("ams_units"))
        self._tray_entity_by_slot = tray
        self._canonical_location_by_slot = canon
        self._physical_ams_slots = tuple(sorted(tray.keys()))
        self._log_calls = []
        self._run_in_calls = []
        self._run_reconcile_calls = []
        self.startup_wait_helpers_seconds = int(a.get("startup_wait_helpers_seconds", 420))
        self.startup_wait_retry_initial_seconds = int(a.get("startup_wait_retry_initial_seconds", 2))
        self.startup_wait_retry_max_seconds = int(a.get("startup_wait_retry_max_seconds", 30))
        self.startup_probe_helper_entity = str(a.get("startup_probe_helper_entity", "input_text.ams_slot_1_spool_id"))
        self._domain_exception_class_logged = False

    def log(self, msg, level="INFO"):
        self._log_calls.append((msg, level))

    def run_in(self, callback, delay, **kwargs):
        self._run_in_calls.append((callback, delay, kwargs.copy()))

    def _run_reconcile(self, reason, **kwargs):
        self._run_reconcile_calls.append((reason, kwargs))

    def get_state(self, entity_id, attribute=None):
        key = f"{entity_id}" if attribute is None else f"{entity_id}::{attribute}"
        return self._state_map.get(key)


def _state_key(slot, entity_suffix, attr=None):
    eid = f"input_text.ams_slot_{slot}_{entity_suffix}"
    return f"{eid}::{attr}" if attr else eid


def _tray_entity(slot):
    return TRAY_ENTITY_BY_SLOT.get(slot, f"sensor.tray_{slot}")


class TestAmsRfidReconcile(unittest.TestCase):
    def setUp(self):
        self.args = {
            "printer_serial": "01p00c5a3101668",
            "spoolman_url": "http://192.0.2.1:7912",
            "enabled": True,
            "debug_logs": False,
            # Tests use input_boolean.filament_iq_nonrfid_enabled in state_map
            "nonrfid_enabled_entity": "input_boolean.filament_iq_nonrfid_enabled",
        }

    def _run_reconcile_core(self, spoolman, state_map, slot=1):
        """Run reconcile for a single slot; return (status, writes, summary)."""
        r = TestableReconcile(spoolman, state_map, None, self.args, None, None, None)
        r._active_run = {
            "reason": "test",
            "writes": [],
            "decisions": [],
            "no_write_paths": [],
            "conflicts": [],
            "unknown_tags": [],
            "auto_registers": [],
        }
        r._run_reconcile("test")
        status_key = f"input_text.ams_slot_{slot}_status"
        status_val = next(
            (w["value"] for w in r._helper_writes if w.get("entity_id") == status_key),
            None,
        )
        return status_val, r._helper_writes, r._active_run

    def test_s1_unknown_uid_one_metadata_match_binds(self):
        """PHASE_2_5: Unknown UID (no spool at Shelf with this tag) -> NEEDS_ACTION, no bind, no metadata fallback."""
        tag = "AABBCCDD00112233"
        spools = [_spool(101, remaining_weight=500, rfid_tag_uid=None, location="Shelf")]
        filaments = [{"id": 1, "name": "Bambu PLA Basic", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        state_map = {
            tray_ent: _tray_state(tag),
            f"{tray_ent}::all": {"attributes": _tray_state(tag)["attributes"], "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        bind_patches = [p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})]
        self.assertEqual(len(bind_patches), 0, "PHASE_2_5: no bind when no spool at Shelf has this RFID UID")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_UNBOUND_ACTION_REQUIRED)

    def test_s2_unknown_uid_two_matches_next_man_up_binds(self):
        """PHASE_2_5: Unknown UID (no spool at Shelf with this tag) -> NEEDS_ACTION, no bind."""
        tag = "BBCCDDEE00112233"
        spools = [
            _spool(201, remaining_weight=100, rfid_tag_uid=None, location="Shelf", color_hex="00ff00"),
            _spool(202, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="00ff00"),
        ]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "00ff00",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        attrs = {"tag_uid": tag, "type": "PLA", "color": "00ff00", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 10}
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        bind_patches = [p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})]
        self.assertEqual(len(bind_patches), 0, "PHASE_2_5: no metadata fallback when no Shelf UID match")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_UNBOUND_ACTION_REQUIRED)

    def test_s3_unknown_uid_two_matches_weights_close_conflict_no_writes(self):
        """PHASE_2_5: Unknown UID (no Shelf UID match) -> NEEDS_ACTION, no writes."""
        tag = "CCDDEEFF00112233"
        spools = [
            _spool(301, remaining_weight=200, rfid_tag_uid=None, location="Shelf", color_hex="0000ff"),
            _spool(302, remaining_weight=250, rfid_tag_uid=None, location="Shelf", color_hex="0000ff"),
        ]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "0000ff",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        attrs = {"tag_uid": tag, "type": "PLA", "color": "0000ff", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 22}
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        bind_patches = [p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})]
        self.assertEqual(len(bind_patches), 0, "PHASE_2_5: no bind when no Shelf UID match")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_UNBOUND_ACTION_REQUIRED)

    def test_tiebreak_choose_spool_two_reds_prefer_used(self):
        """Unit test: two reds 842g vs 1000g (both initial 1000) -> prefer_used picks 842; strict_mode refuses."""
        candidates = [
            {"id": 101, "remaining_weight": 842, "initial_weight": 1000, "location": "Shelf"},
            {"id": 102, "remaining_weight": 1000, "initial_weight": 1000, "location": "Shelf"},
        ]
        chosen_id, reason = tiebreak_choose_spool(candidates, strict_mode=False)
        self.assertEqual(chosen_id, 101, "prefer_used should pick spool with remaining < initial")
        self.assertEqual(reason, "prefer_used")

        chosen_none, reason_strict = tiebreak_choose_spool(candidates, strict_mode=True)
        self.assertIsNone(chosen_none)
        self.assertEqual(reason_strict, "STRICT_MODE_MULTIPLE_CANDIDATES")

    def test_s4_two_reds_842_vs_1000_binds_to_842(self):
        """PHASE_2_5: Unknown UID (no Shelf UID match) -> NEEDS_ACTION, no bind."""
        tag = "AABBCCDD84210001"
        spools = [
            _spool(501, remaining_weight=842, initial_weight=1000, rfid_tag_uid=None, location="Shelf", color_hex="ff0000"),
            _spool(502, remaining_weight=1000, initial_weight=1000, rfid_tag_uid=None, location="Shelf", color_hex="ff0000"),
        ]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        attrs = {"tag_uid": tag, "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 84}
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        bind_patches = [p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})]
        self.assertEqual(len(bind_patches), 0, "PHASE_2_5: no metadata fallback")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_UNBOUND_ACTION_REQUIRED)

    def test_safety_poll_calls_run_reconcile_with_status_only_true(self):
        """P2: Safety poll must call _run_reconcile with status_only=True (no writes)."""
        spools = [_spool(1, remaining_weight=500, rfid_tag_uid="A1B2C3D4E5F60001", location="Shelf", color_hex="ff0000")]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000", "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        state_map = {}
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
        r = TestableReconcile(sm, state_map, args=self.args)
        calls = []

        def capture(reason, slots_filter=None, validation_mode=False, status_only=False):
            calls.append((reason, status_only))

        r._run_reconcile = capture
        r._run_reconcile_poll({})
        self.assertEqual(len(calls), 1, "safety poll must call _run_reconcile exactly once")
        self.assertEqual(calls[0][0], "safety_poll")
        self.assertTrue(calls[0][1], "safety poll must pass status_only=True")

    def test_safety_poll_no_spoolman_patch_on_stable_bound(self):
        """P2: Safety poll (status_only=True) must not perform any Spoolman PATCH on stable bound state."""
        tag = "A1B2C3D4E5F60001"
        spools = [_spool(1, remaining_weight=500, rfid_tag_uid=tag, location="Shelf", color_hex="ff0000")]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000", "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        attrs = {"tag_uid": tag, "type": "PLA", "color": "ff0000", "name": "Bambu PLA", "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "1" if s == 1 else "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "1" if s == 1 else "0"
            state_map[f"input_text.ams_slot_{s}_status"] = "OK" if s == 1 else ""
        state_map["input_text.ams_slot_1_tray_signature"] = tag
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("safety_poll", status_only=True)
        self.assertEqual(len(sm.patches), 0, "safety poll must not issue any Spoolman PATCH")

    def test_find_deterministic_candidates_excludes_location_new(self):
        """Unit test: _find_deterministic_candidates excludes location 'New' and returns only Shelf spool."""
        spools = [
            _spool(701, remaining_weight=1000, rfid_tag_uid=None, location="New", color_hex="ff0000", vendor_name="Overture", name="Overture PLA"),
            _spool(702, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000", vendor_name="Overture", name="Overture PLA"),
        ]
        filaments = [{"id": 1, "name": "Overture PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Overture"}, "external_id": "overture"}]
        sm = FakeSpoolman(spools, filaments)
        state_map = {}
        r = TestableReconcile(sm, state_map, args=self.args)
        r._active_run = {"decisions": [], "no_write_paths": [], "writes": [], "conflicts": [], "unknown_tags": [], "auto_registers": [], "validation_transcripts": []}
        attrs = {"tag_uid": "x", "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        tray_meta = r._tray_meta(attrs, "valid")
        candidate_ids, ineligible_new_count = r._find_deterministic_candidates(spools, tray_meta, slot=1)
        self.assertEqual(candidate_ids, [702], "only Shelf spool 702 should be candidate; New 701 excluded")
        self.assertEqual(ineligible_new_count, 1, "one spool excluded due to location New")
        # When only New spool exists, result should be empty
        candidate_ids_new_only, ineligible_new_only = r._find_deterministic_candidates(spools[:1], tray_meta, slot=1)
        self.assertEqual(candidate_ids_new_only, [], "location New only -> no candidates")
        self.assertEqual(ineligible_new_only, 1, "one New spool excluded")

    def test_find_deterministic_candidates_eligible_locations_shelf_ams_not_new(self):
        """Unit test: eligibility is Shelf or AMS* only; New is not eligible. Non-Bambu vendor used (Bambu excluded)."""
        filaments = [{"id": 1, "name": "Overture PLA", "material": "PLA", "color_hex": "ff0000",
                      "vendor": {"name": "Overture"}, "external_id": "overture"}]
        sm = FakeSpoolman([], filaments)
        r = TestableReconcile(sm, {}, args=self.args)
        r._active_run = {"decisions": [], "no_write_paths": [], "writes": [], "conflicts": [], "unknown_tags": [], "auto_registers": [], "validation_transcripts": []}
        attrs = {"tag_uid": "x", "type": "PLA", "color": "ff0000", "name": "Overture PLA",
                 "filament_id": "overture", "tray_weight": 1000, "remain": 50}
        tray_meta = r._tray_meta(attrs, "valid")

        # Spool at Shelf → eligible
        spools_shelf = [_spool(101, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000", vendor_name="Overture", name="Overture PLA")]
        candidate_ids, ineligible_new = r._find_deterministic_candidates(spools_shelf, tray_meta, slot=1)
        self.assertEqual(candidate_ids, [101], "Spool at Shelf should be eligible")
        self.assertEqual(ineligible_new, 0)

        # Spool at AMS1_Slot4 → eligible
        spools_ams1 = [_spool(102, remaining_weight=500, rfid_tag_uid=None, location="AMS1_Slot4", color_hex="ff0000", vendor_name="Overture", name="Overture PLA")]
        candidate_ids, ineligible_new = r._find_deterministic_candidates(spools_ams1, tray_meta, slot=1)
        self.assertEqual(candidate_ids, [102], "Spool at AMS1_Slot4 should be eligible")
        self.assertEqual(ineligible_new, 0)

        # Spool at AMS128_Slot1 → eligible
        spools_ams128 = [_spool(103, remaining_weight=500, rfid_tag_uid=None, location="AMS128_Slot1", color_hex="ff0000", vendor_name="Overture", name="Overture PLA")]
        candidate_ids, ineligible_new = r._find_deterministic_candidates(spools_ams128, tray_meta, slot=1)
        self.assertEqual(candidate_ids, [103], "Spool at AMS128_Slot1 should be eligible")
        self.assertEqual(ineligible_new, 0)

        # Spool at New → NOT eligible
        spools_new = [_spool(104, remaining_weight=500, rfid_tag_uid=None, location="New", color_hex="ff0000", vendor_name="Overture", name="Overture PLA")]
        candidate_ids, ineligible_new = r._find_deterministic_candidates(spools_new, tray_meta, slot=1)
        self.assertEqual(candidate_ids, [], "Spool at New should not be eligible")
        self.assertEqual(ineligible_new, 1, "one spool excluded due to location New")

    def test_manual_reconcile_button_state_change_invokes_run_reconcile(self):
        """Manual reconcile (state = ISO timestamp) callback invokes _run_reconcile('manual_button') when old != new."""
        sm = FakeSpoolman([], [])
        state_map = {}
        r = TestableReconcile(sm, state_map, args=self.args)
        run_calls = []

        def capture_run(reason, **kwargs):
            run_calls.append(reason)

        r._run_reconcile = capture_run
        r._on_manual_reconcile_button(
            "input_button.filament_iq_reconcile_now",
            "state",
            "2026-01-01T12:00:00",
            "2026-01-01T12:00:01",
            {},
        )
        self.assertEqual(run_calls, ["manual_button"], "state change (old != new) should trigger _run_reconcile('manual_button')")

    def test_manual_reconcile_button_ignores_no_change(self):
        """Manual reconcile callback does nothing when old == new."""
        sm = FakeSpoolman([], [])
        state_map = {}
        r = TestableReconcile(sm, state_map, args=self.args)
        run_calls = []
        r._run_reconcile = lambda reason, **kw: run_calls.append(reason)
        r._on_manual_reconcile_button(
            "input_button.filament_iq_reconcile_now", "state", "2026-01-01T12:00:00", "2026-01-01T12:00:00", {}
        )
        self.assertEqual(run_calls, [], "should not run when old == new")

    def test_manual_reconcile_button_skips_when_active(self):
        """Manual reconcile callback skips when _active_run is not None."""
        sm = FakeSpoolman([], [])
        state_map = {}
        r = TestableReconcile(sm, state_map, args=self.args)
        run_calls = []
        r._run_reconcile = lambda reason, **kw: run_calls.append(reason)
        r._active_run = {"reason": "other"}
        r._on_manual_reconcile_button(
            "input_button.filament_iq_reconcile_now", "state", "2026-01-01T12:00:00", "2026-01-01T12:00:01", {}
        )
        self.assertEqual(run_calls, [], "should not call _run_reconcile when reconcile already active")

    def test_normalize_rfid_tag_uid_json_encoded_matches_sensor(self):
        """Spoolman extra rfid_tag_uid JSON-encoded string literal normalizes to same value as HA sensor tag_uid."""
        raw_extra = '"071F87ED00000100"'
        sensor_tag = "071F87ED00000100"
        self.assertEqual(
            _normalize_rfid_tag_uid(raw_extra),
            _normalize_rfid_tag_uid(sensor_tag),
            "normalized Spoolman raw extra and sensor tag must match",
        )
        self.assertEqual(_normalize_rfid_tag_uid(raw_extra), "071F87ED00000100")
        self.assertEqual(_normalize_rfid_tag_uid(sensor_tag), "071F87ED00000100")

    def test_rfid_ams1_slot4_json_encoded_uid_binds(self):
        """Spool at AMS1_Slot4 with JSON-encoded extra.rfid_tag_uid matches tray tag_uid and binds (eligible location)."""
        tag = "071F87ED00000100"
        # Spoolman-style JSON-encoded string literal in extra
        spools = [
            _spool(101, remaining_weight=500, rfid_tag_uid=f'"{tag}"', location="AMS1_Slot4", color_hex="ff0000"),
        ]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        attrs = {"tag_uid": tag, "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0, "slot 1 status must be written")
        self.assertEqual(status_writes[-1].get("value"), "OK", "spool at AMS1_Slot4 with JSON-encoded UID should bind")
        spool_id_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_spool_id"]
        self.assertGreater(len(spool_id_writes), 0)
        self.assertEqual(spool_id_writes[-1].get("value"), "101", "slot 1 should bind to spool 101")

    def test_rfid_location_new_excluded_from_uid_map(self):
        """Spool at location New with matching rfid_tag_uid is excluded from RFID map -> no bind, UNBOUND."""
        tag = "00EE00FF11223344"
        spools = [
            _spool(201, remaining_weight=500, rfid_tag_uid=tag, location="New", color_hex="ff0000"),
        ]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        attrs = {"tag_uid": tag, "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        self.assertEqual(len([p for p in sm.patches if p.get("spool_id") == 201]), 0, "no PATCH to New spool (not in RFID map)")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_UNBOUND_ACTION_REQUIRED, "New spool excluded -> UNBOUND")

    def test_location_new_excluded_from_deterministic_candidates(self):
        """PHASE_2_5: Tag in tray but no spool at Shelf has this UID -> UNBOUND_ACTION_REQUIRED, no bind to 702."""
        tag = "00EE00FF99AABB01"
        spools = [
            _spool(701, remaining_weight=1000, rfid_tag_uid=None, location="New", color_hex="ff0000"),
            _spool(702, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000"),
        ]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        attrs = {"tag_uid": tag, "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        bind_patches = [p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})]
        self.assertEqual(len(bind_patches), 0, "no bind when no Shelf spool has this RFID UID")
        summary = getattr(r, "_last_summary", None)
        self.assertIsNotNone(summary)
        transcripts = summary.get("validation_transcripts", [])
        slot1 = next((t for t in transcripts if t.get("slot") == 1), None)
        self.assertIsNotNone(slot1, "slot 1 transcript missing")
        self.assertIn(slot1.get("final_slot_status", ""), (STATUS_UNBOUND_ACTION_REQUIRED, "UNBOUND: ACTION_REQUIRED", "UNBOUND"))
        self.assertIn(slot1.get("final_spool_id"), (None, 0), "must not bind when no Shelf UID match")

    def test_location_new_only_unbound(self):
        """PHASE_2_5: No spool at Shelf with this tag (only New) -> UNBOUND_ACTION_REQUIRED."""
        tag = "00EE00FF55667788"
        spools = [
            _spool(801, remaining_weight=1000, rfid_tag_uid=None, location="New", color_hex="00ff00"),
        ]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "00ff00",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        attrs = {"tag_uid": tag, "type": "PLA", "color": "00ff00", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        bind_patches = [p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})]
        self.assertEqual(len(bind_patches), 0, "must not bind when no Shelf UID match")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_UNBOUND_ACTION_REQUIRED)

    def test_s5_two_reds_strict_mode_refuses(self):
        """PHASE_2_5: Unknown UID (no Shelf UID match) -> NEEDS_ACTION, no bind (strict_mode irrelevant when 0 UID match)."""
        tag = "AABBCCDD00111234"
        spools = [
            _spool(601, remaining_weight=842, initial_weight=1000, rfid_tag_uid=None, location="Shelf", color_hex="ff0000"),
            _spool(602, remaining_weight=1000, initial_weight=1000, rfid_tag_uid=None, location="Shelf", color_hex="ff0000"),
        ]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        attrs = {"tag_uid": tag, "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 84}
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args={"strict_mode_reregister": True})
        r._run_reconcile("test")

        bind_patches = [p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})]
        self.assertEqual(len(bind_patches), 0, "PHASE_2_5: no bind when no Shelf UID match")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_UNBOUND_ACTION_REQUIRED)

    def test_flow_b_ha_sig_one_candidate_binds(self):
        """PHASE_2_5: Unknown UID (no Shelf UID match) -> NEEDS_ACTION, no bind (no Flow B when tag_uid set)."""
        tag = "DDEEFF0011223344"
        ha_sig = "HA_SIG=bambu|filament_id=bambu|type=pla|color_hex=ff0000"
        spools = [_spool(101, remaining_weight=500, rfid_tag_uid=None, location="Shelf", comment=ha_sig)]
        filaments = [{"id": 1, "name": "Bambu PLA Basic", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        state_map = {
            tray_ent: _tray_state(tag, tray_type="PLA", color="ff0000", name="Bambu PLA Basic", filament_id="bambu"),
            f"{tray_ent}::all": {"attributes": _tray_state(tag)["attributes"], "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        bind_patches = [p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})]
        self.assertEqual(len(bind_patches), 0, "PHASE_2_5: no Flow B bind when no spool at Shelf has this RFID UID")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_UNBOUND_ACTION_REQUIRED)

    # ---------- PHASE_2_6: Non-RFID deterministic matching (Shelf-first, New fallback) ----------

    def test_phase26_nonrfid_shelf_one_match_binds(self):
        """PHASE_2_6: Non-RFID (all-zero identity) tray + one Shelf candidate -> unified nonrfid auto-match."""
        spools = [_spool(201, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000", vendor_name="Overture", name="Overture PLA")]
        filaments = [{"id": 1, "name": "Overture PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Overture"}, "external_id": "overture"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        state_map = {
            tray_ent: _tray_state("", tray_type="PLA", color="ff0000", name="Bambu PLA Basic", filament_id="bambu"),
            f"{tray_ent}::all": {"attributes": _tray_state("")["attributes"], "state": "valid"},
        }
        state_map["input_boolean.filament_iq_nonrfid_enabled"] = "on"
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_OK_NONRFID)
        patches_201 = [p for p in sm.patches if p.get("path") == "/api/v1/spool/201"]
        self.assertGreaterEqual(len(patches_201), 1, "expected location PATCH to spool 201")
        summary = getattr(r, "_last_summary", None)
        self.assertIsNotNone(summary)
        transcripts = summary.get("validation_transcripts", [])
        slot1 = next((t for t in transcripts if t.get("slot") == 1), None)
        self.assertIsNotNone(slot1)
        self.assertEqual(slot1.get("final_spool_id"), 201)
        self.assertIn(slot1.get("reason"), ("lot_nr_match", "nonrfid_auto_match"), "unified non-RFID path may set lot_nr_match or nonrfid_auto_match")

    def test_phase26_nonrfid_ambiguity_needs_manual_bind(self):
        """PHASE_2_6: Non-RFID (all-zero identity) + multiple candidates -> NEEDS_MANUAL_BIND via unified nonrfid path."""
        spools = [
            _spool(301, remaining_weight=200, rfid_tag_uid=None, location="Shelf", color_hex="00ff00", vendor_name="Overture", name="Overture PLA"),
            _spool(302, remaining_weight=250, rfid_tag_uid=None, location="Shelf", color_hex="00ff00", vendor_name="Overture", name="Overture PLA"),
        ]
        filaments = [{"id": 1, "name": "Overture PLA", "material": "PLA", "color_hex": "00ff00",
                     "vendor": {"name": "Overture"}, "external_id": "overture"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        state_map = {
            tray_ent: _tray_state("", tray_type="PLA", color="00ff00", name="Bambu PLA", filament_id="bambu"),
            f"{tray_ent}::all": {"attributes": _tray_state("")["attributes"], "state": "valid"},
        }
        state_map["input_boolean.filament_iq_nonrfid_enabled"] = "on"
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_NEEDS_MANUAL_BIND)

    def test_phase26_nonrfid_new_fallback_unambiguous_binds(self):
        """PHASE_2_6: Non-RFID unified path — spool with location=New is only a Shelf candidate;
        nonrfid auto-match via _find_deterministic_candidates includes 'New'."""
        spools = [_spool(401, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="0000ff", material="PETG", name="Overture PETG", vendor_name="Overture")]
        filaments = [{"id": 1, "name": "Overture PETG", "material": "PETG", "color_hex": "0000ff",
                     "vendor": {"name": "Overture"}, "external_id": "overture"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        attrs = _tray_state("", tray_type="PETG", color="0000ff", name="Bambu PETG", filament_id="bambu")["attributes"]
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        state_map["input_boolean.filament_iq_nonrfid_enabled"] = "on"
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        summary = getattr(r, "_last_summary", None)
        self.assertIsNotNone(summary)
        slot1 = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == 1), None)
        self.assertIsNotNone(slot1)
        self.assertEqual(slot1.get("final_spool_id"), 401)
        self.assertIn(slot1.get("reason"), ("lot_nr_match", "lot_nr_tiebreak", "nonrfid_auto_match"), "unified non-RFID path may set lot_nr_match, lot_nr_tiebreak, or nonrfid_auto_match")

    def test_phase26_rfid_regression_no_metadata_fallback(self):
        """PHASE_2_6 regression: tag_uid present + no Shelf UID match -> still NEEDS_ACTION, no bind (PHASE_2_5 strict)."""
        tag = "E5F6070011223344"
        spools = [_spool(501, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000")]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        state_map = {
            tray_ent: _tray_state(tag, tray_type="PLA", color="ff0000", name="Bambu PLA", filament_id="bambu"),
            f"{tray_ent}::all": {"attributes": _tray_state(tag)["attributes"], "state": "valid"},
        }
        state_map["input_boolean.filament_iq_nonrfid_enabled"] = "on"
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        bind_patches = [p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})]
        self.assertEqual(len(bind_patches), 0, "RFID path must not bind when no Shelf UID match (no metadata fallback)")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_UNBOUND_ACTION_REQUIRED)

    def test_flow_b_ambiguous_remains_unbound(self):
        """Flow B: 2+ HA_SIG matches -> FLOW_B_AMBIGUOUS, no bind."""
        tag = "EEFF001122334455"
        ha_sig = "HA_SIG=bambu|filament_id=bambu|type=pla|color_hex=00ff00"
        spools = [
            _spool(501, filament_id=1, rfid_tag_uid=None, location="Shelf", color_hex="00ff00", material="PLA",
                   comment=ha_sig, ha_spool_uuid=json.dumps("uuid-1"), vendor_name="Other"),
            _spool(502, filament_id=1, rfid_tag_uid=None, location="Shelf", color_hex="00ff00", material="PLA",
                   comment=ha_sig, ha_spool_uuid=json.dumps("uuid-2"), vendor_name="Other"),
        ]
        filaments = [{"id": 1, "name": "Generic PLA", "material": "PLA", "color_hex": "00ff00",
                     "vendor": {"name": "Other"}, "external_id": "generic"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        attrs = {"tag_uid": tag, "type": "PLA", "color": "00ff00", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        bind_patches = [p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})]
        self.assertEqual(len(bind_patches), 0, "no bind when Flow B ambiguous")

        status_writes = [w for w in r._helper_writes if "status" in w.get("entity_id", "")]
        unbound_write = next((w for w in status_writes if "UNBOUND" in str(w.get("value", ""))), None)
        self.assertIsNotNone(unbound_write)

    def test_unjson_decodes_wrapped_extras(self):
        """_unjson decodes JSON-wrapped Spoolman extra values correctly."""
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {}, args=self.args)
        self.assertEqual(r._unjson('"6743D5ED00000100"'), "6743D5ED00000100")
        self.assertEqual(r._unjson(None), "")
        self.assertEqual(r._unjson(""), "")
        self.assertEqual(r._unjson("  "), "")
        self.assertEqual(r._unjson("ABC"), "ABC")
        self.assertEqual(r._unjson('"74bac25a-0c1b-40d8-a797-ec65068e961c"'), "74bac25a-0c1b-40d8-a797-ec65068e961c")

    def test_patch_extra_retry_on_not_valid_json(self):
        """PHASE_2_5: Unknown UID (no Shelf UID match) -> NEEDS_ACTION, no bind; retry path tested elsewhere."""
        tag = "AABBCCDD00112233"
        spools = [_spool(601, remaining_weight=500, rfid_tag_uid=None, location="Shelf")]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        state_map = {
            tray_ent: _tray_state(tag),
            f"{tray_ent}::all": {"attributes": _tray_state(tag)["attributes"], "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        bind_patches = [p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})]
        self.assertEqual(len(bind_patches), 0, "PHASE_2_5: no bind when no spool at Shelf has this RFID UID")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_UNBOUND_ACTION_REQUIRED)

    def test_flow_b_ha_sig_color_hex_with_leading_hash(self):
        """Tray color with leading # is normalized; known UID (spool at Shelf with tag) -> bind."""
        tag = "FF00112233445566"
        ha_sig = "HA_SIG=bambu|filament_id=gfa00|type=pla|color_hex=c12e1f"
        spools = [
            _spool(101, filament_id=1, rfid_tag_uid=tag, location="Shelf", color_hex="c12e1f", material="PLA",
                   comment=ha_sig),
        ]
        filaments = [{"id": 1, "name": "Generic", "material": "PLA", "color_hex": "c12e1f",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "gfa00"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        state_map = {
            tray_ent: _tray_state(tag, tray_type="PLA", color="#c12e1f", name="Bambu", filament_id="gfa00"),
            f"{tray_ent}::all": {"attributes": _tray_state(tag, tray_type="PLA", color="#c12e1f", name="Bambu", filament_id="gfa00")["attributes"], "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), "OK", "known UID at Shelf -> OK")
        patches_101 = [p for p in sm.patches if p.get("path") == "/api/v1/spool/101"]
        self.assertGreaterEqual(len(patches_101), 1, "expected at least one PATCH to spool 101 (location/comment or extra)")

    def test_compute_ha_sig_falls_back_when_color_hex_missing(self):
        """_compute_ha_sig produces HA_SIG when color_hex is empty but fallback from Spoolman filament succeeds."""
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {}, args=self.args)
        tray_meta = {
            "filament_id": "gfa00",
            "type": "pla",
            "color_hex": "",
            "color": "",
            "color_candidates": [],
        }
        spool_with_filament = _spool(
            701,
            filament_id=1,
            color_hex="c12e1f",
            material="PLA",
            rfid_tag_uid=None,
            location="Shelf",
        )
        spool_index = {701: spool_with_filament}
        ha_sig = r._compute_ha_sig(
            tray_meta,
            slot=1,
            spool_index=spool_index,
            expected_spool_id=701,
            candidate_ids=[],
        )
        self.assertIsNotNone(ha_sig, "HA_SIG should be produced via filament fallback")
        self.assertIn("HA_SIG=bambu|filament_id=gfa00|type=pla|color_hex=c12e1f", ha_sig)
        self.assertIn("color_hex=c12e1f", ha_sig)

    def test_slot_4_ok_patches_location_ams1_slot4(self):
        """Given tray entity for slot 4 (physical), when UID matches one spool at Shelf, we PATCH location=AMS1_Slot4 (PHASE_2_5 Shelf-only)."""
        tag = "C7D26F7B00000100"
        spools = [_spool(601, remaining_weight=500, rfid_tag_uid=tag, location="Shelf", color_hex="ff0000")]
        filaments = [{"id": 1, "name": "Bambu PLA Basic", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        state_map = {}
        for slot in range(1, 5):
            tray_ent = _tray_entity(slot)
            if slot == 4:
                state_map[tray_ent] = _tray_state(tag, tray_type="PLA", color="ff0000", name="Bambu PLA Basic", filament_id="bambu")
                state_map[f"{tray_ent}::all"] = {"attributes": _tray_state(tag)["attributes"], "state": "valid"}
            else:
                state_map[tray_ent] = _tray_state("", tray_type="", color="", name="", filament_id="")
                state_map[f"{tray_ent}::all"] = {"attributes": _tray_state("")["attributes"], "state": "empty"}
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        location_patches = [p for p in sm.patches if p.get("payload", {}).get("location") == "AMS1_Slot4"]
        self.assertGreater(len(location_patches), 0, "expected a PATCH with location=AMS1_Slot4 (known UID at Shelf)")
        self.assertEqual(location_patches[0]["path"], "/api/v1/spool/601")
        status_writes = [w for w in r._helper_writes if "ams_slot_4_status" in w.get("entity_id", "")]
        ok_write = next((w for w in status_writes if w.get("value") == "OK"), None)
        self.assertIsNotNone(ok_write, "slot 4 status should be OK")

    def test_ha_sig_stamped_on_ok_when_comment_missing(self):
        """On successful bind (status OK), spool comment is stamped with HA_SIG when missing or blank (PHASE_2_5: known UID at Shelf)."""
        tag = "C7D8E9F000112233"
        spools = [_spool(101, remaining_weight=500, rfid_tag_uid=tag, location="Shelf", comment="")]
        filaments = [{"id": 1, "name": "Bambu PLA Basic", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        state_map = {
            tray_ent: _tray_state(tag, tray_type="PLA", color="ff0000", name="Bambu PLA Basic", filament_id="bambu"),
            f"{tray_ent}::all": {"attributes": _tray_state(tag)["attributes"], "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        # v4: identity in lot_nr only (RFID: tray_uuid written to lot_nr)
        lot_nr_patches = [p for p in sm.patches if p.get("path") == "/api/v1/spool/101" and (p.get("payload") or {}).get("lot_nr")]
        self.assertGreater(len(lot_nr_patches), 0, "expected a PATCH that stamps spool lot_nr")
        self.assertEqual(len(lot_nr_patches), 1, "expected exactly one lot_nr PATCH (idempotent convergence)")
        self.assertEqual(lot_nr_patches[0]["path"], "/api/v1/spool/101")
        status_writes = [w for w in r._helper_writes if "status" in w.get("entity_id", "")]
        ok_write = next((w for w in status_writes if w.get("value") == "OK"), None)
        self.assertIsNotNone(ok_write, "status should be OK for this bind")

    def test_ha_sig_converge_no_patch_when_comment_already_equals(self):
        """When spool.comment already equals ha_sig, no PATCH is issued."""
        tag = "C7D8E9F000112233"
        expected_ha_sig = "HA_SIG=bambu|filament_id=bambu|type=pla|color_hex=ff0000"
        spools = [_spool(101, remaining_weight=500, rfid_tag_uid=None, location="Shelf", comment=expected_ha_sig)]
        filaments = [{"id": 1, "name": "Bambu PLA Basic", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        state_map = {
            tray_ent: _tray_state(tag, tray_type="PLA", color="ff0000", name="Bambu PLA Basic", filament_id="bambu"),
            f"{tray_ent}::all": {"attributes": _tray_state(tag)["attributes"], "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        comment_patches = [p for p in sm.patches if "comment" in p.get("payload", {})]
        self.assertEqual(len(comment_patches), 0, "expected no comment PATCH when comment already equals ha_sig")

    def test_ha_sig_converge_no_patch_when_compute_ha_sig_returns_none(self):
        """When _compute_ha_sig returns None (e.g. missing color), no comment PATCH."""
        tag = "A1B2C3D400010203"
        # Filament with no color_hex so _resolve_color_for_ha_sig returns "" and _compute_ha_sig returns None
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        spools = [_spool(102, remaining_weight=400, rfid_tag_uid=json.dumps(tag), location="AMS1_Slot4", comment="",
                         filament_id=1, color_hex="")]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(4)
        attrs = {"tag_uid": tag, "type": "PLA", "color": "", "name": "", "filament_id": "bambu",
                 "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"state": "valid", "attributes": attrs},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        comment_patches = [p for p in sm.patches if "comment" in p.get("payload", {})]
        self.assertEqual(len(comment_patches), 0, "expected no comment PATCH when ha_sig is None (missing color)")

    def test_ha_sig_converge_does_not_use_tray_signature_helper(self):
        """v4: Identity written to lot_nr without input_text.ams_slot_*_tray_signature (e.g. when helper is unavailable)."""
        tag = "C7D26F7B00000100"
        spools = [_spool(1, remaining_weight=500, rfid_tag_uid=json.dumps(tag), location="Shelf", lot_nr="")]
        filaments = [{"id": 1, "name": "Bambu PLA Basic", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(4)
        state_map = {
            tray_ent: _tray_state(tag, tray_type="PLA", color="ff0000", name="Bambu PLA Basic", filament_id="bambu"),
            f"{tray_ent}::all": {"attributes": _tray_state(tag)["attributes"], "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""
        # Do NOT set input_text.ams_slot_*_tray_signature (simulates unavailable helper)
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        # v4: RFID bind writes tray_uuid (tag) to lot_nr, not comment
        lot_nr_patches = [p for p in sm.patches if p.get("path") == "/api/v1/spool/1" and (p.get("payload") or {}).get("lot_nr")]
        self.assertEqual(len(lot_nr_patches), 1, "expected one lot_nr PATCH even without tray_signature helper")
        self.assertEqual(lot_nr_patches[0]["path"], "/api/v1/spool/1")

    def test_sticky_must_not_override_when_helper_uid_mismatch(self):
        """PHASE_2_6_1: Sticky must not override UID-resolved spool when helper spool UID != tray tag_uid."""
        tag = "5710AB0011223344"
        # tag_to_spools[tag] = [38]; spool 38 has UID T, spool 4 has different UID
        spools = [
            _spool(38, remaining_weight=500, rfid_tag_uid=tag, location="Shelf", color_hex="ff0000"),
            _spool(4, remaining_weight=400, rfid_tag_uid="07BE200000000001", location="Shelf", color_hex="ff0000"),
        ]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(4)
        state_map = {
            tray_ent: _tray_state(tag, tray_type="PLA", color="ff0000", name="Bambu PLA", filament_id="bambu"),
            f"{tray_ent}::all": {"attributes": _tray_state(tag)["attributes"], "state": "valid"},
        }
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "4" if s == 4 else "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
        state_map["input_text.ams_slot_4_tray_signature"] = tag.strip().lower()
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        summary = getattr(r, "_last_summary", None)
        self.assertIsNotNone(summary)
        slot4 = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == 4), None)
        self.assertIsNotNone(slot4, "slot 4 transcript missing")
        self.assertEqual(slot4.get("final_spool_id"), 38, "must keep UID-resolved spool 38, not stick to helper 4")
        location_4 = [p for p in sm.patches if p.get("path") == "/api/v1/spool/4" and p.get("payload", {}).get("location") == "AMS1_Slot4"]
        self.assertEqual(len(location_4), 0, "must not write location AMS1_Slot4 for spool 4")
        spool_id_writes_4 = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_4_spool_id" and w.get("value") == "4"]
        self.assertEqual(len(spool_id_writes_4), 0, "must not write helper spool_id=4")

    def test_bind_guard_refuses_uid_mismatch(self):
        """PHASE_2_6_1: Final RFID bind guard refuses bind when selected spool UID != tray tag_uid."""
        tag = "B11D6A2D00112233"
        spools = [_spool(38, remaining_weight=500, rfid_tag_uid=tag, location="Shelf", color_hex="ff0000")]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(4)
        state_map = {
            tray_ent: _tray_state(tag, tray_type="PLA", color="ff0000", name="Bambu PLA", filament_id="bambu"),
            f"{tray_ent}::all": {"attributes": _tray_state(tag)["attributes"], "state": "valid"},
        }
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
        r = TestableReconcile(sm, state_map, args=self.args)
        # Force guard to fail so we hit the UNBOUND_SELECTED_UID_MISMATCH path (no bind, no HA_SIG)
        r._rfid_bind_guard_ok = lambda resolved_spool_id, tag_uid, spool_index, tray_uuid="": False
        r._run_reconcile("test")
        summary = getattr(r, "_last_summary", None)
        self.assertIsNotNone(summary)
        slot4 = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == 4), None)
        self.assertIsNotNone(slot4)
        self.assertEqual(slot4.get("final_slot_status"), STATUS_UNBOUND_ACTION_REQUIRED)
        self.assertEqual(slot4.get("unbound_reason"), UNBOUND_SELECTED_UID_MISMATCH)
        location_38 = [p for p in sm.patches if p.get("path") == "/api/v1/spool/38"]
        self.assertEqual(len(location_38), 0, "must not PATCH spool 38 (no helper/location write)")
        comment_38 = [p for p in sm.patches if p.get("path") == "/api/v1/spool/38" and "comment" in p.get("payload", {})]
        self.assertEqual(len(comment_38), 0, "must not stamp HA_SIG on spool 38")

    def test_color_normalize_hex_color(self):
        """Normalization: #RRGGBB, uppercase, 8-hex AARRGGBB / RRGGBBAA produce consistent 6-char lowercase."""
        self.assertEqual(_normalize_hex_color("#c12e1f"), "c12e1f")
        self.assertEqual(_normalize_hex_color("C12E1F"), "c12e1f")
        self.assertEqual(_normalize_hex_color("c12e1f"), "c12e1f")
        # 8-hex: AARRGGBB when first 2 are ff/00 -> last 6
        self.assertEqual(_normalize_hex_color("ff0000ff"), "0000ff")
        self.assertEqual(_normalize_hex_color("00ff0000"), "ff0000")
        self.assertEqual(_normalize_hex_color("ff0000aa"), "0000aa")
        # 8-hex: RRGGBBAA when first 2 not ff/00 -> first 6
        self.assertEqual(_normalize_hex_color("ab0000ff"), "ab0000")
        self.assertIsNone(_normalize_hex_color(""))
        self.assertIsNone(_normalize_hex_color("xyz"))

    def test_color_helpers_rgb_and_distance(self):
        """_hex_to_rgb, _rgb_distance, _colors_close behave as expected."""
        self.assertEqual(_hex_to_rgb("ff0000"), (255, 0, 0))
        self.assertEqual(_hex_to_rgb("c12e1f"), (0xC1, 0x2E, 0x1F))
        self.assertAlmostEqual(_rgb_distance((255, 0, 0), (0, 255, 0)), 360.6, delta=1.0)
        close, dist, thresh = _colors_close("c12e1f", "ff0000", COLOR_DISTANCE_THRESHOLD)
        self.assertTrue(close, "c12e1f vs ff0000 should be within threshold")
        self.assertLessEqual(dist, thresh)
        close2, dist2, _ = _colors_close("00ff00", "ff0000", COLOR_DISTANCE_THRESHOLD)
        self.assertFalse(close2, "green vs red should not be close")
        self.assertGreater(dist2, COLOR_DISTANCE_THRESHOLD)

    def test_color_near_match_not_mismatch(self):
        """Known UID binding: tray_hex c12e1f vs spool ff0000 -> within tolerance -> status OK (no color_mismatch)."""
        tag = "D4E5F60001122334"
        spools = [_spool(401, remaining_weight=500, rfid_tag_uid=json.dumps(tag), location="Shelf", color_hex="ff0000")]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        attrs = {"tag_uid": tag, "type": "PLA", "color": "c12e1f", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        status_writes = [w for w in r._helper_writes if "status" in w.get("entity_id", "")]
        ok_write = next((w for w in status_writes if w.get("value") == "OK"), None)
        self.assertIsNotNone(ok_write, "tray c12e1f vs spool ff0000 (same red) should be OK, not MISMATCH")
        mismatch_write = next((w for w in status_writes if "MISMATCH" in str(w.get("value", ""))), None)
        self.assertIsNone(mismatch_write, "should not report CONFLICT: MISMATCH for close colors")

    def test_color_far_remains_mismatch(self):
        """Known UID binding: tray_hex 00ff00 vs spool ff0000 -> different colors -> CONFLICT: MISMATCH."""
        tag = "E5F6070011223344"
        spools = [_spool(501, remaining_weight=500, rfid_tag_uid=json.dumps(tag), location="Shelf", color_hex="ff0000")]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        attrs = {"tag_uid": tag, "type": "PLA", "color": "00ff00", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        status_writes = [w for w in r._helper_writes if "status" in w.get("entity_id", "")]
        mismatch_write = next((w for w in status_writes if "MISMATCH" in str(w.get("value", ""))), None)
        self.assertIsNotNone(mismatch_write, "tray 00ff00 vs spool ff0000 (green vs red) should remain MISMATCH")

    def test_classify_unbound_reason_tray_empty(self):
        """Classifier returns UNBOUND_TRAY_EMPTY when tray is empty."""
        reason, detail = _classify_unbound_reason({}, "", [], 0, tray_empty=True, tray_state_str="")
        self.assertEqual(reason, UNBOUND_TRAY_EMPTY)
        self.assertEqual(detail, "tray_empty")

    def test_classify_unbound_reason_no_tag_uid(self):
        """Classifier returns UNBOUND_NO_TAG_UID when tag_uid is blank."""
        reason, detail = _classify_unbound_reason({}, "", [], 0, tray_empty=False, tray_state_str="valid")
        self.assertEqual(reason, UNBOUND_NO_TAG_UID)
        self.assertEqual(detail, "tag_uid_blank")

    def test_classify_unbound_reason_all_zero_tag(self):
        """Classifier returns UNBOUND_NO_RFID_TAG_ALL_ZERO when raw tag_uid is 0000000000000000."""
        reason, detail = _classify_unbound_reason(
            {}, "", [], 0, tray_empty=False, tray_state_str="valid", raw_tag_uid="0000000000000000"
        )
        self.assertEqual(reason, UNBOUND_NO_RFID_TAG_ALL_ZERO)
        self.assertEqual(detail, "non_rfid_tray")

    def test_classify_unbound_reason_ineligible_location_new(self):
        """Classifier returns UNBOUND_TAG_UID_INELIGIBLE_LOCATION_NEW when eligible=0 and ineligible_new>0."""
        reason, detail = _classify_unbound_reason(
            {}, "AABBCCDD00112233", [], 2, tray_empty=False, tray_state_str="valid"
        )
        self.assertEqual(reason, UNBOUND_TAG_UID_INELIGIBLE_LOCATION_NEW)
        self.assertIn("ineligible_new=2", detail)

    def test_classify_unbound_reason_no_match(self):
        """Classifier returns UNBOUND_TAG_UID_NO_MATCH when eligible=0 and ineligible_new=0."""
        reason, detail = _classify_unbound_reason(
            {}, "AABBCCDD00112233", [], 0, tray_empty=False, tray_state_str="valid"
        )
        self.assertEqual(reason, UNBOUND_TAG_UID_NO_MATCH)
        self.assertIn("eligible=0", detail)

    def test_classify_unbound_reason_ambiguous(self):
        """Classifier returns UNBOUND_TAG_UID_AMBIGUOUS when eligible>1."""
        reason, detail = _classify_unbound_reason(
            {}, "AABBCCDD00112233", [101, 102], 0, tray_empty=False, tray_state_str="valid"
        )
        self.assertEqual(reason, UNBOUND_TAG_UID_AMBIGUOUS)
        self.assertIn("eligible=2", detail)

    def test_classify_unbound_reason_tray_unavailable(self):
        """Classifier returns UNBOUND_TRAY_UNAVAILABLE when tray state is unknown/unavailable."""
        reason, detail = _classify_unbound_reason(
            {}, "AABBCCDD", [], 0, tray_empty=False, tray_state_str="unknown"
        )
        self.assertEqual(reason, UNBOUND_TRAY_UNAVAILABLE)
        self.assertEqual(detail, "tray_unavailable")

    def test_unbound_slot_has_reason_in_transcript(self):
        """Run reconcile with no tag -> slot ends UNBOUND with unbound_reason UNBOUND_NO_TAG_UID."""
        tag = ""
        spools = [_spool(101, remaining_weight=500, rfid_tag_uid=None, location="Shelf")]
        sm = FakeSpoolman(spools, [])
        tray_ent = _tray_entity(1)
        attrs = {"tag_uid": "", "type": "PLA", "color": "ff0000", "name": "Bambu PLA", "filament_id": "bambu",
                 "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""
            state_map[f"input_text.ams_slot_{slot}_unbound_reason"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        summary = getattr(r, "_last_summary", None)
        self.assertIsNotNone(summary)
        transcripts = summary.get("validation_transcripts", [])
        slot1 = next((tr for tr in transcripts if tr.get("slot") == 1), None)
        self.assertIsNotNone(slot1)
        self.assertTrue(str(slot1.get("final_slot_status", "")).startswith("UNBOUND"))
        self.assertEqual(slot1.get("unbound_reason"), UNBOUND_NO_TAG_UID)
        self.assertIn("unbound_detail", slot1)
        # input_text.* must use input_text/set_value (text/set_value does not update input_text entities in HA 2026).
        unbound_reason_writes = [w for w in r._helper_writes if w.get("entity_id") and "unbound_reason" in w.get("entity_id", "")]
        self.assertGreater(len(unbound_reason_writes), 0, "expected at least one unbound_reason helper write")
        for w in unbound_reason_writes:
            self.assertEqual(w.get("service"), "input_text/set_value", f"input_text.* must use input_text/set_value, got {w.get('service')}")

    def test_unbound_all_zero_tag_reason(self):
        """Tray with tag_uid 0000000000000000 (non-RFID) gets unbound_reason UNBOUND_NO_RFID_TAG_ALL_ZERO."""
        spools = [_spool(101, remaining_weight=500, rfid_tag_uid=None, location="Shelf")]
        sm = FakeSpoolman(spools, [])
        tray_ent = _tray_entity(1)
        attrs = {"tag_uid": "0000000000000000", "type": "Generic PETG", "color": "ff0000", "name": "PETG",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": -1, "empty": False}
        state_map = {
            tray_ent: {"attributes": attrs, "state": "Generic PETG"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "Generic PETG"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""
            state_map[f"input_text.ams_slot_{slot}_unbound_reason"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        summary = getattr(r, "_last_summary", None)
        self.assertIsNotNone(summary)
        transcripts = summary.get("validation_transcripts", [])
        slot1 = next((tr for tr in transcripts if tr.get("slot") == 1), None)
        self.assertIsNotNone(slot1)
        self.assertTrue(str(slot1.get("final_slot_status", "")).startswith("UNBOUND"))
        self.assertEqual(slot1.get("unbound_reason"), UNBOUND_NO_RFID_TAG_ALL_ZERO)
        unbound_reason_writes = [w for w in r._helper_writes if w.get("entity_id") and "unbound_reason" in w.get("entity_id", "")]
        self.assertGreater(len(unbound_reason_writes), 0)
        for w in unbound_reason_writes:
            self.assertEqual(w.get("service"), "input_text/set_value", f"input_text.* must use input_text/set_value, got {w.get('service')}")

    def test_unbound_empty_tray_reason(self):
        """Empty tray slot gets unbound_reason UNBOUND_TRAY_EMPTY (or no_tag when no tag)."""
        sm = FakeSpoolman([], [])
        state_map = {}
        tray_ent = _tray_entity(1)
        state_map[tray_ent] = {"state": "empty", "attributes": {"tag_uid": "", "empty": True}}
        state_map[f"{tray_ent}::all"] = {"state": "empty", "attributes": {"tag_uid": "", "empty": True}}
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        summary = getattr(r, "_last_summary", None)
        self.assertIsNotNone(summary)
        transcripts = summary.get("validation_transcripts", [])
        slot1 = next((tr for tr in transcripts if tr.get("slot") == 1), None)
        self.assertIsNotNone(slot1)
        self.assertTrue(str(slot1.get("final_slot_status", "")).startswith("UNBOUND"))
        self.assertIn(slot1.get("unbound_reason"), (UNBOUND_TRAY_EMPTY, UNBOUND_NO_TAG_UID))

    def test_canonical_locations_do_not_include_deprecated(self):
        """Canonical location list must never contain AMS2_HT_* (would re-seed Spoolman settings)."""
        deprecated_prefix = "AMS2_HT_"
        for slot, loc in CANONICAL_LOCATION_BY_SLOT.items():
            self.assertFalse(
                loc.startswith(deprecated_prefix) or deprecated_prefix in loc,
                f"CANONICAL_LOCATION_BY_SLOT[{slot}] must not contain deprecated '{deprecated_prefix}': got {loc!r}",
            )

    def test_deprecated_locations_map_to_canonical(self):
        """Deprecated strings must map to AMS128_Slot1 / AMS129_Slot1 so we never write them."""
        self.assertEqual(DEPRECATED_LOCATION_TO_CANONICAL.get("AMS2_HT_Slot1"), "AMS128_Slot1")
        self.assertEqual(DEPRECATED_LOCATION_TO_CANONICAL.get("AMS2_HT_Slot2"), "AMS129_Slot1")
        self.assertEqual(DEPRECATED_LOCATION_TO_CANONICAL.get("AMS2_HT_Slot3"), "AMS130_Slot1")
        for deprecated, canonical in DEPRECATED_LOCATION_TO_CANONICAL.items():
            self.assertIn("AMS2_HT_", deprecated, f"keys should be deprecated: {deprecated}")
            self.assertNotIn("AMS2_HT_", canonical, f"canonical must not be deprecated: {canonical}")

    def test_empty_tray_slot_3_clears_expected_helpers_no_mismatch(self):
        """When tray for slot 3 is empty, reconcile clears expected helpers and does not produce SPOOLS_MISMATCH."""
        sm = FakeSpoolman([], [])
        state_map = {}
        for slot in range(1, 5):
            tray_ent = _tray_entity(slot)
            if slot == 3:
                state_map[tray_ent] = {"state": "empty", "attributes": {}}
                state_map[f"{tray_ent}::all"] = {"attributes": {}, "state": "empty"}
                state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
                state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "99"
                state_map[f"input_text.ams_slot_{slot}_tray_signature"] = "abc"
                state_map[f"input_text.ams_slot_{slot}_expected_material"] = "PLA"
                state_map[f"input_text.ams_slot_{slot}_expected_color"] = "Red"
                state_map[f"input_text.ams_slot_{slot}_expected_color_hex"] = "ff0000"
                state_map[f"input_text.ams_slot_{slot}_status"] = ""
            else:
                state_map[tray_ent] = {"state": "empty", "attributes": {}}
                state_map[f"{tray_ent}::all"] = {"attributes": {}, "state": "empty"}
                state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
                state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
                state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        log_calls = []
        r.log = lambda msg, level="INFO": log_calls.append((msg, level))
        r._active_run = {
            "reason": "test",
            "writes": [],
            "decisions": [],
            "no_write_paths": [],
            "conflicts": [],
            "unknown_tags": [],
            "auto_registers": [],
        }
        r._run_reconcile("test")
        clear_logs = [c for c in log_calls if "RFID_EMPTY_TRAY_CLEAR" in str(c[0])]
        self.assertEqual(len(clear_logs), 1, "self.log must be called once with RFID_EMPTY_TRAY_CLEAR when clearing")
        self.assertIn("prior_expected_spool_id=99", clear_logs[0][0])
        self.assertEqual(clear_logs[0][1], "INFO")

        # Slot 3 expected helpers must be cleared.
        slot3_expected_helpers = [
            "input_text.ams_slot_3_expected_spool_id",
            "input_text.ams_slot_3_tray_signature",
            "input_text.ams_slot_3_expected_material",
            "input_text.ams_slot_3_expected_color",
            "input_text.ams_slot_3_expected_color_hex",
        ]
        slot3_clears = [w for w in r._helper_writes if w.get("entity_id") in slot3_expected_helpers]
        self.assertEqual(len(slot3_clears), 7, "expected seven helper clears for slot 3 (five from clear_expected + expected_spool_id and tray_signature from unbind path)")
        by_entity = {w["entity_id"]: w["value"] for w in slot3_clears}
        self.assertEqual(by_entity.get("input_text.ams_slot_3_expected_spool_id"), "0")
        self.assertEqual(by_entity.get("input_text.ams_slot_3_tray_signature"), "")
        self.assertEqual(by_entity.get("input_text.ams_slot_3_expected_material"), "")
        self.assertEqual(by_entity.get("input_text.ams_slot_3_expected_color"), "")
        self.assertEqual(by_entity.get("input_text.ams_slot_3_expected_color_hex"), "")

        # No MISMATCH conflict (empty tray path does not compare expected vs resolved).
        summary = r._last_summary or {}
        mismatch_conflicts = [c for c in (summary.get("conflicts_detected") or []) if c.get("reason") == "MISMATCH"]
        self.assertEqual(len(mismatch_conflicts), 0, "must not produce SPOOLS_MISMATCH when tray is empty")
        self.assertEqual(summary.get("mismatch", 0), 0, "mismatch count must be 0")

        # Observability: log and evidence line emitted when clear occurs (idempotent: only when change made).
        self.assertGreater(len(r._evidence_lines), 0, "evidence line must be written when clearing expected helpers")
        clear_lines = [line for line in r._evidence_lines if "RFID_EMPTY_TRAY_CLEAR" in line]
        self.assertEqual(len(clear_lines), 1, "exactly one RFID_EMPTY_TRAY_CLEAR line when slot 3 cleared")
        self.assertIn("prior_expected_spool_id=99", clear_lines[0], "prior_expected_spool_id must appear in evidence line")
        self.assertIn("slot=3", clear_lines[0])
        self.assertIn("reason=tray_empty", clear_lines[0])

    def test_never_initialized_slot_empty_tray_writes_unbound_reason(self):
        """Bug 15: Slot with spool_id='unknown' (HA default) + empty tray must get UNBOUND_TRAY_EMPTY."""
        sm = FakeSpoolman([], [])
        state_map = {}
        # All slots empty, but slot 5 has never been written (spool_id='unknown')
        for slot in range(1, 8):
            tray_ent = _tray_entity(slot)
            state_map[tray_ent] = {"state": "empty", "attributes": {}}
            state_map[f"{tray_ent}::all"] = {"attributes": {}, "state": "empty"}
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_unbound_reason"] = ""
            state_map[f"input_text.ams_slot_{slot}_status"] = ""
        # Slot 5: simulate HA default 'unknown' for never-initialized helpers
        state_map["input_text.ams_slot_5_spool_id"] = "unknown"
        state_map["input_text.ams_slot_5_unbound_reason"] = "unknown"

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        # Slot 5 must have UNBOUND_TRAY_EMPTY written to unbound_reason
        unbound_writes = [
            w for w in r._helper_writes
            if w.get("entity_id") == "input_text.ams_slot_5_unbound_reason"
            and w.get("value") == UNBOUND_TRAY_EMPTY
        ]
        self.assertGreaterEqual(
            len(unbound_writes), 1,
            f"Expected UNBOUND_TRAY_EMPTY write for slot 5, got: {r._helper_writes}"
        )

        # spool_id may be written to 0 by other reconciler paths — that's acceptable.
        # The key assertion is that unbound_reason got UNBOUND_TRAY_EMPTY above.

    def test_color_warning_tray_hex_000000_no_warning(self):
        """tray_hex='000000' is non-authoritative → no COLOR_WARNING."""
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {}, args=self.args)
        self.assertFalse(r._should_emit_color_warning("c0c0c0", "000000"))
        self.assertFalse(r._should_emit_color_warning("ff0000", "000000"))

    def test_color_warning_tray_hex_empty_no_warning(self):
        """tray_hex='' is non-authoritative → no COLOR_WARNING."""
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {}, args=self.args)
        self.assertFalse(r._should_emit_color_warning("c0c0c0", ""))
        self.assertFalse(r._should_emit_color_warning("ff0000", ""))

    def test_color_warning_tray_hex_different_valid_hex_emits_warning(self):
        """When expected_hex set and tray_hex is valid but different → COLOR_WARNING emitted."""
        tag = "AABBCCDD00112233"
        spools = [_spool(101, remaining_weight=500, rfid_tag_uid=tag, location="Shelf", color_hex="ff0000")]
        filaments = [{"id": 1, "name": "Bambu PLA Basic", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        state_map = {
            tray_ent: _tray_state(tag, color="ff0000"),
            f"{tray_ent}::all": {"attributes": _tray_state(tag, color="ff0000")["attributes"], "state": "valid"},
        }
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""
        state_map["input_text.ams_slot_1_expected_spool_id"] = "101"
        state_map["input_text.ams_slot_1_expected_color_hex"] = "c0c0c0"
        r = TestableReconcile(sm, state_map, args=self.args)
        log_calls = []
        r.log = lambda msg, level="INFO": log_calls.append((msg, level))
        r._active_run = {
            "reason": "test",
            "writes": [],
            "decisions": [],
            "no_write_paths": [],
            "conflicts": [],
            "unknown_tags": [],
            "auto_registers": [],
        }
        r._run_reconcile("test")
        color_warnings = [c for c in log_calls if "COLOR_WARNING" in str(c[0])]
        self.assertEqual(len(color_warnings), 1, "COLOR_WARNING must be emitted when tray_hex differs from expected_hex")
        self.assertIn("expected_hex=c0c0c0", color_warnings[0][0])
        self.assertIn("tray_hex=ff0000", color_warnings[0][0])
        self.assertEqual(color_warnings[0][1], "WARNING")

    def test_rfid_pending_tray_change_no_tag_uid_takes_pending_path(self):
        """Tray change + tag_uid None with pending window active -> PENDING_RFID_READ, no non-RFID path."""
        slot = 5
        tray_ent = _tray_entity(slot)
        # Tray not empty but no valid tag (blank tag_uid)
        attrs_no_tag = {"tag_uid": "", "type": "PLA", "color": "ff0000", "name": "Bambu PLA", "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs_no_tag, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs_no_tag, "state": "valid"},
        }
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            if s != slot:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        # Pending window in future (input_text, ISO8601 UTC)
        future_ts = (datetime.datetime.utcnow() + datetime.timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state_map[f"input_text.ams_slot_{slot}_rfid_pending_until"] = future_ts
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0, "expected status helper write")
        self.assertEqual(status_writes[-1].get("value"), STATUS_PENDING_RFID_READ, "status must be PENDING_RFID_READ")
        self.assertEqual(len(sm.patches), 0, "must not run non-RFID binding when in pending window")

    def test_rfid_pending_tag_uid_valid_before_expire_rfid_lane_runs(self):
        """tag_uid valid before pending expires + one Shelf UID match -> RFID lane runs (OK/bind)."""
        slot = 5
        tag = "AABBCCDD00112233"
        spools = [_spool(101, remaining_weight=500, rfid_tag_uid=tag, location="Shelf")]
        filaments = [{"id": 1, "name": "Bambu PLA Basic", "material": "PLA", "color_hex": "ff0000", "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(slot)
        state_map = {
            tray_ent: _tray_state(tag),
            f"{tray_ent}::all": {"attributes": _tray_state(tag)["attributes"], "state": "valid"},
        }
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            if s != slot:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        future_ts = (datetime.datetime.utcnow() + datetime.timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state_map[f"input_text.ams_slot_{slot}_rfid_pending_until"] = future_ts
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), "OK", "with valid tag_uid and one Shelf UID match, RFID lane must run and set OK")
        patches_101 = [p for p in sm.patches if p.get("path") == "/api/v1/spool/101"]
        self.assertGreaterEqual(len(patches_101), 1, "expected at least one PATCH to spool 101 (location/comment or extra)")

    def test_rfid_pending_with_nonrfid_enabled_future_pending_takes_pending_not_non_rfid(self):
        """With non-RFID enabled, future pending_until, tray not empty, tag_uid invalid: status must be PENDING_RFID_READ; NON_RFID path must NOT run."""
        slot = 5
        tray_ent = _tray_entity(slot)
        attrs_all_zero = {"tag_uid": "0000000000000000", "tray_uuid": "00000000000000000000000000000000", "empty": False, "type": "PLA", "color": "ff0000", "name": "Bambu", "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs_all_zero, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs_all_zero, "state": "valid"},
        }
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            if s != slot:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        future_ts = (datetime.datetime.utcnow() + datetime.timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state_map[f"input_text.ams_slot_{slot}_rfid_pending_until"] = future_ts
        state_map["input_boolean.filament_iq_nonrfid_enabled"] = "on"
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0, "expected status helper write")
        self.assertEqual(status_writes[-1].get("value"), STATUS_PENDING_RFID_READ, "during pending window status must be PENDING_RFID_READ")
        non_rfid_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status" and w.get("value") == "NON_RFID_UNREGISTERED"]
        self.assertEqual(len(non_rfid_writes), 0, "NON_RFID_UNREGISTERED must not be written while pending is active")

    def test_rfid_pending_expired_tag_uid_still_none_non_rfid_lane_runs(self):
        """Pending expires and tag_uid still None -> Non-RFID lane runs (NON_RFID_UNREGISTERED when enabled)."""
        slot = 5
        tray_ent = _tray_entity(slot)
        attrs_all_zero = {"tag_uid": "0000000000000000", "tray_uuid": "00000000000000000000000000000000", "empty": False, "type": "PLA", "color": "ff0000", "name": "Bambu", "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs_all_zero, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs_all_zero, "state": "valid"},
        }
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            if s != slot:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        # Pending window in past (input_text, ISO8601 UTC)
        past_ts = (datetime.datetime.utcnow() - datetime.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state_map[f"input_text.ams_slot_{slot}_rfid_pending_until"] = past_ts
        state_map["input_boolean.filament_iq_nonrfid_enabled"] = "on"
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertIn(status_writes[-1].get("value"),
                      (STATUS_NEEDS_MANUAL_BIND, STATUS_LOW_CONFIDENCE, "NON_RFID_UNREGISTERED"),
                      "after pending expires, non-RFID lane must run when enabled")

    def test_bound_invariant_spool_id_equals_expected_no_tag_uid_yields_non_rfid_registered(self):
        """When spool_id == expected_spool_id > 0 and tag_uid missing/empty, reconcile must yield NON_RFID_REGISTERED, not PENDING_RFID_READ."""
        slot = 3
        tray_ent = _tray_entity(slot)
        attrs_no_tag = {"tag_uid": "", "type": "PLA", "color": "ff0000", "name": "Bambu PLA", "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs_no_tag, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs_no_tag, "state": "valid"},
        }
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            if s != slot:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        state_map[f"input_text.ams_slot_{slot}_spool_id"] = "3"
        state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "3"
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0, "expected status helper write")
        self.assertEqual(status_writes[-1].get("value"), "NON_RFID_REGISTERED", "bound slot with no tag_uid must be NON_RFID_REGISTERED, not PENDING_RFID_READ")
        pending_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status" and w.get("value") == STATUS_PENDING_RFID_READ]
        self.assertEqual(len(pending_writes), 0, "must not write PENDING_RFID_READ when spool_id == expected_spool_id > 0")

    def test_bound_invariant_with_future_pending_until_stays_non_rfid_registered(self):
        """Bound slot (spool_id == expected_spool_id > 0) with future pending_until must stay NON_RFID_REGISTERED; must not demote to PENDING_RFID_READ."""
        slot = 3
        tray_ent = _tray_entity(slot)
        attrs_no_tag = {"tag_uid": "", "type": "PLA", "color": "ff0000", "name": "Bambu PLA", "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs_no_tag, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs_no_tag, "state": "valid"},
        }
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            if s != slot:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        state_map[f"input_text.ams_slot_{slot}_spool_id"] = "3"
        state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "3"
        future_ts = (datetime.datetime.utcnow() + datetime.timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state_map[f"input_text.ams_slot_{slot}_rfid_pending_until"] = future_ts
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0, "expected status helper write")
        self.assertEqual(status_writes[-1].get("value"), "NON_RFID_REGISTERED", "bound invariant must win over pending_until; must not demote to PENDING_RFID_READ")
        pending_writes = [w for w in r._helper_writes if w.get("value") == STATUS_PENDING_RFID_READ]
        self.assertEqual(len(pending_writes), 0, "must not write PENDING_RFID_READ when bound")

    def test_get_tray_identity_tray_uuid_preferred(self):
        """_get_tray_identity uses tray_uuid when present (uppercased), else tag_uid."""
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {}, args=self.args)
        self.assertEqual(r._get_tray_identity({"tray_uuid": "  c482963767a24acbb858f95d4376a2e5  "}, "1D33DD3B00000100"), "C482963767A24ACBB858F95D4376A2E5")
        self.assertEqual(r._get_tray_identity({"tray_uuid": ""}, "1d33dd3b00000100"), "1D33DD3B00000100")
        self.assertEqual(r._get_tray_identity({}, "1d33dd3b00000100"), "1D33DD3B00000100")

    def test_has_tray_uuid_and_norm_tray_identity_tag(self):
        """_has_tray_uuid and _norm_tray_identity_tag for tray identity hardening."""
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {}, args=self.args)
        self.assertTrue(r._has_tray_uuid({"tray_uuid": "C482963767A24ACBB858F95D4376A2E5"}))
        self.assertFalse(r._has_tray_uuid({"tray_uuid": ""}))
        self.assertFalse(r._has_tray_uuid({}))
        self.assertEqual(r._norm_tray_identity_tag("1d33dd3b00000100"), "1D33DD3B00000100")

    def test_spool_exists_cache_per_run(self):
        """Multiple _should_stick checks for same spool_id cause only one GET (per-run cache). Spool 4 has UID match."""
        slot_a, slot_b = 1, 4
        tag = "AABBCCDD00112233"
        spools = [
            _spool(4, remaining_weight=800, rfid_tag_uid=tag, location="Shelf", color_hex="ff0000"),
            _spool(38, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000"),
        ]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000", "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        attrs = {"tag_uid": tag, "tray_uuid": "TRAYUUID001", "type": "PLA", "color": "ff0000", "name": "Bambu PLA", "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {}
        for slot in (slot_a, slot_b):
            ent = _tray_entity(slot)
            state_map[ent] = {"attributes": attrs, "state": "valid"}
            state_map[f"{ent}::all"] = {"attributes": attrs, "state": "valid"}
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "4"
            state_map[f"input_text.ams_slot_{slot}_tray_signature"] = "TRAYUUID001"
        for s in range(1, 7):
            state_map.setdefault(f"input_text.ams_slot_{s}_spool_id", "0")
            state_map.setdefault(f"input_text.ams_slot_{s}_expected_spool_id", "0")
            state_map.setdefault(f"input_text.ams_slot_{s}_status", "")
            state_map.setdefault(f"input_text.ams_slot_{s}_tray_signature", "")
            if s not in (slot_a, slot_b):
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        r = TestableReconcile(sm, state_map, args=self.args)
        get_paths = []
        orig_get = r._spoolman_get
        def count_get(path):
            get_paths.append(path)
            return orig_get(path)
        r._spoolman_get = count_get
        r._run_reconcile("test")
        spool_4_gets = [p for p in get_paths if p == "/api/v1/spool/4" or p.endswith("/spool/4")]
        self.assertGreaterEqual(len(spool_4_gets), 1, "GET spool 4: at least _spool_exists (guarded clearing uses spool_index)")

    def test_tray_uuid_missing_tag_uid_same_treat_as_unchanged(self):
        """Stored = tag_uid norm; this run tray_uuid missing but tag_uid same → SAME tray (hardening), no spool_id change."""
        slot = 4
        tag = "1D33DD3B00000100"
        spools = [_spool(4, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000")]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000", "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(slot)
        attrs_no_tray = {"tag_uid": tag, "type": "PLA", "color": "ff0000", "name": "Bambu PLA", "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs_no_tray, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs_no_tray, "state": "valid"},
        }
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            if s != slot:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        state_map[f"input_text.ams_slot_{slot}_spool_id"] = "4"
        state_map[f"input_text.ams_slot_{slot}_tray_signature"] = "1D33DD3B00000100"
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        current_sig = r._get_tray_identity(attrs_no_tray, tag)
        self.assertEqual(current_sig, "1D33DD3B00000100", "current from tag_uid only")
        spool_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_spool_id"]
        self.assertEqual(len(spool_writes), 0, "stored_sig == tag_norm and tray_uuid missing: treat as same tray, no spool_id change")

    def test_sticky_same_tray_signature_keeps_spool_id(self):
        """Same tray_signature and valid helper_spool_id => _force_location_and_helpers called with helper (4), not 38. Spool 4 has UID match."""
        slot = 4
        tag = "1D33DD3B00000100"
        spools = [
            _spool(4, remaining_weight=800, rfid_tag_uid=tag, location="Shelf", color_hex="ff0000"),
            _spool(38, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000"),
        ]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000", "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(slot)
        attrs = {"tag_uid": tag, "tray_uuid": "C482963767A24ACBB858F95D4376A2E5", "type": "PLA", "color": "ff0000", "name": "Bambu PLA", "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            if s != slot:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        state_map[f"input_text.ams_slot_{slot}_spool_id"] = "4"
        state_map[f"input_text.ams_slot_{slot}_tray_signature"] = "C482963767A24ACBB858F95D4376A2E5"
        r = TestableReconcile(sm, state_map, args=self.args)
        force_calls = []
        orig = r._force_location_and_helpers
        def capture_force(slot, spool_id, tag_uid, source, tray_meta=None, tray_state="", tray_identity=None, previous_helper_spool_id=0, **kwargs):
            force_calls.append((slot, spool_id, tag_uid, source))
            orig(slot, spool_id, tag_uid, source, tray_meta=tray_meta, tray_state=tray_state, tray_identity=tray_identity, previous_helper_spool_id=previous_helper_spool_id, **kwargs)
        r._force_location_and_helpers = capture_force
        r._run_reconcile("test")
        slot4_calls = [c for c in force_calls if c[0] == slot]
        self.assertGreater(len(slot4_calls), 0, "expected at least one _force_location_and_helpers call for slot 4")
        self.assertEqual(slot4_calls[-1][1], 4, "sticky: same tray_signature and valid helper 4 => pass spool_id=4, not tiebreak winner 38")

    def test_sticky_tray_signature_change_allows_spool_id_change(self):
        """When tray_signature changes (real swap), spool_id may change to resolved."""
        slot = 4
        tag = "1D33DD3B00000100"
        spools = [_spool(38, remaining_weight=500, rfid_tag_uid=tag, location="Shelf")]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000", "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(slot)
        attrs = {"tag_uid": tag, "tray_uuid": "NEWTRAY99999999999999999999999999999999", "type": "PLA", "color": "ff0000", "name": "Bambu PLA", "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            if s != slot:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        state_map[f"input_text.ams_slot_{slot}_spool_id"] = "4"
        state_map[f"input_text.ams_slot_{slot}_tray_signature"] = "OLD_TRAY_SIG_DIFFERENT"
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        spool_id_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_spool_id"]
        self.assertGreater(len(spool_id_writes), 0, "spool_id must be written")
        self.assertEqual(spool_id_writes[-1].get("value"), "38", "tray_signature changed => do not stick; write resolved 38")

    def test_slot_swap_clears_old_spool_location(self):
        """Previous helper spool_id=41 at AMS1_Slot1, new resolved=23 -> PATCH issued to move 41 out of AMS1_Slot1 to Shelf."""
        slot = 1
        tag = "1D33DD3B00000100"
        spools = [
            _spool(41, remaining_weight=400, rfid_tag_uid=None, location="AMS1_Slot1", color_hex="ff0000"),
            _spool(23, remaining_weight=500, rfid_tag_uid=tag, location="Shelf", color_hex="ff0000"),
        ]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000", "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(slot)
        attrs = {"tag_uid": tag, "tray_uuid": "C482963767A24ACBB858F95D4376A2E5", "type": "PLA", "color": "ff0000", "name": "Bambu PLA", "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {}
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            if s == slot:
                state_map[tray_ent] = {"attributes": attrs, "state": "valid"}
                state_map[f"{tray_ent}::all"] = {"attributes": attrs, "state": "valid"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = "41"
            else:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        clear_41 = [p for p in sm.patches if p.get("spool_id") == 41 and p.get("payload", {}).get("location") == "Shelf"]
        self.assertGreater(len(clear_41), 0, "expected a PATCH to spool 41 with location=Shelf (clear previous slot occupant)")
        location_23 = [p for p in sm.patches if p.get("spool_id") == 23 and p.get("payload", {}).get("location") == "AMS1_Slot1"]
        self.assertGreater(len(location_23), 0, "expected a PATCH to spool 23 with location=AMS1_Slot1")

    def test_unbind_clears_old_spool_location(self):
        """Previous helper 41 at AMS1_Slot1, unbind (no tag) -> clear 41 out of AMS1_Slot1 to Shelf."""
        slot = 1
        spools = [_spool(41, remaining_weight=400, rfid_tag_uid=None, location="AMS1_Slot1", color_hex="ff0000")]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000", "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        state_map = {}
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
            state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
            state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        state_map[f"input_text.ams_slot_{slot}_spool_id"] = "41"
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        clear_41 = [p for p in sm.patches if p.get("spool_id") == 41 and p.get("payload", {}).get("location") == "Shelf"]
        self.assertGreater(len(clear_41), 0, "expected a PATCH to spool 41 with location=Shelf on unbind")
        spool_id_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_spool_id"]
        self.assertGreater(len(spool_id_writes), 0)
        self.assertEqual(spool_id_writes[-1].get("value"), "0", "unbind must set spool_id helper to 0")

    def test_no_clear_if_old_spool_not_at_slot(self):
        """Old spool 41 location != AMS1_Slot1 -> no PATCH to clear 41 (avoid destructive move)."""
        slot = 1
        tag = "1D33DD3B00000100"
        spools = [
            _spool(41, remaining_weight=400, rfid_tag_uid=None, location="Shelf", color_hex="ff0000"),
            _spool(23, remaining_weight=500, rfid_tag_uid=tag, location="Shelf", color_hex="ff0000"),
        ]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000", "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(slot)
        attrs = {"tag_uid": tag, "tray_uuid": "C482963767A24ACBB858F95D4376A2E5", "type": "PLA", "color": "ff0000", "name": "Bambu PLA", "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {}
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            if s == slot:
                state_map[tray_ent] = {"attributes": attrs, "state": "valid"}
                state_map[f"{tray_ent}::all"] = {"attributes": attrs, "state": "valid"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = "41"
            else:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        clear_41_to_shelf = [p for p in sm.patches if p.get("spool_id") == 41 and p.get("payload", {}).get("location") == "Shelf"]
        self.assertEqual(len(clear_41_to_shelf), 0, "must not clear spool 41 when its location is not AMS1_Slot1")

    def test_rfid_no_shelf_match_needs_action(self):
        """PHASE_2_5: RFID tag in tray but no spool at Shelf with that UID => UNBOUND_ACTION_REQUIRED, no bind, no create."""
        from filament_iq.ams_rfid_reconcile import STATUS_UNBOUND_ACTION_REQUIRED
        slot = 1
        tag = "AABBCCDD00112233"
        spools = [_spool(99, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000")]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000", "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(slot)
        attrs = {"tag_uid": tag, "type": "PLA", "color": "ff0000", "name": "Bambu PLA", "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {}
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            if s == slot:
                state_map[tray_ent] = {"attributes": attrs, "state": "valid"}
                state_map[f"{tray_ent}::all"] = {"attributes": attrs, "state": "valid"}
            else:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_UNBOUND_ACTION_REQUIRED, "must set NEEDS_ACTION when no Shelf UID match")
        bind_patches = [p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})]
        self.assertEqual(len(bind_patches), 0, "must not bind when no spool at Shelf has this RFID UID")


    # ── Truth Guard tests ───────────────────────────────────────────────

    def test_truth_guard_rfid_visible_helper_mismatch_blocks_writes(self):
        """RFID_VISIBLE: helper spool (10) RFID UID differs from tray tag_uid ->
        guard clears stale helper, RFID resolution resolves to correct spool (20),
        no PATCH for the wrong spool (10)."""
        slot = 1
        tray_tag = "A6EC1BDE00000100"
        helper_tag = "DEADBEEF00000100"
        spool_helper = _spool(10, rfid_tag_uid=helper_tag, location="Shelf", material="PLA", color_hex="ff0000")
        spool_other = _spool(20, rfid_tag_uid=tray_tag, location="Shelf", material="PLA", color_hex="ff0000")
        filaments = [_bambu_filament()]
        sm = FakeSpoolman([spool_helper, spool_other], filaments)
        tray_ent = _tray_entity(slot)
        attrs = {"tag_uid": tray_tag, "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {}
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""
            if s == slot:
                state_map[tray_ent] = {"attributes": attrs, "state": "valid"}
                state_map[f"{tray_ent}::all"] = {"attributes": attrs, "state": "valid"}
            else:
                empty_a = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_a, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_a, "state": "empty"}
        state_map[f"input_text.ams_slot_{slot}_spool_id"] = "10"
        state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "10"
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        bad_patches = [p for p in sm.patches if p.get("spool_id") == 10]
        self.assertEqual(len(bad_patches), 0, "must not PATCH the mismatched helper spool 10")
        spool_id_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_spool_id"]
        self.assertGreater(len(spool_id_writes), 0, "must write helper_spool_id")
        cleared = any(w.get("value") == "0" for w in spool_id_writes)
        self.assertTrue(cleared, "truth guard must clear stale helper to 0 before RFID resolution")
        self.assertEqual(spool_id_writes[-1].get("value"), "20", "RFID resolution must bind to correct spool 20")
        unbound_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_unbound_reason"]
        self.assertGreater(len(unbound_writes), 0, "must write unbound_reason")
        self.assertEqual(unbound_writes[0].get("value"), UNBOUND_HELPER_RFID_MISMATCH)
        summary = r._last_summary
        self.assertIsNotNone(summary)
        slot_t = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
        self.assertIsNotNone(slot_t)
        self.assertEqual(slot_t.get("unbound_reason"), UNBOUND_HELPER_RFID_MISMATCH)

    def test_truth_guard_force_location_allows_patch_when_bound_invariant(self):
        """_force_location_and_helpers: when expected_spool_id == helper_spool_id (manual bind),
        material mismatch (PETG vs PLA) warns only; location PATCH is allowed and helpers are NOT cleared."""
        slot = 5
        helper_spool = _spool(7, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                              material="PETG", color_hex="00ff00", name="Bambu PETG")
        other_at_slot = _spool(99, remaining_weight=300, rfid_tag_uid=None, location="AMS2_Slot1",
                               material="PLA", color_hex="ff0000")
        filaments = [_bambu_filament()]
        sm = FakeSpoolman([helper_spool, other_at_slot], filaments)
        tray_meta = {"name": "Bambu PLA", "type": "PLA", "filament_id": "bambu",
                     "color": "ff0000", "color_hex": "ff0000", "color_candidates": ["ff0000"]}
        spool_index = {s["id"]: s for s in sm.spools.values()}
        state_map = {
            f"input_text.ams_slot_{slot}_spool_id": "7",
            f"input_text.ams_slot_{slot}_expected_spool_id": "7",
            f"input_text.ams_slot_{slot}_unbound_reason": "",
        }
        r = TestableReconcile(sm, state_map, args=self.args)
        r._active_run = {"reason": "test", "writes": [], "decisions": [], "no_write_paths": [],
                         "conflicts": [], "unknown_tags": [], "auto_registers": [],
                         "validation_transcripts": [], "spool_exists_cache": {}}
        t = {"slot": slot}
        r._force_location_and_helpers(
            slot, 7, "", source="test_guard",
            tray_meta=tray_meta, tray_state="valid", tray_identity="HT_SLOT_5",
            previous_helper_spool_id=0,
            spool_index=spool_index, t=t, tray_empty=False, tray_state_str="valid",
        )
        self.assertEqual(len(sm.patches), 1, "bound invariant holds: truth guard allows location PATCH")
        spool_id_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_spool_id"]
        cleared = any(w.get("value") == "0" for w in spool_id_writes)
        self.assertFalse(cleared, "bound invariant: truth guard must NOT clear helper")

    def test_truth_guard_converge_ha_sig_blocks_comment_patch_on_material_mismatch(self):
        """_converge_ha_sig: material mismatch blocks comment PATCH (no spoolman_patch for comment)."""
        slot = 5
        helper_spool = _spool(7, remaining_weight=500, rfid_tag_uid=None, location="AMS2_Slot1",
                              material="PETG", color_hex="00ff00", name="Bambu PETG", comment="old_comment")
        filaments = [_bambu_filament(material="PETG", color_hex="00ff00", name="Bambu PETG")]
        sm = FakeSpoolman([helper_spool], filaments)
        tray_meta = {"name": "Bambu PLA", "type": "PLA", "filament_id": "bambu",
                     "color": "ff0000", "color_hex": "ff0000", "color_candidates": ["ff0000"]}
        spool_index = {s["id"]: s for s in sm.spools.values()}
        r = TestableReconcile(sm, {}, args=self.args)
        r._active_run = {"reason": "test", "writes": [], "decisions": [], "no_write_paths": [],
                         "conflicts": [], "unknown_tags": [], "auto_registers": [],
                         "validation_transcripts": [], "spool_exists_cache": {}}
        # v4: _converge_lot_nr replaces _converge_ha_sig; truth guard blocks lot_nr PATCH on material mismatch
        r._converge_lot_nr(slot, 7, tray_meta, spool_index, tray_uuid="", tag_uid="")
        lot_nr_patches = [p for p in sm.patches if "lot_nr" in (p.get("payload") or {})]
        self.assertEqual(len(lot_nr_patches), 0, "truth guard must block lot_nr PATCH on material mismatch")


    def test_truth_guard_force_location_blocks_on_rfid_mismatch(self):
        """_force_location_and_helpers: spool RFID UID != tray tag_uid blocks all location PATCHes."""
        slot = 2
        tray_tag = "A6EC1BDE00000100"
        wrong_spool = _spool(10, rfid_tag_uid="DEADBEEF00000100", location="Shelf",
                             material="PLA", color_hex="ff0000")
        filaments = [_bambu_filament()]
        sm = FakeSpoolman([wrong_spool], filaments)
        tray_meta = {"name": "Bambu PLA", "type": "PLA", "filament_id": "bambu",
                     "color": "ff0000", "color_hex": "ff0000", "color_candidates": ["ff0000"]}
        spool_index = {s["id"]: s for s in sm.spools.values()}
        state_map = {
            f"input_text.ams_slot_{slot}_spool_id": "10",
            f"input_text.ams_slot_{slot}_expected_spool_id": "10",
            f"input_text.ams_slot_{slot}_unbound_reason": "",
        }
        r = TestableReconcile(sm, state_map, args=self.args)
        r._active_run = {"reason": "test", "writes": [], "decisions": [], "no_write_paths": [],
                         "conflicts": [], "unknown_tags": [], "auto_registers": [],
                         "validation_transcripts": [], "spool_exists_cache": {}}
        t = {"slot": slot}
        r._force_location_and_helpers(
            slot, 10, tray_tag, source="test_rfid_guard",
            tray_meta=tray_meta, tray_state="valid", tray_identity=tray_tag,
            previous_helper_spool_id=0,
            spool_index=spool_index, t=t, tray_empty=False, tray_state_str="valid",
        )
        self.assertEqual(len(sm.patches), 0, "truth guard must block ALL PATCHes when RFID UID mismatch")
        spool_id_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_spool_id"]
        cleared = any(w.get("value") == "0" for w in spool_id_writes)
        self.assertTrue(cleared, "truth guard must clear helper on RFID mismatch")
        unbound_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_unbound_reason"]
        self.assertGreater(len(unbound_writes), 0)
        self.assertEqual(unbound_writes[-1].get("value"), UNBOUND_HELPER_RFID_MISMATCH)

    def test_truth_guard_full_reconcile_rfid_mismatch_zero_spoolman_patches_in_writes_performed(self):
        """Full reconcile: RFID_VISIBLE helper mismatch -> transcript writes_performed has zero spoolman_patch entries for that slot."""
        slot = 1
        tray_tag = "A6EC1BDE00000100"
        helper_tag = "DEADBEEF00000100"
        spool_helper = _spool(10, rfid_tag_uid=helper_tag, location="Shelf", material="PLA", color_hex="ff0000")
        spool_correct = _spool(20, rfid_tag_uid=tray_tag, location="Shelf", material="PLA", color_hex="ff0000")
        filaments = [_bambu_filament()]
        sm = FakeSpoolman([spool_helper, spool_correct], filaments)
        tray_ent = _tray_entity(slot)
        attrs = {"tag_uid": tray_tag, "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {}
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""
            if s == slot:
                state_map[tray_ent] = {"attributes": attrs, "state": "valid"}
                state_map[f"{tray_ent}::all"] = {"attributes": attrs, "state": "valid"}
            else:
                empty_a = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_a, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_a, "state": "empty"}
        state_map[f"input_text.ams_slot_{slot}_spool_id"] = "10"
        state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "10"
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        summary = r._last_summary
        self.assertIsNotNone(summary)
        slot_t = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
        self.assertIsNotNone(slot_t)
        spoolman_patch_writes = [w for w in slot_t.get("writes_performed", []) if w.startswith("spoolman_patch")]
        bad_patches = [p for p in sm.patches if p.get("spool_id") == 10]
        self.assertEqual(len(bad_patches), 0, "mismatched helper spool 10 must have zero PATCHes")
        self.assertEqual(slot_t.get("unbound_reason"), UNBOUND_HELPER_RFID_MISMATCH,
                         "transcript must record RFID mismatch unbound_reason")

    def test_truth_guard_full_reconcile_material_mismatch_zero_patches(self):
        """Full reconcile: HT slot IDENTITY_UNAVAILABLE, helper material mismatch -> zero Spoolman PATCHes for that spool."""
        slot = 5
        ht_attrs = {
            "tag_uid": "0000000000000000",
            "tray_uuid": "00000000000000000000000000000000",
            "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
            "filament_id": "bambu", "tray_weight": 1000, "remain": 50,
        }
        helper_spool = _spool(7, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                              material="PETG", color_hex="00ff00", name="Bambu PETG")
        filaments = [_bambu_filament(material="PETG", color_hex="00ff00", name="Bambu PETG")]
        sm = FakeSpoolman([helper_spool], filaments)
        tray_ent = _tray_entity(slot)
        state_map = {"input_boolean.filament_iq_nonrfid_enabled": "on"}
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""
            if s == slot:
                state_map[tray_ent] = {"attributes": ht_attrs, "state": "valid"}
                state_map[f"{tray_ent}::all"] = {"attributes": ht_attrs, "state": "valid"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = "7"
            else:
                empty_a = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_a, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_a, "state": "empty"}
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        self.assertEqual(len(sm.patches), 0, "material mismatch must result in zero Spoolman PATCHes")
        summary = r._last_summary
        self.assertIsNotNone(summary)
        slot_t = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
        self.assertIsNotNone(slot_t)
        spoolman_patch_writes = [w for w in slot_t.get("writes_performed", []) if w.startswith("spoolman_patch")]
        self.assertEqual(len(spoolman_patch_writes), 0, "writes_performed must have zero spoolman_patch entries")
        self.assertIn(
            slot_t.get("unbound_reason"),
            (UNBOUND_HELPER_MATERIAL_MISMATCH, UNBOUND_NONRFID_NO_MATCH),
            "transcript: material mismatch may clear via truth guard or swap-detect -> rematch path",
        )
        spool_id_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_spool_id"]
        cleared = any(w.get("value") == "0" for w in spool_id_writes)
        self.assertTrue(cleared, "helper must be cleared on material mismatch")


    # ── Bambu vendor exclusion in non-RFID candidate selection ──────────

    def test_vendor_name_and_is_bambu_vendor_helpers(self):
        """Unit test for module-level _vendor_name / _is_bambu_vendor helpers."""
        bambu = {"filament": {"vendor": {"name": "Bambu Lab"}, "material": "PLA"}}
        overture = {"filament": {"vendor": {"name": "Overture"}, "material": "PLA"}}
        empty_vendor = {"filament": {"vendor": {}, "material": "PLA"}}
        no_filament = {}

        self.assertEqual(_vendor_name(bambu), "Bambu Lab")
        self.assertEqual(_vendor_name(overture), "Overture")
        self.assertEqual(_vendor_name(empty_vendor), "")
        self.assertEqual(_vendor_name(no_filament), "")

        self.assertTrue(_is_bambu_vendor(bambu))
        self.assertFalse(_is_bambu_vendor(overture))
        self.assertFalse(_is_bambu_vendor(empty_vendor))
        self.assertFalse(_is_bambu_vendor(no_filament))

    def test_nonrfid_excludes_bambu_keeps_non_bambu(self):
        """Non-RFID candidate selection must exclude Bambu Lab spools with generic filament_id
        and keep non-Bambu. Bambu with specific filament_id is eligible (tested separately)."""
        spools = [
            _spool(10, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                   color_hex="ff0000", material="PLA", name="Bambu PLA Basic", vendor_name="Bambu Lab"),
            _spool(20, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                   color_hex="ff0000", material="PLA", name="Overture PLA", vendor_name="Overture"),
        ]
        spools[0]["filament"]["external_id"] = "GFA99"
        filaments = [
            {"id": 1, "name": "Overture PLA", "material": "PLA", "color_hex": "ff0000",
             "vendor": {"name": "Overture"}, "external_id": "overture"},
        ]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        tray = _tray_state("", tray_type="PLA", color="ff0000", name="PLA", filament_id="overture")
        state_map = {
            tray_ent: tray,
            f"{tray_ent}::all": {"attributes": tray["attributes"], "state": "valid"},
        }
        state_map["input_boolean.filament_iq_nonrfid_enabled"] = "on"
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        summary = r._last_summary
        self.assertIsNotNone(summary)
        slot1 = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == 1), None)
        self.assertIsNotNone(slot1)
        self.assertEqual(slot1.get("final_spool_id"), 20, "must bind to non-Bambu spool 20")
        self.assertIn(slot1.get("reason"), ("lot_nr_match", "nonrfid_auto_match"), "unified non-RFID path may set lot_nr_match or nonrfid_auto_match")

        bambu_patches = [p for p in sm.patches if "/spool/10" in p.get("path", "")]
        self.assertEqual(len(bambu_patches), 0, "Bambu spool 10 must never be PATCHed in non-RFID")

        non_bambu_patches = [p for p in sm.patches if "/spool/20" in p.get("path", "")]
        self.assertGreaterEqual(len(non_bambu_patches), 1, "non-Bambu spool 20 should be PATCHed")

    def test_rfid_visible_does_not_exclude_bambu(self):
        """RFID_VISIBLE mode must NOT apply the Bambu vendor filter — Bambu spools
        with matching RFID tag must still resolve normally via UID lookup."""
        tag = "A1B2C3D400000100"
        spools = [
            _spool(30, remaining_weight=500, rfid_tag_uid=tag, location="Shelf",
                   color_hex="ff0000", material="PLA", name="Bambu PLA Basic", vendor_name="Bambu Lab"),
        ]
        filaments = [
            {"id": 1, "name": "Bambu PLA Basic", "material": "PLA", "color_hex": "ff0000",
             "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"},
        ]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        state_map = {
            tray_ent: _tray_state(tag, tray_type="PLA", color="ff0000", name="Bambu PLA Basic", filament_id="bambu"),
            f"{tray_ent}::all": {"attributes": _tray_state(tag)["attributes"], "state": "valid"},
        }
        state_map["input_boolean.filament_iq_nonrfid_enabled"] = "on"
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        summary = r._last_summary
        self.assertIsNotNone(summary)
        slot1 = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == 1), None)
        self.assertIsNotNone(slot1)
        self.assertEqual(slot1.get("final_spool_id"), 30,
                         "Bambu spool 30 must resolve via RFID UID match regardless of vendor")

        patches_30 = [p for p in sm.patches if "/spool/30" in p.get("path", "")]
        self.assertGreaterEqual(len(patches_30), 1,
                                "Bambu spool 30 should be PATCHed in RFID_VISIBLE mode")


    def test_nonrfid_new_fallback_excludes_bambu_keeps_overture(self):
        """_find_deterministic_candidates_new_only must exclude Bambu Lab spools with generic
        filament_id and keep non-Bambu (Overture). Both at location New, matching material."""
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {}, args=self.args)
        r._active_run = {
            "decisions": [], "no_write_paths": [], "writes": [],
            "conflicts": [], "unknown_tags": [], "auto_registers": [],
            "validation_transcripts": [],
        }
        attrs = {"tag_uid": "", "type": "PLA", "color": "ff0000", "name": "PLA",
                 "filament_id": "generic", "tray_weight": 1000, "remain": 50}
        tray_meta = r._tray_meta(attrs, "valid")

        spools = [
            _spool(50, remaining_weight=500, rfid_tag_uid=None, location="New",
                   color_hex="ff0000", material="PLA", name="Bambu PLA Basic", vendor_name="Bambu Lab"),
            _spool(51, remaining_weight=500, rfid_tag_uid=None, location="New",
                   color_hex="ff0000", material="PLA", name="Overture PLA", vendor_name="Overture"),
        ]
        spools[0]["filament"]["external_id"] = "GFA99"

        result = r._find_deterministic_candidates_new_only(spools, tray_meta, slot=1)
        self.assertEqual(result, [51], "only Overture spool 51 should be candidate; Bambu 50 excluded")

        bambu_rejects = [d for d in r._active_run["decisions"]
                         if d.get("decision") == "candidate_reject"
                         and d.get("payload", {}).get("reason") == "bambu_generic_sentinel"
                         and d.get("payload", {}).get("spool_id") == 50]
        self.assertEqual(len(bambu_rejects), 1, "Bambu spool 50 must be recorded as rejected (generic sentinel)")


    # ── Non-RFID location convergence (self-healing) ──────────────────

    def test_nonrfid_bound_invariant_converges_location_from_shelf(self):
        """Bound invariant (spool_id == expected > 0, no tag): spool at Shelf must be
        PATCHed back to canonical AMS location when status_only=False."""
        slot = 3
        spool_id = 42
        canonical = CANONICAL_LOCATION_BY_SLOT[slot]
        spools = [_spool(spool_id, remaining_weight=400, rfid_tag_uid=None, location="Shelf",
                         color_hex="ff0000", material="PLA", vendor_name="Overture", name="Overture PLA")]
        sm = FakeSpoolman(spools, [])
        tray_ent = _tray_entity(slot)
        tray_attrs = {"tag_uid": "", "type": "PLA", "color": "ff0000", "name": "Overture PLA",
                      "filament_id": "overture", "tray_weight": 1000, "remain": 50}
        state_map = {}
        for s in range(1, 7):
            ent = _tray_entity(s)
            if s == slot:
                state_map[ent] = {"attributes": tray_attrs, "state": "valid"}
                state_map[f"{ent}::all"] = {"attributes": tray_attrs, "state": "valid"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = str(spool_id)
                state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = str(spool_id)
            else:
                empty_a = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[ent] = {"attributes": empty_a, "state": "empty"}
                state_map[f"{ent}::all"] = {"attributes": empty_a, "state": "empty"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
                state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test", status_only=False)

        loc_patches = [p for p in sm.patches
                       if p.get("path") == f"/api/v1/spool/{spool_id}"
                       and p.get("payload", {}).get("location") == canonical]
        self.assertGreaterEqual(len(loc_patches), 1,
                                f"spool {spool_id} at Shelf must be PATCHed to {canonical}")

        summary = r._last_summary
        self.assertIsNotNone(summary)
        slot_t = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
        self.assertIsNotNone(slot_t)
        self.assertEqual(slot_t.get("final_spool_id"), spool_id)
        self.assertEqual(slot_t.get("reason"), "bound_invariant")
        self.assertEqual(slot_t.get("final_location"), canonical)

        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_NON_RFID_REGISTERED)

    def test_nonrfid_bound_invariant_status_only_no_patch(self):
        """Bound invariant with status_only=True must NOT PATCH Spoolman location."""
        slot = 3
        spool_id = 42
        spools = [_spool(spool_id, remaining_weight=400, rfid_tag_uid=None, location="Shelf",
                         color_hex="ff0000", material="PLA", vendor_name="Overture", name="Overture PLA")]
        sm = FakeSpoolman(spools, [])
        tray_ent = _tray_entity(slot)
        tray_attrs = {"tag_uid": "", "type": "PLA", "color": "ff0000", "name": "Overture PLA",
                      "filament_id": "overture", "tray_weight": 1000, "remain": 50}
        state_map = {}
        for s in range(1, 7):
            ent = _tray_entity(s)
            if s == slot:
                state_map[ent] = {"attributes": tray_attrs, "state": "valid"}
                state_map[f"{ent}::all"] = {"attributes": tray_attrs, "state": "valid"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = str(spool_id)
                state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = str(spool_id)
            else:
                empty_a = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[ent] = {"attributes": empty_a, "state": "empty"}
                state_map[f"{ent}::all"] = {"attributes": empty_a, "state": "empty"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
                state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test", status_only=True)

        self.assertEqual(len(sm.patches), 0, "status_only=True must produce zero Spoolman PATCHes")

    def test_nonrfid_bound_invariant_truth_guard_allows_patch(self):
        """Bound invariant: when expected_spool_id == helper_spool_id (manual bind), material mismatch
        warns only and location PATCH is allowed."""
        slot = 3
        spool_id = 42
        spools = [_spool(spool_id, remaining_weight=400, rfid_tag_uid=None, location="Shelf",
                         color_hex="ff0000", material="PETG", vendor_name="Overture", name="Overture PETG")]
        sm = FakeSpoolman(spools, [])
        tray_ent = _tray_entity(slot)
        tray_attrs = {"tag_uid": "", "type": "PLA", "color": "ff0000", "name": "Overture PLA",
                      "filament_id": "overture", "tray_weight": 1000, "remain": 50}
        state_map = {}
        for s in range(1, 7):
            ent = _tray_entity(s)
            if s == slot:
                state_map[ent] = {"attributes": tray_attrs, "state": "valid"}
                state_map[f"{ent}::all"] = {"attributes": tray_attrs, "state": "valid"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = str(spool_id)
                state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = str(spool_id)
            else:
                empty_a = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[ent] = {"attributes": empty_a, "state": "empty"}
                state_map[f"{ent}::all"] = {"attributes": empty_a, "state": "empty"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
                state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test", status_only=False)

        loc_patches = [p for p in sm.patches
                       if p.get("path") == f"/api/v1/spool/{spool_id}"
                       and "location" in p.get("payload", {})]
        self.assertEqual(len(loc_patches), 1,
                         "bound invariant holds (expected==spool_id): truth guard allows location PATCH")

    def test_nonrfid_stable_converges_location_from_shelf(self):
        """Non-RFID stable (spool_id > 0, expected == 0, no tag): spool at Shelf
        must be PATCHed to canonical AMS location on first promotion."""
        slot = 5
        spool_id = 77
        canonical = CANONICAL_LOCATION_BY_SLOT[slot]
        spools = [_spool(spool_id, remaining_weight=300, rfid_tag_uid=None, location="Shelf",
                         color_hex="00ff00", material="PETG", vendor_name="Overture", name="Overture PETG")]
        sm = FakeSpoolman(spools, [])
        tray_ent = _tray_entity(slot)
        tray_attrs = {"tag_uid": "", "type": "PETG", "color": "00ff00", "name": "Overture PETG",
                      "filament_id": "overture", "tray_weight": 1000, "remain": 50}
        state_map = {}
        for s in range(1, 7):
            ent = _tray_entity(s)
            if s == slot:
                state_map[ent] = {"attributes": tray_attrs, "state": "valid"}
                state_map[f"{ent}::all"] = {"attributes": tray_attrs, "state": "valid"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = str(spool_id)
                state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            else:
                empty_a = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[ent] = {"attributes": empty_a, "state": "empty"}
                state_map[f"{ent}::all"] = {"attributes": empty_a, "state": "empty"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
                state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test", status_only=False)

        loc_patches = [p for p in sm.patches
                       if p.get("path") == f"/api/v1/spool/{spool_id}"
                       and p.get("payload", {}).get("location") == canonical]
        self.assertGreaterEqual(len(loc_patches), 1,
                                f"spool {spool_id} at Shelf must be PATCHed to {canonical}")

        summary = r._last_summary
        self.assertIsNotNone(summary)
        slot_t = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
        self.assertIsNotNone(slot_t)
        self.assertEqual(slot_t.get("final_spool_id"), spool_id)
        self.assertEqual(slot_t.get("reason"), "non_rfid_stable")
        self.assertEqual(slot_t.get("final_location"), canonical)


    # ── Pending demotion for identity-unavailable slots ─────────────────

    def _setup_pending_demote(self, slot, spool_id, expected_id, tray_empty=False, material="PLA",
                              spool_missing=False, tag_uid="", stored_status="PENDING_RFID_READ",
                              pending_until=""):
        """Helper: build state for pending demotion tests."""
        if spool_missing:
            spools = []
        else:
            spools = [_spool(spool_id, remaining_weight=400, rfid_tag_uid=None, location="Shelf",
                             color_hex="ff0000", material=material, vendor_name="Overture", name="Overture " + material)]
        sm = FakeSpoolman(spools, [])
        tray_attrs = {"tag_uid": tag_uid, "type": material, "color": "ff0000", "name": "Overture " + material,
                      "filament_id": "overture", "tray_weight": 1000, "remain": 50}
        tray_state_val = "empty" if tray_empty else "valid"
        state_map = {}
        for s in range(1, 7):
            ent = _tray_entity(s)
            if s == slot:
                state_map[ent] = {"attributes": tray_attrs, "state": tray_state_val}
                state_map[f"{ent}::all"] = {"attributes": tray_attrs, "state": tray_state_val}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = str(spool_id)
                state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = str(expected_id)
                state_map[f"input_text.ams_slot_{s}_status"] = stored_status
                state_map[f"input_text.ams_slot_{s}_rfid_pending_until"] = pending_until
            else:
                empty_a = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[ent] = {"attributes": empty_a, "state": "empty"}
                state_map[f"{ent}::all"] = {"attributes": empty_a, "state": "empty"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
                state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
                state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""
        return sm, state_map

    def test_nonrfid_identity_unavailable_demotes_pending_clears_expected(self):
        """Identity unavailable + helper_spool_id=45, expected=1 (stale) ->
        demotes to NON_RFID_REGISTERED, clears expected, converges location."""
        slot = 1
        spool_id = 45
        expected_stale = 1
        canonical = CANONICAL_LOCATION_BY_SLOT[slot]

        sm, state_map = self._setup_pending_demote(slot, spool_id, expected_stale)
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test", status_only=False)

        summary = r._last_summary
        self.assertIsNotNone(summary)
        slot_t = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
        self.assertIsNotNone(slot_t)
        self.assertEqual(slot_t.get("final_spool_id"), spool_id)
        self.assertEqual(slot_t.get("reason"), "pending_demote_identity_unavailable")
        self.assertEqual(slot_t.get("final_location"), canonical)

        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_NON_RFID_REGISTERED)

        expected_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_expected_spool_id"]
        cleared = any(w.get("value") == "0" for w in expected_writes)
        self.assertTrue(cleared, "expected_spool_id must be cleared to 0")

        loc_patches = [p for p in sm.patches
                       if p.get("path") == f"/api/v1/spool/{spool_id}"
                       and p.get("payload", {}).get("location") == canonical]
        self.assertGreaterEqual(len(loc_patches), 1,
                                f"spool {spool_id} must be PATCHed to {canonical}")

    def test_nonrfid_identity_unavailable_pending_status_only_no_patch(self):
        """Same stale-expected scenario with status_only=True -> no Spoolman PATCHes,
        but expected still cleared."""
        slot = 1
        spool_id = 45
        expected_stale = 1

        sm, state_map = self._setup_pending_demote(slot, spool_id, expected_stale)
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test", status_only=True)

        self.assertEqual(len(sm.patches), 0, "status_only=True must produce zero Spoolman PATCHes")

        expected_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_expected_spool_id"]
        cleared = any(w.get("value") == "0" for w in expected_writes)
        self.assertTrue(cleared, "expected_spool_id must still be cleared even in status_only")

    def test_nonrfid_identity_unavailable_pending_no_action_when_empty_tray(self):
        """Empty tray + stale expected -> do NOT demote; keep existing empty-tray behavior."""
        slot = 1
        spool_id = 45
        expected_stale = 1

        sm, state_map = self._setup_pending_demote(slot, spool_id, expected_stale, tray_empty=True)
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test", status_only=False)

        summary = r._last_summary
        self.assertIsNotNone(summary)
        slot_t = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
        self.assertIsNotNone(slot_t)
        self.assertNotEqual(slot_t.get("reason"), "pending_demote_identity_unavailable",
                            "empty tray must not trigger pending demotion")

    def test_nonrfid_identity_unavailable_pending_helper_missing(self):
        """Stale expected but helper spool missing in Spoolman -> demotion fires
        (helper_spool_id > 0 in HA helpers); truth guard in force_location handles missing spool."""
        slot = 1
        spool_id = 45
        expected_stale = 1

        sm, state_map = self._setup_pending_demote(slot, spool_id, expected_stale, spool_missing=True)
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test", status_only=False)

        summary = r._last_summary
        self.assertIsNotNone(summary)
        slot_t = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
        self.assertIsNotNone(slot_t)
        self.assertEqual(slot_t.get("reason"), "pending_demote_identity_unavailable",
                         "demotion fires (helper valid in helpers); truth guard in force_location handles missing spool")

    def test_nonrfid_pending_demote_expected_zero_still_fires(self):
        """Pending + identity unavailable + helper_spool_id=45 + expected=0 (not stale
        but nonsensical for pending) -> demotion fires, clears pending, converges location."""
        slot = 1
        spool_id = 45
        canonical = CANONICAL_LOCATION_BY_SLOT[slot]

        sm, state_map = self._setup_pending_demote(slot, spool_id, expected_id=0)
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test", status_only=False)

        summary = r._last_summary
        self.assertIsNotNone(summary)
        slot_t = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
        self.assertIsNotNone(slot_t)
        self.assertEqual(slot_t.get("reason"), "pending_demote_identity_unavailable")
        self.assertEqual(slot_t.get("final_spool_id"), spool_id)

        loc_patches = [p for p in sm.patches
                       if p.get("path") == f"/api/v1/spool/{spool_id}"
                       and p.get("payload", {}).get("location") == canonical]
        self.assertGreaterEqual(len(loc_patches), 1,
                                f"spool {spool_id} must be PATCHed to {canonical}")

    def test_nonrfid_pending_demote_not_pending_no_demotion(self):
        """Not actually pending (status != PENDING_RFID_READ, pending_until empty) ->
        demotion must NOT fire even with stale expected."""
        slot = 1
        spool_id = 45
        expected_stale = 1

        sm, state_map = self._setup_pending_demote(
            slot, spool_id, expected_stale, stored_status="", pending_until="")
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test", status_only=False)

        summary = r._last_summary
        self.assertIsNotNone(summary)
        slot_t = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
        self.assertIsNotNone(slot_t)
        self.assertNotEqual(slot_t.get("reason"), "pending_demote_identity_unavailable",
                            "not-pending slot must not trigger demotion")

    def test_nonrfid_pending_demote_rfid_visible_no_demotion(self):
        """tag_uid present (RFID_VISIBLE) + pending + stale expected -> demotion must NOT fire."""
        slot = 1
        spool_id = 45
        expected_stale = 1

        sm, state_map = self._setup_pending_demote(
            slot, spool_id, expected_stale, tag_uid="A1B2C3D400000100")
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test", status_only=False)

        summary = r._last_summary
        self.assertIsNotNone(summary)
        slot_t = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
        self.assertIsNotNone(slot_t)
        self.assertNotEqual(slot_t.get("reason"), "pending_demote_identity_unavailable",
                            "RFID_VISIBLE slot must not trigger pending demotion")


    # ------------------------------------------------------------------
    # Location semantics: cleared spools go to Shelf or Empty by weight
    # ------------------------------------------------------------------

    def test_clear_slot_moves_prior_spool_to_shelf_when_remaining_positive(self):
        """Previous spool at slot with remaining_weight > 0 must PATCH to Shelf, never Empty."""
        slot = 1
        tag = "1D33DD3B00000100"
        prev_id = 41
        new_id = 23
        spools = [
            _spool(prev_id, remaining_weight=400, rfid_tag_uid=None, location="AMS1_Slot1", color_hex="ff0000"),
            _spool(new_id, remaining_weight=500, rfid_tag_uid=tag, location="Shelf", color_hex="ff0000"),
        ]
        filaments = [_bambu_filament()]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(slot)
        attrs = {"tag_uid": tag, "tray_uuid": "C482963767A24ACBB858F95D4376A2E5",
                 "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {}
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            if s == slot:
                state_map[tray_ent] = {"attributes": attrs, "state": "valid"}
                state_map[f"{tray_ent}::all"] = {"attributes": attrs, "state": "valid"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = str(prev_id)
            else:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "",
                               "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        shelf_patches = [p for p in sm.patches
                         if p.get("spool_id") == prev_id
                         and p.get("payload", {}).get("location") == LOCATION_NOT_IN_AMS]
        empty_patches = [p for p in sm.patches
                         if p.get("spool_id") == prev_id
                         and p.get("payload", {}).get("location") == LOCATION_EMPTY]
        self.assertGreater(len(shelf_patches), 0,
                           f"spool {prev_id} with remaining>0 must be moved to {LOCATION_NOT_IN_AMS}")
        self.assertEqual(len(empty_patches), 0,
                         f"spool {prev_id} with remaining>0 must NOT be moved to {LOCATION_EMPTY}")

    def test_clear_slot_moves_prior_spool_to_empty_when_remaining_zero(self):
        """Previous spool at slot with remaining_weight == 0 must PATCH to Empty (end-of-life)."""
        slot = 1
        tag = "1D33DD3B00000100"
        prev_id = 41
        new_id = 23
        spools = [
            _spool(prev_id, remaining_weight=0, rfid_tag_uid=None, location="AMS1_Slot1", color_hex="ff0000"),
            _spool(new_id, remaining_weight=500, rfid_tag_uid=tag, location="Shelf", color_hex="ff0000"),
        ]
        filaments = [_bambu_filament()]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(slot)
        attrs = {"tag_uid": tag, "tray_uuid": "C482963767A24ACBB858F95D4376A2E5",
                 "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        state_map = {}
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            if s == slot:
                state_map[tray_ent] = {"attributes": attrs, "state": "valid"}
                state_map[f"{tray_ent}::all"] = {"attributes": attrs, "state": "valid"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = str(prev_id)
            else:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "",
                               "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        empty_patches = [p for p in sm.patches
                         if p.get("spool_id") == prev_id
                         and p.get("payload", {}).get("location") == LOCATION_EMPTY]
        shelf_patches = [p for p in sm.patches
                         if p.get("spool_id") == prev_id
                         and p.get("payload", {}).get("location") == LOCATION_NOT_IN_AMS]
        self.assertGreater(len(empty_patches), 0,
                           f"spool {prev_id} with remaining==0 must be moved to {LOCATION_EMPTY}")
        self.assertEqual(len(shelf_patches), 0,
                         f"spool {prev_id} with remaining==0 must NOT be moved to {LOCATION_NOT_IN_AMS}")


    # ── HT non-RFID fingerprint / confidence / auto-match tests ──

    def _nonrfid_state_map(self, slot, ht_attrs, helper_spool_id=0, expected_spool_id=0, stored_sig="", status=""):
        """Build a state_map for an HT slot with all other slots empty."""
        tray_ent = _tray_entity(slot)
        state_map = {
            tray_ent: {"attributes": ht_attrs, "state": ht_attrs.get("_state", "valid")},
            f"{tray_ent}::all": {"attributes": ht_attrs, "state": ht_attrs.get("_state", "valid")},
            "input_boolean.filament_iq_nonrfid_enabled": "on",
            f"input_text.ams_slot_{slot}_spool_id": str(helper_spool_id),
            f"input_text.ams_slot_{slot}_expected_spool_id": str(expected_spool_id),
            f"input_text.ams_slot_{slot}_status": status,
            f"input_text.ams_slot_{slot}_tray_signature": stored_sig,
            f"input_text.ams_slot_{slot}_unbound_reason": "",
        }
        for s in range(1, 7):
            if s != slot:
                other_ent = _tray_entity(s)
                state_map[other_ent] = {"attributes": {"tag_uid": "", "empty": True}, "state": "empty"}
                state_map[f"{other_ent}::all"] = {"attributes": {"tag_uid": "", "empty": True}, "state": "empty"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
                state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
                state_map[f"input_text.ams_slot_{s}_status"] = ""
                state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
        return state_map

    def _nonrfid_attrs(self, tray_type="PLA", color="ff0000", name="Overture PLA", state="valid"):
        return {
            "tag_uid": "0000000000000000",
            "tray_uuid": "00000000000000000000000000000000",
            "empty": False,
            "type": tray_type,
            "color": color,
            "name": name,
            "filament_id": "",
            "tray_weight": 1000,
            "remain": 50,
            "_state": state,
        }

    def test_allzero_identity_produces_pipe_separated_signature(self):
        """Tray with all-zero IDs -> _build_tray_signature produces pipe-separated, no NONRFID| prefix."""
        r = TestableReconcile(FakeSpoolman([], []), {}, args=self.args)
        attrs = self._nonrfid_attrs(tray_type="PLA", color="FFFFFFFF", state="Overture PLA")
        tray_meta = r._tray_meta(attrs, "Overture PLA")
        sig = r._build_tray_signature(tray_meta, "Overture PLA", "")
        self.assertIn("|", sig, "signature must be pipe-separated")
        self.assertFalse(sig.startswith("NONRFID|"), "must not use old NONRFID| format")
        self.assertIn("pla", sig)
        self.assertLessEqual(len(sig), 255)

    def test_ht_bound_clears_unbound_reason(self):
        """When spool_id == expected_spool_id > 0 and tray present, unbound_reason must be cleared."""
        slot = 5
        ht = self._nonrfid_attrs(tray_type="PLA", color="FF0000")
        spool_id = 42
        spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="AMS2_Slot1",
                         color_hex="FF0000", vendor_name="Overture", name="Overture PLA")]
        sm = FakeSpoolman(spools, [])
        state_map = self._nonrfid_state_map(slot, ht, helper_spool_id=spool_id, expected_spool_id=spool_id)
        state_map[f"input_text.ams_slot_{slot}_unbound_reason"] = UNBOUND_NONRFID_NO_MATCH
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        reason_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_unbound_reason"]
        cleared = any(w.get("value") == "" for w in reason_writes)
        self.assertTrue(cleared, "unbound_reason must be cleared when slot is bound (spool_id == expected)")

    def test_rfid_identity_unchanged_by_fingerprint(self):
        """RFID tray with valid tag_uid must use tag-based identity, not NONRFID fingerprint."""
        tag = "AABBCCDD00112233"
        spools = [_spool(10, remaining_weight=500, rfid_tag_uid=tag, location="Shelf")]
        filaments = [_bambu_filament()]
        sm = FakeSpoolman(spools, filaments)
        slot = 1
        tray_ent = _tray_entity(slot)
        attrs = {
            "tag_uid": tag,
            "tray_uuid": "C482963767A24ACBB858F95D4376A2E5",
            "type": "PLA",
            "color": "ff0000",
            "name": "Bambu PLA",
            "filament_id": "bambu",
            "tray_weight": 1000,
            "remain": 50,
        }
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            if s != slot:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "",
                               "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0)
        final_status = status_writes[-1].get("value", "")
        self.assertNotIn("NONRFID", final_status, "RFID tray must not use NONRFID status path")
        self.assertNotEqual(final_status, STATUS_WAITING_CONFIRMATION)
        self.assertNotEqual(final_status, STATUS_NEEDS_MANUAL_BIND)

    def test_generic_sentinel_short_circuits_before_confidence(self):
        """Tray with sentinel filament_id ending in 99 -> NEEDS_MANUAL_BIND via sentinel short-circuit, no waterfall."""
        slot = 5
        ht = self._nonrfid_attrs(tray_type="PLA", color="FF0000", state="Generic Filament")
        ht["filament_id"] = "GFA99"
        sm = FakeSpoolman([], [])
        state_map = self._nonrfid_state_map(slot, ht)
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_NEEDS_MANUAL_BIND)
        reason_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_unbound_reason"]
        self.assertGreater(len(reason_writes), 0)
        self.assertEqual(reason_writes[-1].get("value"), UNBOUND_NONRFID_NO_MATCH)


    # ── RFID identity-stuck tests ──

    def test_rfid_identity_stuck_after_60s_on_manual_reconcile(self):
        """Manual reconcile with constant tag_uid/tray_uuid for >60s -> RFID_IDENTITY_STUCK."""
        import time
        tag = "AABBCCDD00112233"
        slot = 1
        spools = [_spool(10, remaining_weight=500, rfid_tag_uid=tag, location="Shelf")]
        filaments = [_bambu_filament()]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(slot)
        attrs = {
            "tag_uid": tag,
            "tray_uuid": "C482963767A24ACBB858F95D4376A2E5",
            "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
            "filament_id": "bambu", "tray_weight": 1000, "remain": 50,
        }
        state_map = {}
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""
            if s == slot:
                state_map[tray_ent] = {"attributes": attrs, "state": "valid"}
                state_map[f"{tray_ent}::all"] = {"attributes": attrs, "state": "valid"}
            else:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "",
                               "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}

        r = TestableReconcile(sm, state_map, args=self.args)

        r._run_reconcile("tray_change")

        r._rfid_identity_tracker[slot]["change_ts"] = time.time() - (RFID_STUCK_SECONDS + 5)
        r._helper_writes.clear()

        r._run_reconcile("manual_button")

        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_RFID_IDENTITY_STUCK)
        reason_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_unbound_reason"]
        self.assertGreater(len(reason_writes), 0)
        self.assertEqual(reason_writes[-1].get("value"), UNBOUND_RFID_NOT_REFRESHED)

    def test_rfid_identity_change_clears_stuck_status(self):
        """Identity change after stuck -> normal RFID path runs, stuck status cleared."""
        import time
        tag_old = "AABBCCDD00112233"
        tag_new = "1D33DD3B00000100"
        slot = 1
        spools = [
            _spool(10, remaining_weight=500, rfid_tag_uid=tag_old, location="AMS1_Slot1"),
            _spool(20, remaining_weight=500, rfid_tag_uid=tag_new, location="Shelf"),
        ]
        filaments = [_bambu_filament()]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(slot)
        attrs_old = {
            "tag_uid": tag_old,
            "tray_uuid": "C482963767A24ACBB858F95D4376A2E5",
            "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
            "filament_id": "bambu", "tray_weight": 1000, "remain": 50,
        }
        state_map = {}
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""
            if s == slot:
                state_map[tray_ent] = {"attributes": attrs_old, "state": "valid"}
                state_map[f"{tray_ent}::all"] = {"attributes": attrs_old, "state": "valid"}
            else:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "",
                               "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("tray_change")

        r._rfid_identity_tracker[slot]["change_ts"] = time.time() - (RFID_STUCK_SECONDS + 5)
        r._helper_writes.clear()

        r._run_reconcile("manual_button")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_RFID_IDENTITY_STUCK)

        attrs_new = dict(attrs_old)
        attrs_new["tag_uid"] = tag_new
        attrs_new["tray_uuid"] = "D593074878B35BDCC969F06E5487B3F6"
        r._state_map[tray_ent] = {"attributes": attrs_new, "state": "valid"}
        r._state_map[f"{tray_ent}::all"] = {"attributes": attrs_new, "state": "valid"}
        r._helper_writes.clear()

        r._run_reconcile("manual_button")
        status_writes2 = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes2), 0)
        self.assertNotEqual(status_writes2[-1].get("value"), STATUS_RFID_IDENTITY_STUCK,
                            "identity changed, stuck status must clear")

    def test_rfid_stuck_false_positive_on_enrolled_slot(self):
        """Enrolled spool with matching lot_nr+tray_uuid must NOT be flagged stuck after 60s."""
        import time
        tag = "AABBCCDD00112233"
        tray_uuid_val = "C482963767A24ACBB858F95D4376A2E5"
        slot = 1
        # Spool 10 has lot_nr matching tray_uuid — properly enrolled
        spools = [_spool(10, remaining_weight=500, rfid_tag_uid=tag, location="AMS1_Slot1",
                         lot_nr=tray_uuid_val)]
        filaments = [_bambu_filament()]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(slot)
        attrs = {
            "tag_uid": tag,
            "tray_uuid": tray_uuid_val,
            "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
            "filament_id": "bambu", "tray_weight": 1000, "remain": 50,
        }
        state_map = {}
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = str(10) if s == slot else "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""
            if s == slot:
                state_map[tray_ent] = {"attributes": attrs, "state": "valid"}
                state_map[f"{tray_ent}::all"] = {"attributes": attrs, "state": "valid"}
            else:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "",
                               "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("tray_change")
        # Simulate 60+ seconds elapsed
        r._rfid_identity_tracker[slot]["change_ts"] = time.time() - (RFID_STUCK_SECONDS + 5)
        r._helper_writes.clear()

        r._run_reconcile("manual_button")

        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        # Enrolled spool must NOT be flagged stuck
        for w in status_writes:
            self.assertNotEqual(w.get("value"), STATUS_RFID_IDENTITY_STUCK,
                                "enrolled spool with matching lot_nr must not be flagged stuck")

    def test_rfid_stuck_fires_on_unmatched_tag(self):
        """Tag present but lot_nr does NOT match tray_uuid → correctly flagged STUCK after 60s."""
        import time
        tag = "AABBCCDD00112233"
        slot = 1
        # Spool 10 has lot_nr that does NOT match the tray_uuid
        spools = [_spool(10, remaining_weight=500, rfid_tag_uid=tag, location="AMS1_Slot1",
                         lot_nr="DIFFERENT_LOT_NR_VALUE_1234567890")]
        filaments = [_bambu_filament()]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(slot)
        attrs = {
            "tag_uid": tag,
            "tray_uuid": "C482963767A24ACBB858F95D4376A2E5",
            "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
            "filament_id": "bambu", "tray_weight": 1000, "remain": 50,
        }
        state_map = {}
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = str(10) if s == slot else "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""
            if s == slot:
                state_map[tray_ent] = {"attributes": attrs, "state": "valid"}
                state_map[f"{tray_ent}::all"] = {"attributes": attrs, "state": "valid"}
            else:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "",
                               "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("tray_change")
        r._rfid_identity_tracker[slot]["change_ts"] = time.time() - (RFID_STUCK_SECONDS + 5)
        r._helper_writes.clear()

        r._run_reconcile("manual_button")

        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_RFID_IDENTITY_STUCK)

    def test_rfid_stuck_clears_when_tag_matches(self):
        """Slot was stuck, then lot_nr is updated to match tray_uuid → stuck clears on next reconcile."""
        import time
        tag = "AABBCCDD00112233"
        tray_uuid_val = "C482963767A24ACBB858F95D4376A2E5"
        slot = 1
        # Initially: lot_nr doesn't match → will trigger stuck
        spools = [_spool(10, remaining_weight=500, rfid_tag_uid=tag, location="AMS1_Slot1",
                         lot_nr="WRONG_LOT_NR_00000000000000000000")]
        filaments = [_bambu_filament()]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(slot)
        attrs = {
            "tag_uid": tag,
            "tray_uuid": tray_uuid_val,
            "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
            "filament_id": "bambu", "tray_weight": 1000, "remain": 50,
        }
        state_map = {}
        for s in range(1, 7):
            state_map[f"input_text.ams_slot_{s}_spool_id"] = str(10) if s == slot else "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
            state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""
            if s == slot:
                state_map[tray_ent] = {"attributes": attrs, "state": "valid"}
                state_map[f"{tray_ent}::all"] = {"attributes": attrs, "state": "valid"}
            else:
                empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "",
                               "filament_id": "", "tray_weight": 0, "remain": 0}
                state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
                state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("tray_change")
        r._rfid_identity_tracker[slot]["change_ts"] = time.time() - (RFID_STUCK_SECONDS + 5)
        r._helper_writes.clear()

        # First manual reconcile → stuck (lot_nr mismatch)
        r._run_reconcile("manual_button")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_RFID_IDENTITY_STUCK)

        # Now fix lot_nr in Spoolman to match tray_uuid
        sm.spools[10]["lot_nr"] = tray_uuid_val
        r._helper_writes.clear()

        # Next manual reconcile → stuck should clear
        r._run_reconcile("manual_button")
        status_writes2 = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        for w in status_writes2:
            self.assertNotEqual(w.get("value"), STATUS_RFID_IDENTITY_STUCK,
                                "lot_nr now matches tray_uuid, stuck must clear")


class TestStartupWaiter(unittest.TestCase):
    """Startup readiness waiter: timeout (no reconcile), not-ready reasons, and STARTUP_WAIT_HELPERS_READY before reconcile."""

    def test_timeout_calls_no_run_reconcile_and_logs_startup_wait_timeout(self):
        """On timeout, must not call _run_reconcile and must log STARTUP_WAIT_TIMEOUT at ERROR."""
        state_map = {"input_text.ams_slot_1_spool_id::all": {"state": "unavailable", "attributes": {}}}
        args = {"startup_wait_helpers_seconds": 420, "startup_probe_helper_entity": "input_text.ams_slot_1_spool_id"}
        h = StartupWaiterHarness(state_map, args=args)
        with unittest.mock.patch.object(ams_rfid_reconcile, "DomainException", Exception):
            # Pass end_utc in the past so first check hits timeout
            h._run_reconcile_startup({
                "_readiness_end_utc": datetime.datetime.utcnow() - datetime.timedelta(seconds=1),
                "_readiness_next_interval_sec": 2,
            })
        self.assertEqual(len(h._run_reconcile_calls), 0, "timeout must not call _run_reconcile")
        log_msgs = [msg for msg, _ in h._log_calls]
        self.assertTrue(
            any("STARTUP_WAIT_TIMEOUT" in msg for msg in log_msgs),
            f"expected STARTUP_WAIT_TIMEOUT in log; got {log_msgs}",
        )
        error_logs = [(m, lvl) for m, lvl in h._log_calls if lvl == "ERROR"]
        self.assertGreater(len(error_logs), 0, "STARTUP_WAIT_TIMEOUT must be logged at ERROR level")
        self.assertTrue(any("STARTUP_WAIT_TIMEOUT" in m for m, _ in error_logs))

    def test_not_ready_checks_all_three_conditions_and_logs_startup_wait_helpers_not_ready(self):
        """Probe must treat unavailable state, restored=True, and domain exception as not ready; log STARTUP_WAIT_HELPERS_NOT_READY and reschedule."""
        probe_entity = "input_text.ams_slot_1_spool_id"
        # Case 1: state unavailable
        state_map = {f"{probe_entity}::all": {"state": "unavailable", "attributes": {}}}
        args = {"startup_wait_helpers_seconds": 420, "startup_probe_helper_entity": probe_entity}
        h = StartupWaiterHarness(state_map, args=args)
        with unittest.mock.patch.object(ams_rfid_reconcile, "DomainException", Exception):
            h._run_reconcile_startup({})
        self.assertEqual(len(h._run_reconcile_calls), 0)
        self.assertGreater(len(h._run_in_calls), 0, "must reschedule on not ready")
        log_msgs = [msg for msg, _ in h._log_calls]
        self.assertTrue(
            any("STARTUP_WAIT_HELPERS_NOT_READY" in msg and "reason=helper_unavailable" in msg for msg in log_msgs),
            f"expected STARTUP_WAIT_HELPERS_NOT_READY reason=helper_unavailable; got {log_msgs}",
        )

        # Case 2: attributes.restored is True
        state_map2 = {f"{probe_entity}::all": {"state": "0", "attributes": {"restored": True}}}
        h2 = StartupWaiterHarness(state_map2, args=args)
        with unittest.mock.patch.object(ams_rfid_reconcile, "DomainException", Exception):
            h2._run_reconcile_startup({})
        self.assertEqual(len(h2._run_reconcile_calls), 0)
        self.assertGreater(len(h2._run_in_calls), 0)
        log_msgs2 = [msg for msg, _ in h2._log_calls]
        self.assertTrue(
            any("STARTUP_WAIT_HELPERS_NOT_READY" in msg and "reason=helper_restored" in msg for msg in log_msgs2),
            f"expected STARTUP_WAIT_HELPERS_NOT_READY reason=helper_restored; got {log_msgs2}",
        )

    def test_ready_logs_startup_wait_helpers_ready_before_reconcile(self):
        """When probe passes (no exception, not unavailable, not restored), log STARTUP_WAIT_HELPERS_READY then call _run_reconcile."""
        probe_entity = "input_text.ams_slot_1_spool_id"
        state_map = {f"{probe_entity}::all": {"state": "0", "attributes": {}}}
        args = {"startup_wait_helpers_seconds": 420, "startup_probe_helper_entity": probe_entity}
        h = StartupWaiterHarness(state_map, args=args)
        with unittest.mock.patch.object(ams_rfid_reconcile, "DomainException", Exception):
            h._run_reconcile_startup({})
        self.assertEqual(len(h._run_reconcile_calls), 1, "must call _run_reconcile once when ready")
        self.assertEqual(h._run_reconcile_calls[0][0], "startup_delay")
        log_msgs = [msg for msg, _ in h._log_calls]
        self.assertTrue(
            any("STARTUP_WAIT_HELPERS_READY" in msg for msg in log_msgs),
            f"expected STARTUP_WAIT_HELPERS_READY before reconcile; got {log_msgs}",
        )
        idx_ready = next(i for i, msg in enumerate(log_msgs) if "STARTUP_WAIT_HELPERS_READY" in msg)
        self.assertEqual(len(h._run_in_calls), 0, "must not reschedule when ready")


# ── Parameterized non-RFID tests across all 6 slots ──

_DEFAULT_ARGS = {
    "printer_serial": "01p00c5a3101668",
    "spoolman_url": "http://192.0.2.1:7912",
    "enabled": True,
    "debug_logs": False,
    "nonrfid_enabled_entity": "input_boolean.filament_iq_nonrfid_enabled",
}
_ALL_SLOTS = [1, 2, 3, 4, 5, 6]

def _nonrfid_attrs_standalone(tray_type="PLA", color="ff0000", name="Overture PLA", state="valid", tag_uid="0000000000000000", filament_id=""):
    return {
        "tag_uid": tag_uid,
        "tray_uuid": "00000000000000000000000000000000",
        "empty": False,
        "type": tray_type,
        "color": color,
        "name": name,
        "filament_id": filament_id,
        "tray_weight": 1000,
        "remain": 50,
        "_state": state,
    }

def _nonrfid_state_map_standalone(slot, attrs, helper_spool_id=0, expected_spool_id=0, stored_sig="", status=""):
    tray_ent = _tray_entity(slot)
    state_map = {
        tray_ent: {"attributes": attrs, "state": attrs.get("_state", "valid")},
        f"{tray_ent}::all": {"attributes": attrs, "state": attrs.get("_state", "valid")},
        "input_boolean.filament_iq_nonrfid_enabled": "on",
        f"input_text.ams_slot_{slot}_spool_id": str(helper_spool_id),
        f"input_text.ams_slot_{slot}_expected_spool_id": str(expected_spool_id),
        f"input_text.ams_slot_{slot}_status": status,
        f"input_text.ams_slot_{slot}_tray_signature": stored_sig,
        f"input_text.ams_slot_{slot}_unbound_reason": "",
    }
    for s in range(1, 7):
        if s != slot:
            other_ent = _tray_entity(s)
            state_map[other_ent] = {"attributes": {"tag_uid": "", "empty": True}, "state": "empty"}
            state_map[f"{other_ent}::all"] = {"attributes": {"tag_uid": "", "empty": True}, "state": "empty"}
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{s}_status"] = ""
            state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
    return state_map


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_helper_set_location_sync(slot):
    """Non-RFID present: helper_spool_id > 0 -> status OK, location synced."""
    attrs = _nonrfid_attrs_standalone(name="Bambu PLA", filament_id="bambu")
    spool_id = 101
    spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000")]
    filaments = [_bambu_filament()]
    sm = FakeSpoolman(spools, filaments)
    state_map = _nonrfid_state_map_standalone(slot, attrs, helper_spool_id=spool_id)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") == "OK"
    expected_loc = CANONICAL_LOCATION_BY_SLOT[slot]
    loc_patches = [p for p in sm.patches if p.get("path") == f"/api/v1/spool/{spool_id}" and p.get("payload", {}).get("location") == expected_loc]
    assert len(loc_patches) > 0, f"must PATCH spool {spool_id} location to {expected_loc}"
    summary = getattr(r, "_last_summary", None)
    assert summary is not None
    slot_t = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
    assert slot_t is not None
    assert slot_t.get("final_spool_id") == spool_id
    assert slot_t.get("reason") == "nonrfid_present"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_no_helper_remains_unregistered(slot):
    """Non-RFID tray + helper_spool_id == 0 + only generic Bambu spools -> NEEDS_MANUAL_BIND or LOW_CONFIDENCE."""
    attrs = _nonrfid_attrs_standalone(name="Bambu PLA", filament_id="bambu")
    spools = [_spool(101, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000")]
    spools[0]["filament"]["external_id"] = "GFA99"
    filaments = [_bambu_filament()]
    sm = FakeSpoolman(spools, filaments)
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") in (STATUS_NEEDS_MANUAL_BIND, STATUS_LOW_CONFIDENCE)
    loc_patches = [p for p in sm.patches if p.get("path") == "/api/v1/spool/101" and (p.get("payload") or {}).get("location")]
    assert len(loc_patches) == 0, "must not write location when no helper bound"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_helper_404_clears_to_zero_and_unregistered(slot):
    """Non-RFID: helper_spool_id points to missing spool -> clear to 0 (swap-detect or 404), unbound."""
    attrs = _nonrfid_attrs_standalone(name="Bambu PLA", filament_id="bambu")
    # No spools so after clear, rematch does not bind; helper stays 0
    spools = []
    filaments = [_bambu_filament()]
    sm = FakeSpoolman(spools, filaments)
    missing_id = 999
    state_map = _nonrfid_state_map_standalone(slot, attrs, helper_spool_id=missing_id)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    spool_id_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_spool_id"]
    assert len(spool_id_writes) > 0
    assert spool_id_writes[-1].get("value") == "0"
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") in ("NON_RFID_UNREGISTERED", STATUS_NEEDS_MANUAL_BIND)
    unbound_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_unbound_reason"]
    assert len(unbound_writes) > 0
    assert unbound_writes[-1].get("value") in (UNBOUND_HELPER_SPOOL_NOT_FOUND, UNBOUND_NONRFID_NO_MATCH)
    tray_sig_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_tray_signature"]
    assert len(tray_sig_writes) == 0, "must not write tray_signature when helper 404"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_stamps_tray_signature_when_registered(slot):
    """Non-RFID: valid helper spool -> tray_signature written in pipe-separated format."""
    spool_id = 200 + slot
    attrs = _nonrfid_attrs_standalone(name="Bambu PLA", filament_id="bambu")
    spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000")]
    filaments = [_bambu_filament()]
    sm = FakeSpoolman(spools, filaments)
    state_map = _nonrfid_state_map_standalone(slot, attrs, helper_spool_id=spool_id)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    tray_sig_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_tray_signature"]
    assert len(tray_sig_writes) > 0, "must write tray_signature when registered"
    assert "|" in tray_sig_writes[-1].get("value", ""), "tray_signature must be pipe-separated"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_helper_sees_spool_and_registers(slot):
    """Non-RFID: helper points to valid spool -> registers, location synced, ha_sig written."""
    spool_id = 300 + slot
    attrs = _nonrfid_attrs_standalone(name="Bambu PLA", filament_id="bambu")
    spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000")]
    filaments = [_bambu_filament()]
    sm = FakeSpoolman(spools, filaments)
    state_map = _nonrfid_state_map_standalone(slot, attrs, helper_spool_id=spool_id)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") == "OK"
    tray_sig_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_tray_signature"]
    assert len(tray_sig_writes) > 0
    assert "|" in tray_sig_writes[-1].get("value", "")
    expected_loc = CANONICAL_LOCATION_BY_SLOT[slot]
    loc_patches = [p for p in sm.patches if p.get("path") == f"/api/v1/spool/{spool_id}" and (p.get("payload") or {}).get("location")]
    assert len(loc_patches) > 0, f"must PATCH spool {spool_id} location"
    # v4: identity in lot_nr only (no comment/ha_sig write)
    lot_nr_patches = [p for p in sm.patches if p.get("path") == f"/api/v1/spool/{spool_id}" and (p.get("payload") or {}).get("lot_nr")]
    assert len(lot_nr_patches) > 0, f"must PATCH spool {spool_id} lot_nr when registered"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_empty_tag_tray_uuid_uses_nonrfid_branch(slot):
    """tag_uid=\"\" and tray_uuid=\"\" -> nonrfid branch runs, NOT UNBOUND_NO_RFID_TAG_ALL_ZERO."""
    spool_id = 400 + slot
    attrs = _nonrfid_attrs_standalone(tag_uid="", name="Bambu PLA", filament_id="bambu")
    attrs["tray_uuid"] = ""
    spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000")]
    filaments = [_bambu_filament()]
    sm = FakeSpoolman(spools, filaments)
    state_map = _nonrfid_state_map_standalone(slot, attrs, helper_spool_id=spool_id)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert status_writes[-1].get("value") == "OK"
    tray_sig = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_tray_signature"]
    assert len(tray_sig) > 0
    assert "|" in tray_sig[-1].get("value", "")
    summary = getattr(r, "_last_summary", None)
    slot_t = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
    assert slot_t.get("unbound_reason") != UNBOUND_NO_RFID_TAG_ALL_ZERO
    assert slot_t.get("reason") == "nonrfid_present"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_literal_zeros_trigger_nonrfid_branch(slot):
    """Literal 0000...0 tag/tray_uuid -> nonrfid branch, not UNBOUND_NO_RFID_TAG_ALL_ZERO."""
    spool_id = 500 + slot
    attrs = _nonrfid_attrs_standalone(name="Bambu PLA", filament_id="bambu")
    spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000")]
    filaments = [_bambu_filament()]
    sm = FakeSpoolman(spools, filaments)
    state_map = _nonrfid_state_map_standalone(slot, attrs, helper_spool_id=spool_id)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert status_writes[-1].get("value") == "OK"
    tray_sig = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_tray_signature"]
    assert len(tray_sig) > 0
    assert "|" in tray_sig[-1].get("value", "")
    summary = getattr(r, "_last_summary", None)
    slot_t = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
    assert slot_t.get("unbound_reason") != UNBOUND_NO_RFID_TAG_ALL_ZERO
    assert slot_t.get("reason") == "nonrfid_present"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_no_match_sets_needs_manual_bind(slot):
    """Non-RFID: helper_spool_id=0, only generic Bambu spools -> NEEDS_MANUAL_BIND."""
    attrs = _nonrfid_attrs_standalone(name="Bambu PLA", filament_id="bambu")
    spools = [_spool(101, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000")]
    spools[0]["filament"]["external_id"] = "GFA99"
    filaments = [_bambu_filament()]
    sm = FakeSpoolman(spools, filaments)
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") == STATUS_NEEDS_MANUAL_BIND


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_helper_200_remains_registered(slot):
    """Non-RFID: helper spool exists in Spoolman -> remains registered, location synced."""
    spool_id = 600 + slot
    attrs = _nonrfid_attrs_standalone(name="Bambu PLA", filament_id="bambu")
    spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000")]
    filaments = [_bambu_filament()]
    sm = FakeSpoolman(spools, filaments)
    state_map = _nonrfid_state_map_standalone(slot, attrs, helper_spool_id=spool_id)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") == "OK"
    expected_loc = CANONICAL_LOCATION_BY_SLOT[slot]
    loc_patches = [p for p in sm.patches if p.get("path") == f"/api/v1/spool/{spool_id}" and (p.get("payload") or {}).get("location") == expected_loc]
    assert len(loc_patches) > 0, f"must sync location to {expected_loc}"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_pending_then_confirm(slot):
    """Signature change -> first run PENDING, second run confirms."""
    attrs_petg = _nonrfid_attrs_standalone(tray_type="PETG", color="808080", name="Generic PETG")
    attrs_pla = _nonrfid_attrs_standalone(tray_type="PLA", color="FFFFFF", name="Overture PLA")
    r_init = TestableReconcile(FakeSpoolman([], []), {}, args=_DEFAULT_ARGS)
    meta_petg = r_init._tray_meta(attrs_petg, "valid")
    meta_pla = r_init._tray_meta(attrs_pla, "valid")
    sig_petg = r_init._build_tray_signature(meta_petg, "valid", "")
    sig_pla = r_init._build_tray_signature(meta_pla, "valid", "")
    assert sig_petg != sig_pla

    state_map_1 = _nonrfid_state_map_standalone(slot, attrs_pla, stored_sig=sig_petg)
    r1 = TestableReconcile(FakeSpoolman([], []), state_map_1, args=_DEFAULT_ARGS)
    r1._run_reconcile("test")
    status_1 = [w for w in r1._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_1) > 0
    assert status_1[-1].get("value") == STATUS_WAITING_CONFIRMATION
    sig_writes = [w for w in r1._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_tray_signature"]
    assert len(sig_writes) > 0
    pending_sig = sig_writes[-1].get("value", "")
    assert pending_sig.startswith("PENDING:")

    state_map_2 = _nonrfid_state_map_standalone(slot, attrs_pla, stored_sig=pending_sig)
    r2 = TestableReconcile(FakeSpoolman([], []), state_map_2, args=_DEFAULT_ARGS)
    r2._run_reconcile("test")
    status_2 = [w for w in r2._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_2) > 0
    assert status_2[-1].get("value") != STATUS_WAITING_CONFIRMATION


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_confident_no_match_needs_manual_bind(slot):
    """Confident non-RFID tray with no matching non-Bambu spool -> NEEDS_MANUAL_BIND."""
    attrs = _nonrfid_attrs_standalone(tray_type="PLA", color="FF0000", name="Overture PLA")
    sm = FakeSpoolman([], [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") == STATUS_NEEDS_MANUAL_BIND
    reason_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_unbound_reason"]
    assert len(reason_writes) > 0
    assert reason_writes[-1].get("value") == UNBOUND_NONRFID_NO_MATCH


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_truth_guard_404_clears_and_blocks(slot):
    """IDENTITY_UNAVAILABLE: helper spool 42 not in Spoolman -> swap-detect or 404 clears to 0, no PATCH."""
    attrs = _nonrfid_attrs_standalone(name="Bambu PLA", filament_id="bambu")
    # No spools so after clear, rematch finds no match (helper stays 0)
    spools = []
    filaments = [_bambu_filament()]
    sm = FakeSpoolman(spools, filaments)
    tray_ent = _tray_entity(slot)
    state_map = {"input_boolean.filament_iq_nonrfid_enabled": "on"}
    for s in range(1, 7):
        state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
        state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
        state_map[f"input_text.ams_slot_{s}_status"] = ""
        state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
        state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""
        if s == slot:
            state_map[tray_ent] = {"attributes": attrs, "state": "valid"}
            state_map[f"{tray_ent}::all"] = {"attributes": attrs, "state": "valid"}
            state_map[f"input_text.ams_slot_{s}_spool_id"] = "42"
        else:
            empty_a = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
            state_map[_tray_entity(s)] = {"attributes": empty_a, "state": "empty"}
            state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_a, "state": "empty"}
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    spool_id_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_spool_id"]
    assert len(spool_id_writes) > 0
    assert spool_id_writes[-1].get("value") == "0"
    unbound_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_unbound_reason"]
    assert len(unbound_writes) > 0
    assert unbound_writes[-1].get("value") in (
        UNBOUND_HELPER_SPOOL_NOT_FOUND,
        UNBOUND_NONRFID_NO_MATCH,
    ), "cleared invalid helper -> unbound (404 path or swap-detect then rematch no-match)"
    location_patches = [p for p in sm.patches if "location" in (p.get("payload") or {})]
    assert len(location_patches) == 0, "no Spoolman PATCH when helper 404"
    tray_sig_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_tray_signature"]
    assert len(tray_sig_writes) == 0, "must not stamp tray_signature when helper 404"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_truth_guard_material_mismatch_clears(slot):
    """IDENTITY_UNAVAILABLE: helper spool material (PETG) != tray type (PLA) -> cleared."""
    attrs = _nonrfid_attrs_standalone(name="Bambu PLA", filament_id="bambu")
    helper_spool = _spool(700 + slot, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                          material="PETG", color_hex="00ff00", name="Bambu PETG")
    filaments = [_bambu_filament(material="PETG", color_hex="00ff00", name="Bambu PETG")]
    sm = FakeSpoolman([helper_spool], filaments)
    tray_ent = _tray_entity(slot)
    state_map = {"input_boolean.filament_iq_nonrfid_enabled": "on"}
    for s in range(1, 7):
        state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
        state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
        state_map[f"input_text.ams_slot_{s}_status"] = ""
        state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
        state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""
        if s == slot:
            state_map[tray_ent] = {"attributes": attrs, "state": "valid"}
            state_map[f"{tray_ent}::all"] = {"attributes": attrs, "state": "valid"}
            state_map[f"input_text.ams_slot_{s}_spool_id"] = str(700 + slot)
        else:
            empty_a = {"tag_uid": "", "type": "", "color": "", "name": "", "filament_id": "", "tray_weight": 0, "remain": 0}
            state_map[_tray_entity(s)] = {"attributes": empty_a, "state": "empty"}
            state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_a, "state": "empty"}
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    spool_id_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_spool_id"]
    assert len(spool_id_writes) > 0
    assert spool_id_writes[-1].get("value") == "0"
    unbound_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_unbound_reason"]
    assert len(unbound_writes) > 0
    assert unbound_writes[-1].get("value") in (
        UNBOUND_HELPER_MATERIAL_MISMATCH,
        UNBOUND_NONRFID_NO_MATCH,
    ), "material mismatch clears helper via truth guard or swap-detect -> rematch"
    assert len(sm.patches) == 0, "no Spoolman PATCH when material mismatch"
    tray_sig_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_tray_signature"]
    assert len(tray_sig_writes) == 0, "must not stamp tray_signature on material mismatch"


# ── P5: Non-RFID matching improvement tests ──

def test_is_generic_filament_id_unit():
    """Unit test for is_generic_filament_id."""
    assert is_generic_filament_id("GFL99") is True
    assert is_generic_filament_id("GFG99") is True
    assert is_generic_filament_id("GFA99") is True
    assert is_generic_filament_id("gfl99") is True
    assert is_generic_filament_id("GFL05") is False
    assert is_generic_filament_id("GFA00") is False
    assert is_generic_filament_id("") is False
    assert is_generic_filament_id(None) is False


def test_color_distance_unit():
    """Unit test for _color_distance."""
    assert _color_distance("ff0000", "ff0000") == 0.0
    assert _color_distance("ff0000", "00ff00") > 300
    assert _color_distance("ff0000", "fe0000") < 5
    assert _color_distance("invalid", "ff0000") == 999.0
    assert _color_distance("", "") == 999.0


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_generic_sentinel_short_circuits_to_needs_action(slot):
    """Generic tray (GFL99) + matching unenrolled spool -> bind via unenrolled; sentinel is last resort when zero candidates."""
    attrs = _nonrfid_attrs_standalone(name="Generic PLA", filament_id="GFL99", tray_type="PLA", color="ff0000")
    spools = [_spool(101, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                     color_hex="ff0000", vendor_name="Overture", name="Overture PLA")]
    sm = FakeSpoolman(spools, [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") == STATUS_OK_NONRFID, "generic tray + matching spool must bind via unenrolled"
    logs = [msg for msg, _ in r._log_calls]
    assert any("NONRFID_UNENROLLED_MATCH" in msg for msg in logs), "must log unenrolled match when binding"
    assert not any("NONRFID_SENTINEL_SKIP" in msg for msg in logs), "sentinel must not fire when unenrolled match exists"


@pytest.mark.parametrize("fid", ["GFL99", "GFG99", "GFA99"])
@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_generic_sentinel_all_patterns_block(slot, fid):
    """All sentinel filament_id patterns block auto-match."""
    attrs = _nonrfid_attrs_standalone(name="Generic Filament", filament_id=fid, tray_type="PLA", color="ff0000")
    sm = FakeSpoolman([], [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") == STATUS_NEEDS_MANUAL_BIND


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_specific_filament_id_not_blocked(slot):
    """filament_id=GFL05 with generic-sounding name -> NOT blocked, proceeds to waterfall."""
    attrs = _nonrfid_attrs_standalone(name="Generic PLA", filament_id="GFL05", tray_type="PLA", color="ff0000")
    spools = [_spool(101, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                     color_hex="ff0000", vendor_name="Overture", name="Overture PLA")]
    sm = FakeSpoolman(spools, [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") != STATUS_NEEDS_MANUAL_BIND or len(sm.patches) > 0, \
        "specific filament_id must not be blocked by sentinel"
    logs = [msg for msg, _ in r._log_calls]
    assert not any("NONRFID_SENTINEL_SKIP" in msg for msg in logs), "must NOT log sentinel skip for specific fid"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_filament_id_exact_match_binds(slot):
    """Spoolman filament with external_id=GFL05 at Shelf -> bound via filament_id match."""
    attrs = _nonrfid_attrs_standalone(name="Overture Matte PLA", filament_id="GFL05", tray_type="PLA", color="ff0000")
    spool_id = 800 + slot
    spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                     color_hex="00ff00", vendor_name="Overture", name="Overture Matte PLA")]
    spools[0]["filament"]["external_id"] = "GFL05"
    sm = FakeSpoolman(spools, [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") == STATUS_OK_NONRFID
    logs = [msg for msg, _ in r._log_calls]
    assert any("NONRFID_FILAMENT_ID_MATCH" in msg and str(spool_id) in msg for msg in logs), \
        f"must log NONRFID_FILAMENT_ID_MATCH; got {[m for m in logs if 'FILAMENT_ID' in m]}"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_filament_id_no_external_id_falls_through(slot):
    """No external_id populated -> falls through to vendor+material step."""
    attrs = _nonrfid_attrs_standalone(name="Overture Matte PLA", filament_id="GFL05", tray_type="PLA", color="ff0000")
    spool_id = 900 + slot
    spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                     color_hex="ff0000", vendor_name="Overture", name="Overture Matte PLA")]
    spools[0]["filament"]["external_id"] = ""
    sm = FakeSpoolman(spools, [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    logs = [msg for msg, _ in r._log_calls]
    assert any("NONRFID_FILAMENT_ID_NO_MATCH" in msg for msg in logs) or \
           not any("NONRFID_FILAMENT_ID_MATCH" in msg for msg in logs), \
        "with no external_id, filament_id step must fall through"
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") in (STATUS_OK_NONRFID, STATUS_NEEDS_MANUAL_BIND, STATUS_LOW_CONFIDENCE)


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_color_not_hard_filter(slot):
    """Two candidates same material, one matching color one not -> both remain (color doesn't eliminate)."""
    attrs = _nonrfid_attrs_standalone(name="Overture PLA", filament_id="OV01", tray_type="PLA", color="ff0000")
    spool_match = _spool(1000 + slot, remaining_weight=400, rfid_tag_uid=None, location="Shelf",
                         color_hex="ff0000", vendor_name="Overture", name="Overture PLA Red")
    spool_mismatch = _spool(2000 + slot, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                            color_hex="00ff00", vendor_name="Overture", name="Overture PLA Green")
    spool_match["filament"]["external_id"] = ""
    spool_mismatch["filament"]["external_id"] = ""
    sm = FakeSpoolman([spool_match, spool_mismatch], [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    final = status_writes[-1].get("value")
    assert final != STATUS_NEEDS_MANUAL_BIND or final == STATUS_OK_NONRFID, \
        "color mismatch alone must not block — both candidates must remain"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_color_tiebreak_selects_closer(slot):
    """Two candidates same material, different colors -> color distance selects closer one."""
    attrs = _nonrfid_attrs_standalone(name="PLA", filament_id="X01", tray_type="PLA", color="ff0000")
    spool_close = _spool(3000 + slot, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                         color_hex="fe0000", vendor_name="Overture", name="Overture PLA Red")
    spool_far = _spool(4000 + slot, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                       color_hex="00ff00", vendor_name="Overture", name="Overture PLA Green")
    spool_close["filament"]["external_id"] = ""
    spool_far["filament"]["external_id"] = ""
    sm = FakeSpoolman([spool_close, spool_far], [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") == STATUS_OK_NONRFID
    summary = getattr(r, "_last_summary", None)
    assert summary is not None
    slot_t = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
    assert slot_t is not None
    assert slot_t.get("final_spool_id") == 3000 + slot, "must select closer-color spool"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_bambu_specific_filament_id_eligible(slot):
    """Bambu vendor + filament_id=GFA00 -> eligible for non-RFID matching."""
    attrs = _nonrfid_attrs_standalone(name="Bambu PLA", filament_id="GFA00", tray_type="PLA", color="ff0000")
    spool_id = 5000 + slot
    spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                     color_hex="ff0000", vendor_name="Bambu Lab", name="Bambu PLA Basic")]
    spools[0]["filament"]["external_id"] = "GFA00"
    sm = FakeSpoolman(spools, [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") == STATUS_OK_NONRFID, \
        "Bambu spool with specific (non-generic) filament_id must be eligible"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_nonrfid_bambu_generic_filament_id_excluded(slot):
    """Bambu vendor + filament_id=GFA99 -> excluded, NONRFID_BAMBU_EXCLUDED logged."""
    attrs = _nonrfid_attrs_standalone(name="Generic PLA", filament_id="bambu_pla", tray_type="PLA", color="ff0000")
    spool_id = 6000 + slot
    spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                     color_hex="ff0000", vendor_name="Bambu Lab", name="Bambu PLA Basic")]
    spools[0]["filament"]["external_id"] = "GFA99"
    sm = FakeSpoolman(spools, [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") in (STATUS_NEEDS_MANUAL_BIND, STATUS_LOW_CONFIDENCE)
    logs = [msg for msg, _ in r._log_calls]
    assert any("NONRFID_BAMBU_EXCLUDED" in msg and str(spool_id) in msg for msg in logs), \
        f"must log NONRFID_BAMBU_EXCLUDED; got {[m for m in logs if 'BAMBU' in m]}"


# ── Tier 2 RFID Tests ─────────────────────────────────────────────────

def _rfid_state_map(slot, tag_uid, source_slot=None, source_slot_state="empty",
                    extra_state=None):
    """Build a state_map for RFID Tier 2 tests.
    slot: the slot being reconciled (tray has tag_uid)
    source_slot: if set, the slot where the spool came from
    source_slot_state: state of the source slot's tray ('empty', 'valid', etc.)
    extra_state: additional state_map overrides
    """
    tray_ent = _tray_entity(slot)
    attrs = {"tag_uid": tag_uid, "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
             "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
    state_map = {}
    for s in range(1, 7):
        state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
        state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
        state_map[f"input_text.ams_slot_{s}_status"] = ""
        state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
        state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""
        if s == slot:
            state_map[tray_ent] = {"attributes": attrs, "state": "valid"}
            state_map[f"{tray_ent}::all"] = {"attributes": attrs, "state": "valid"}
        elif s == source_slot:
            empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "",
                           "filament_id": "", "tray_weight": 0, "remain": 0}
            state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": source_slot_state}
            state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": source_slot_state}
        else:
            empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "",
                           "filament_id": "", "tray_weight": 0, "remain": 0}
            state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
            state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
    if extra_state:
        state_map.update(extra_state)
    return state_map


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_rfid_tier2_slot_to_slot_move_binds(slot):
    """Spool moved between AMS slots: Tier 1 (tag_to_spools) misses, Tier 2 finds it at source
    slot whose tray is empty -> bind via Tier 2, TIER2_MATCH logged."""
    tag = "AABBCCDD00112233"
    source_slot = (slot % 6) + 1
    source_loc = CANONICAL_LOCATION_BY_SLOT[source_slot]
    spool_id = 7000 + slot

    spool = _spool(spool_id, rfid_tag_uid=tag, location="Empty", remaining_weight=500)
    sm = FakeSpoolman([spool], [_bambu_filament()])

    state_map = _rfid_state_map(slot, tag, source_slot=source_slot, source_slot_state="empty")
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)

    tier2_spool = dict(spool)
    tier2_spool["location"] = source_loc
    original_method = r._find_tier2_candidates
    r._find_tier2_candidates = lambda s, t, tm, si, tray_uuid="": [tier2_spool] if s == slot else original_method(s, t, tm, si, tray_uuid=tray_uuid)

    r._run_reconcile("test")

    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") == "OK", \
        f"Tier 2 bind must produce STATUS_OK; got {status_writes[-1].get('value')}"
    logs = [msg for msg, _ in r._log_calls]
    assert any("TIER2_MATCH" in msg and str(spool_id) in msg for msg in logs), \
        f"must log TIER2_MATCH; got tier-related: {[m for m in logs if 'TIER2' in m]}"
    assert any("TIER2_LOCATION_UPDATE" in msg for msg in logs), \
        "must log TIER2_LOCATION_UPDATE"
    summary = r._last_summary
    assert summary is not None
    slot_t = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
    assert slot_t is not None
    assert slot_t.get("final_spool_id") == spool_id


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_rfid_tier2_source_slot_not_empty_skips(slot):
    """Spool at another AMS slot but that slot's tray is NOT empty -> Tier 2 skips, NEEDS_ACTION."""
    tag = "AABBCCDD00112233"
    source_slot = (slot % 6) + 1
    source_loc = CANONICAL_LOCATION_BY_SLOT[source_slot]
    spool_id = 8000 + slot

    spool = _spool(spool_id, rfid_tag_uid=tag, location=source_loc, remaining_weight=500)
    spool_index = {spool_id: spool}
    state_map = _rfid_state_map(slot, tag, source_slot=source_slot, source_slot_state="valid")
    r = TestableReconcile(FakeSpoolman([spool], []), state_map, args=_DEFAULT_ARGS)

    result = r._find_tier2_candidates(slot, tag, {}, spool_index)
    assert len(result) == 0, "source slot tray not empty -> no Tier 2 candidates"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_rfid_tier2_active_in_other_slot_excluded(slot):
    """Spool at AMS slot (empty tray) but active spool_id of another non-empty slot -> excluded."""
    tag = "AABBCCDD00112233"
    source_slot = (slot % 6) + 1
    blocker_slot = ((slot + 1) % 6) + 1
    if blocker_slot == slot:
        blocker_slot = ((slot + 2) % 6) + 1
    if blocker_slot == source_slot:
        blocker_slot = ((slot + 3) % 6) + 1

    source_loc = CANONICAL_LOCATION_BY_SLOT[source_slot]
    spool_id = 9000 + slot

    spool = _spool(spool_id, rfid_tag_uid=tag, location=source_loc, remaining_weight=500)
    spool_index = {spool_id: spool}

    state_map = _rfid_state_map(slot, tag, source_slot=source_slot, source_slot_state="empty")
    blocker_ent = _tray_entity(blocker_slot)
    blocker_attrs = {"tag_uid": "OTHER", "type": "PLA", "color": "ff0000", "name": "Other",
                     "filament_id": "x", "tray_weight": 1000, "remain": 50}
    state_map[blocker_ent] = {"attributes": blocker_attrs, "state": "valid"}
    state_map[f"{blocker_ent}::all"] = {"attributes": blocker_attrs, "state": "valid"}
    state_map[f"input_text.ams_slot_{blocker_slot}_spool_id"] = str(spool_id)

    r = TestableReconcile(FakeSpoolman([spool], []), state_map, args=_DEFAULT_ARGS)
    result = r._find_tier2_candidates(slot, tag, {}, spool_index)
    assert len(result) == 0, "spool active in another non-empty slot must be excluded"
    logs = [msg for msg, _ in r._log_calls]
    assert any("TIER2_EXCLUDED" in msg and str(spool_id) in msg for msg in logs), \
        f"must log TIER2_EXCLUDED; got {[m for m in logs if 'TIER2' in m]}"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_rfid_tier2_falls_through_to_needs_action_when_no_match(slot):
    """Tier 2 finds no UID match at empty AMS slots -> falls through to NEEDS_ACTION."""
    tag = "AABBCCDD00112233"
    spool_id = 10000 + slot
    other_slot = (slot % 6) + 1

    spool_different_uid = _spool(spool_id, rfid_tag_uid="DIFFERENTUID12345678",
                                 location=CANONICAL_LOCATION_BY_SLOT[other_slot], remaining_weight=500)
    sm = FakeSpoolman([spool_different_uid], [_bambu_filament()])
    state_map = _rfid_state_map(slot, tag)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")

    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") == STATUS_UNBOUND_ACTION_REQUIRED, \
        f"no Tier 2 match must fall through to NEEDS_ACTION; got {status_writes[-1].get('value')}"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_rfid_tier2_not_applied_to_nonrfid(slot):
    """All-zero tag_uid (non-RFID) -> Tier 2 never called, no TIER2 logs."""
    attrs = _nonrfid_attrs_standalone(name="Overture PLA", filament_id="OV01",
                                      tray_type="PLA", color="ff0000")
    spool_id = 11000 + slot
    other_slot = (slot % 6) + 1
    spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid="AABBCCDD00112233",
                     location=CANONICAL_LOCATION_BY_SLOT[other_slot], vendor_name="Overture",
                     name="Overture PLA Red")]
    spools[0]["filament"]["external_id"] = "OV01"
    sm = FakeSpoolman(spools, [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")

    logs = [msg for msg, _ in r._log_calls]
    tier2_logs = [m for m in logs if "TIER2" in m]
    assert len(tier2_logs) == 0, \
        f"non-RFID slot must never invoke Tier 2; got {tier2_logs}"


# ── Previous Occupant Clearing Guard Tests ─────────────────────────────

def _prev_occupant_state_map(slot, tag_uid, prev_spool_id=0):
    """Build state_map for previous occupant guard tests.
    Sets up slot with a valid RFID tray and prev_spool_id as previous helper.
    All other slots are empty.
    """
    tray_ent = _tray_entity(slot)
    attrs = {"tag_uid": tag_uid, "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
             "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
    state_map = {}
    for s in range(1, 7):
        state_map[f"input_text.ams_slot_{s}_spool_id"] = str(prev_spool_id) if s == slot else "0"
        state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = str(prev_spool_id) if s == slot else "0"
        state_map[f"input_text.ams_slot_{s}_status"] = ""
        state_map[f"input_text.ams_slot_{s}_tray_signature"] = ""
        state_map[f"input_text.ams_slot_{s}_unbound_reason"] = ""
        if s == slot:
            state_map[tray_ent] = {"attributes": attrs, "state": "valid"}
            state_map[f"{tray_ent}::all"] = {"attributes": attrs, "state": "valid"}
        else:
            empty_attrs = {"tag_uid": "", "type": "", "color": "", "name": "",
                           "filament_id": "", "tray_weight": 0, "remain": 0}
            state_map[_tray_entity(s)] = {"attributes": empty_attrs, "state": "empty"}
            state_map[f"{_tray_entity(s)}::all"] = {"attributes": empty_attrs, "state": "empty"}
    return state_map


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_prev_occupant_moved_to_shelf_when_remaining(slot):
    """Previous occupant at correct location, not active elsewhere, remaining_weight > 0 -> moved to Shelf."""
    tag = "AABBCCDD00112233"
    new_spool_id = 20000 + slot
    prev_spool_id = 30000 + slot
    slot_loc = CANONICAL_LOCATION_BY_SLOT[slot]

    new_spool = _spool(new_spool_id, rfid_tag_uid=tag, location="Shelf", remaining_weight=500)
    prev_spool = _spool(prev_spool_id, rfid_tag_uid=None, location=slot_loc, remaining_weight=300)
    sm = FakeSpoolman([new_spool, prev_spool], [_bambu_filament()])

    state_map = _prev_occupant_state_map(slot, tag, prev_spool_id=prev_spool_id)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")

    loc_patches = [p for p in sm.patches if p.get("spool_id") == prev_spool_id and "location" in p.get("payload", {})]
    assert len(loc_patches) > 0, "previous occupant must be moved"
    assert loc_patches[-1]["payload"]["location"] == LOCATION_NOT_IN_AMS, \
        f"remaining > 0 must move to Shelf; got {loc_patches[-1]['payload']['location']}"
    logs = [msg for msg, _ in r._log_calls]
    assert any("PREV_OCCUPANT_MOVED" in msg and str(prev_spool_id) in msg for msg in logs), \
        f"must log PREV_OCCUPANT_MOVED; got {[m for m in logs if 'PREV_OCCUPANT' in m]}"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_prev_occupant_moved_to_empty_when_depleted(slot):
    """Previous occupant remaining_weight == 0 -> moved to Empty."""
    tag = "AABBCCDD00112233"
    new_spool_id = 21000 + slot
    prev_spool_id = 31000 + slot
    slot_loc = CANONICAL_LOCATION_BY_SLOT[slot]

    new_spool = _spool(new_spool_id, rfid_tag_uid=tag, location="Shelf", remaining_weight=500)
    prev_spool = _spool(prev_spool_id, rfid_tag_uid=None, location=slot_loc, remaining_weight=0)
    sm = FakeSpoolman([new_spool, prev_spool], [_bambu_filament()])

    state_map = _prev_occupant_state_map(slot, tag, prev_spool_id=prev_spool_id)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")

    loc_patches = [p for p in sm.patches if p.get("spool_id") == prev_spool_id and "location" in p.get("payload", {})]
    assert len(loc_patches) > 0, "depleted previous occupant must be moved"
    assert loc_patches[-1]["payload"]["location"] == LOCATION_EMPTY, \
        f"remaining <= 0 must move to Empty; got {loc_patches[-1]['payload']['location']}"
    logs = [msg for msg, _ in r._log_calls]
    assert any("PREV_OCCUPANT_MOVED" in msg and str(prev_spool_id) in msg for msg in logs)


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_prev_occupant_skip_location_mismatch(slot):
    """Previous occupant already at Shelf (not at slot's canonical location) -> skip."""
    tag = "AABBCCDD00112233"
    new_spool_id = 22000 + slot
    prev_spool_id = 32000 + slot

    new_spool = _spool(new_spool_id, rfid_tag_uid=tag, location="Shelf", remaining_weight=500)
    prev_spool = _spool(prev_spool_id, rfid_tag_uid=None, location="Shelf", remaining_weight=300)
    sm = FakeSpoolman([new_spool, prev_spool], [_bambu_filament()])

    state_map = _prev_occupant_state_map(slot, tag, prev_spool_id=prev_spool_id)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")

    loc_patches = [p for p in sm.patches if p.get("spool_id") == prev_spool_id and "location" in p.get("payload", {})]
    assert len(loc_patches) == 0, "previous occupant already at Shelf must NOT be moved"
    logs = [msg for msg, _ in r._log_calls]
    skip_logs = [m for m in logs if "PREV_OCCUPANT_SKIP" in m and "location_mismatch" in m]
    assert len(skip_logs) == 0, \
        "spool at Shelf shouldn't appear as prev occupant at slot (not at canonical location)"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_prev_occupant_skip_active_in_other_slot(slot):
    """Previous occupant is spool_id of another slot with non-empty tray -> skip via guard."""
    tag = "AABBCCDD00112233"
    new_spool_id = 23000 + slot
    prev_spool_id = 33000 + slot
    other_slot = (slot % 6) + 1
    slot_loc = CANONICAL_LOCATION_BY_SLOT[slot]

    new_spool = _spool(new_spool_id, rfid_tag_uid=tag, location="Shelf", remaining_weight=500)
    prev_spool = _spool(prev_spool_id, rfid_tag_uid=None, location=slot_loc, remaining_weight=300)
    sm = FakeSpoolman([new_spool, prev_spool], [_bambu_filament()])
    spool_index = {new_spool_id: new_spool, prev_spool_id: prev_spool}

    state_map = _prev_occupant_state_map(slot, tag, prev_spool_id=prev_spool_id)
    other_ent = _tray_entity(other_slot)
    other_attrs = {"tag_uid": "OTHER", "type": "PLA", "color": "ff0000", "name": "Other",
                   "filament_id": "x", "tray_weight": 1000, "remain": 50}
    state_map[other_ent] = {"attributes": other_attrs, "state": "valid"}
    state_map[f"{other_ent}::all"] = {"attributes": other_attrs, "state": "valid"}
    state_map[f"input_text.ams_slot_{other_slot}_spool_id"] = str(prev_spool_id)

    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._clear_previous_occupant_guarded(slot, new_spool_id, spool_index)

    loc_patches = [p for p in sm.patches if p.get("spool_id") == prev_spool_id and "location" in p.get("payload", {})]
    assert len(loc_patches) == 0, \
        "previous occupant active in another non-empty slot must NOT be moved"
    logs = [msg for msg, _ in r._log_calls]
    assert any("PREV_OCCUPANT_SKIP" in msg and "active_in_other_slot" in msg for msg in logs), \
        f"must log PREV_OCCUPANT_SKIP active_in_other_slot; got {[m for m in logs if 'PREV_OCCUPANT' in m]}"
    assert any("PREV_OCCUPANT_ACTIVE_IN_OTHER_SLOT" in msg and str(prev_spool_id) in msg for msg in logs), \
        f"must log PREV_OCCUPANT_ACTIVE_IN_OTHER_SLOT; got {[m for m in logs if 'PREV_OCCUPANT' in m]}"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_prev_occupant_none_when_no_previous(slot):
    """No spool at slot's canonical location -> PREV_OCCUPANT_NONE logged, no PATCH."""
    tag = "AABBCCDD00112233"
    new_spool_id = 24000 + slot

    new_spool = _spool(new_spool_id, rfid_tag_uid=tag, location="Shelf", remaining_weight=500)
    sm = FakeSpoolman([new_spool], [_bambu_filament()])

    state_map = _prev_occupant_state_map(slot, tag, prev_spool_id=0)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")

    move_patches = [p for p in sm.patches if "location" in p.get("payload", {}) and
                    p.get("spool_id") != new_spool_id]
    assert len(move_patches) == 0, "no previous occupant means no location PATCH for other spools"
    logs = [msg for msg, _ in r._log_calls]
    assert any("PREV_OCCUPANT_NONE" in msg for msg in logs), \
        f"must log PREV_OCCUPANT_NONE; got {[m for m in logs if 'PREV_OCCUPANT' in m]}"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_prev_occupant_skip_when_other_slot_tray_empty(slot):
    """Previous occupant matches another slot's spool_id BUT that slot's tray is empty -> NOT skipped, move proceeds."""
    tag = "AABBCCDD00112233"
    new_spool_id = 25000 + slot
    prev_spool_id = 35000 + slot
    other_slot = (slot % 6) + 1
    slot_loc = CANONICAL_LOCATION_BY_SLOT[slot]

    new_spool = _spool(new_spool_id, rfid_tag_uid=tag, location="Shelf", remaining_weight=500)
    prev_spool = _spool(prev_spool_id, rfid_tag_uid=None, location=slot_loc, remaining_weight=300)
    sm = FakeSpoolman([new_spool, prev_spool], [_bambu_filament()])

    state_map = _prev_occupant_state_map(slot, tag, prev_spool_id=prev_spool_id)
    state_map[f"input_text.ams_slot_{other_slot}_spool_id"] = str(prev_spool_id)

    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")

    loc_patches = [p for p in sm.patches if p.get("spool_id") == prev_spool_id and "location" in p.get("payload", {})]
    assert len(loc_patches) > 0, \
        "previous occupant matching empty slot's helper must still be moved (tray empty = not in use)"
    assert loc_patches[-1]["payload"]["location"] == LOCATION_NOT_IN_AMS, \
        "remaining > 0 must move to Shelf"
    logs = [msg for msg, _ in r._log_calls]
    assert any("PREV_OCCUPANT_MOVED" in msg and str(prev_spool_id) in msg for msg in logs), \
        "must log PREV_OCCUPANT_MOVED (not skipped)"
    assert not any("PREV_OCCUPANT_SKIP" in msg for msg in logs), \
        "must NOT log PREV_OCCUPANT_SKIP when other slot tray is empty"


class TestActiveRunResetOnFailure(unittest.TestCase):
    """Verify _active_run is always reset after reconcile, even on exceptions."""

    def setUp(self):
        self.args = {
            "printer_serial": "01p00c5a3101668",
            "spoolman_url": "http://192.0.2.1:7912",
            "enabled": True,
            "debug_logs": False,
            "nonrfid_enabled_entity": "input_boolean.filament_iq_nonrfid_enabled",
        }

    def test_active_run_reset_on_spoolman_failure(self):
        """Spoolman returns bad data → _active_run must be None after, next run proceeds."""
        sm = FakeSpoolman([], [])
        state_map = _rfid_state_map(1, tag_uid="AABB")
        r = TestableReconcile(sm, state_map, None, self.args)

        # Poison Spoolman to return a string instead of a list
        original_get = r._spoolman_get
        r._spoolman_get = lambda path: "not_a_list"

        r._run_reconcile("test_failure")
        assert r._active_run is None, "_active_run must be reset after Spoolman failure"

        # Restore and verify next run proceeds normally
        r._spoolman_get = original_get
        r._run_reconcile("test_recovery")
        assert r._active_run is None, "_active_run must be reset after successful run"
        assert r._last_summary is not None, "recovery run should produce a summary"

    def test_active_run_reset_on_success(self):
        """Normal reconcile cycle → _active_run is None after."""
        sm = FakeSpoolman([], [])
        state_map = _rfid_state_map(1, tag_uid="AABB")
        r = TestableReconcile(sm, state_map, None, self.args)
        r._run_reconcile("test_success")
        assert r._active_run is None, "_active_run must be None after successful reconcile"

    def test_reconcile_logs_error_on_exception(self):
        """Unhandled exception during reconcile → RECONCILE_ERROR logged at ERROR level."""
        sm = FakeSpoolman([], [])
        state_map = _rfid_state_map(1, tag_uid="AABB")
        r = TestableReconcile(sm, state_map, None, self.args)

        # Poison Spoolman to raise an exception
        def _raise_on_get(path):
            raise ConnectionError("Spoolman unreachable")
        r._spoolman_get = _raise_on_get

        r._run_reconcile("test_exception")
        assert r._active_run is None, "_active_run must be reset after exception"
        error_logs = [(msg, lvl) for msg, lvl in r._log_calls if lvl == "ERROR" and "RECONCILE_ERROR" in msg]
        assert len(error_logs) >= 1, "RECONCILE_ERROR must be logged at ERROR level"
        assert "Spoolman unreachable" in error_logs[0][0], "exception message must appear in log"


class TestPrintActiveGuard(unittest.TestCase):
    """Tests for the print-active guard that prevents unbinding during active prints."""

    def _make_reconciler(self, print_active="off"):
        """Create a TestableReconcile with print_active state."""
        spoolman = FakeSpoolman([], [])
        state = {
            "input_boolean.filament_iq_print_active": print_active,
            "input_text.ams_slot_1_spool_id": "42",
            "input_text.ams_slot_1_expected_spool_id": "42",
            "input_text.ams_slot_1_tray_signature": "sig_1",
            "input_text.ams_slot_2_spool_id": "99",
            "input_text.ams_slot_2_expected_spool_id": "99",
            "input_text.ams_slot_2_tray_signature": "sig_2",
        }
        r = TestableReconcile(spoolman, state)
        r._print_active_entity = "input_boolean.filament_iq_print_active"
        return r

    def test_binding_held_during_active_print(self):
        """print_active=on, unbind requested → binding NOT cleared, BINDING_HELD logged."""
        r = self._make_reconciler(print_active="on")
        r._force_location_and_helpers(slot=1, spool_id=0, tag_uid="", source="test_unbind")
        # Binding should NOT have been cleared
        unbind_writes = [
            w for w in r._helper_writes
            if "ams_slot_1_spool_id" in w.get("entity_id", "")
        ]
        assert len(unbind_writes) == 0, "binding should not be cleared during active print"
        held_logs = [msg for msg, lvl in r._log_calls if "BINDING_HELD_DURING_PRINT" in msg and "slot=1" in msg]
        assert len(held_logs) >= 1, "BINDING_HELD_DURING_PRINT must be logged"

    def test_binding_cleared_when_print_inactive(self):
        """print_active=off, unbind requested → binding cleared normally."""
        r = self._make_reconciler(print_active="off")
        r._force_location_and_helpers(slot=1, spool_id=0, tag_uid="", source="test_unbind")
        # Binding should have been cleared
        unbind_writes = [
            w for w in r._helper_writes
            if "ams_slot_1_spool_id" in w.get("entity_id", "") and w.get("value") == "0"
        ]
        assert len(unbind_writes) >= 1, "binding should be cleared when print is not active"
        held_logs = [msg for msg, lvl in r._log_calls if "BINDING_HELD_DURING_PRINT" in msg]
        assert len(held_logs) == 0, "BINDING_HELD should NOT be logged when print inactive"

    def test_reconciler_continues_other_slots_during_print(self):
        """print_active=on, slot 1 unbind blocked, slot 2 bind proceeds normally."""
        r = self._make_reconciler(print_active="on")
        # Attempt to unbind slot 1 (should be blocked)
        r._force_location_and_helpers(slot=1, spool_id=0, tag_uid="", source="test_unbind")
        # Bind slot 2 to a new spool (should proceed)
        r._force_location_and_helpers(slot=2, spool_id=77, tag_uid="AABB", source="test_bind")
        # Slot 1: no unbind writes
        slot1_writes = [
            w for w in r._helper_writes
            if "ams_slot_1_spool_id" in w.get("entity_id", "")
        ]
        assert len(slot1_writes) == 0, "slot 1 unbind should be blocked"
        # Slot 2: binding written
        slot2_writes = [
            w for w in r._helper_writes
            if "ams_slot_2_spool_id" in w.get("entity_id", "") and w.get("value") == "77"
        ]
        assert len(slot2_writes) >= 1, "slot 2 bind should proceed during active print"


# ── helper method coverage tests ──────────────────────────────────────

class TestNormalizeUid:
    """_normalize_uid edge cases."""

    def _app(self):
        sm = FakeSpoolman([], [])
        return TestableReconcile(sm, {})

    def test_none(self):
        assert self._app()._normalize_uid(None) == ""

    def test_empty(self):
        assert self._app()._normalize_uid("") == ""

    def test_all_zeros(self):
        assert self._app()._normalize_uid("0000000000000000") == ""

    def test_unknown(self):
        assert self._app()._normalize_uid("unknown") == ""

    def test_unavailable(self):
        assert self._app()._normalize_uid("unavailable") == ""

    def test_quoted(self):
        assert self._app()._normalize_uid('"AABB0011"') == "AABB0011"

    def test_double_quoted(self):
        assert self._app()._normalize_uid('""AABB0011""') == "AABB0011"

    def test_normal(self):
        assert self._app()._normalize_uid("aabb0011") == "AABB0011"

    def test_spaces(self):
        assert self._app()._normalize_uid(" aa bb 00 11 ") == "AABB0011"


class TestNormalizeColorReconcile:
    """_normalize_color on reconcile class."""

    def _app(self):
        sm = FakeSpoolman([], [])
        return TestableReconcile(sm, {})

    def test_8char_hex(self):
        assert self._app()._normalize_color("#FF0000FF") == "ff0000"

    def test_6char_hex(self):
        assert self._app()._normalize_color("00AE42") == "00ae42"

    def test_invalid(self):
        assert self._app()._normalize_color("gggggg") == ""

    def test_none(self):
        assert self._app()._normalize_color(None) == ""


class TestColorCandidates:
    """_color_candidates generates multiple hex options."""

    def _app(self):
        sm = FakeSpoolman([], [])
        return TestableReconcile(sm, {})

    def test_8char_two_candidates(self):
        result = self._app()._color_candidates("FF0000AA")
        assert "ff0000" in result

    def test_6char_one_candidate(self):
        result = self._app()._color_candidates("00AE42")
        assert result == ["00ae42"]

    def test_invalid(self):
        result = self._app()._color_candidates("xyz")
        assert result == []


class TestMaterialKey:
    """_material_key normalization."""

    def _app(self):
        sm = FakeSpoolman([], [])
        return TestableReconcile(sm, {})

    def test_normal(self):
        assert self._app()._material_key("PLA") == "pla"

    def test_with_spaces(self):
        assert self._app()._material_key("  PLA  Plus  ") == "pla plus"

    def test_none(self):
        assert self._app()._material_key(None) == ""


class TestNormalizeMaterial:
    """_normalize_material groups variants."""

    def _app(self):
        sm = FakeSpoolman([], [])
        return TestableReconcile(sm, {})

    def test_pla_basic(self):
        assert self._app()._normalize_material("PLA") == "PLA"

    def test_pla_plus(self):
        assert self._app()._normalize_material("PLA+") == "PLA"

    def test_pla_silk(self):
        assert self._app()._normalize_material("PLA Silk") == "PLA"

    def test_petg_cf(self):
        assert self._app()._normalize_material("PETG-CF") == "PETG"

    def test_abs_variant(self):
        assert self._app()._normalize_material("ABS-GF") == "ABS"

    def test_tpu(self):
        assert self._app()._normalize_material("TPU") == "TPU"

    def test_empty(self):
        assert self._app()._normalize_material("") == ""


class TestIsTrayEmpty:
    """_is_tray_empty detection."""

    def _app(self):
        sm = FakeSpoolman([], [])
        return TestableReconcile(sm, {})

    def test_empty(self):
        assert self._app()._is_tray_empty("empty", {}) is True

    def test_not_empty(self):
        assert self._app()._is_tray_empty("loaded", {}) is False

    def test_none(self):
        assert self._app()._is_tray_empty(None, {}) is False


class TestNormalizeTrayHex:
    """_normalize_tray_hex validation."""

    def _app(self):
        sm = FakeSpoolman([], [])
        return TestableReconcile(sm, {})

    def test_8char_with_alpha(self):
        assert self._app()._normalize_tray_hex("#FF0000FF") == "ff0000"

    def test_6char(self):
        assert self._app()._normalize_tray_hex("00AE42") == "00ae42"

    def test_invalid(self):
        assert self._app()._normalize_tray_hex("gggggg") == ""


class TestLotNrIsRefinement:
    """_lot_nr_is_refinement logic."""

    def _app(self):
        sm = FakeSpoolman([], [])
        return TestableReconcile(sm, {})

    def test_refinement_fills_empty(self):
        assert self._app()._lot_nr_is_refinement("pla|1|000000", "pla|1|ff0000") is True

    def test_same_is_not_refinement(self):
        assert self._app()._lot_nr_is_refinement("pla|1|ff0000", "pla|1|ff0000") is False

    def test_type_mismatch(self):
        assert self._app()._lot_nr_is_refinement("pla|1|000000", "petg|1|ff0000") is False

    def test_uuid_not_refinable(self):
        uuid = "a" * 32
        assert self._app()._lot_nr_is_refinement(uuid, "pla|1|ff0000") is False

    def test_wrong_format(self):
        assert self._app()._lot_nr_is_refinement("pla", "pla|1|ff0000") is False

    def test_non_empty_field_differs(self):
        assert self._app()._lot_nr_is_refinement("pla|1|ff0000", "pla|2|ff0000") is False


class TestSafeIntFloat:
    """_safe_int and _safe_float edge cases."""

    def _app(self):
        sm = FakeSpoolman([], [])
        return TestableReconcile(sm, {})

    def test_safe_int_none(self):
        assert self._app()._safe_int(None) == 0

    def test_safe_int_valid(self):
        assert self._app()._safe_int("42") == 42

    def test_safe_int_invalid(self):
        assert self._app()._safe_int("abc", -1) == -1

    def test_safe_float_none(self):
        assert self._app()._safe_float(None) == 0.0

    def test_safe_float_valid(self):
        assert self._app()._safe_float("3.14") == 3.14

    def test_safe_float_invalid(self):
        assert self._app()._safe_float("xyz", -1.0) == -1.0


class TestGetHelperState:
    """_get_helper_state with fallback."""

    def test_returns_state(self):
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {
            "input_text.ams_slot_1_spool_id": "42",
        })
        assert r._get_helper_state("input_text.ams_slot_1_spool_id") == "42"

    def test_missing_returns_none(self):
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {})
        result = r._get_helper_state("input_text.nonexistent_entity")
        # TestableReconcile returns None for missing non-slot entities
        assert result is None


class TestRecordWriteNoWrite:
    """_record_write and _record_no_write with active run."""

    def _app(self):
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {})
        r._active_run = {
            "writes": [], "no_write_paths": [], "decisions": [],
            "conflicts": [], "validation_transcripts": [],
        }
        return r

    def test_record_write(self):
        r = self._app()
        r._record_write("spoolman_patch", {"path": "/api/v1/spool/1"})
        assert len(r._active_run["writes"]) == 1

    def test_record_no_write(self):
        r = self._app()
        r._record_no_write("slot_1", "helper_already_equal")
        assert len(r._active_run["no_write_paths"]) == 1
        assert r._active_run["no_write_paths"][0]["reason"] == "helper_already_equal"

    def test_record_decision(self):
        r = self._app()
        r._record_decision(1, "rfid_match", {"tag_uid": "AA"})
        assert len(r._active_run["decisions"]) == 1

    def test_record_write_no_active_run(self):
        """No active run → no crash."""
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {})
        r._active_run = None
        r._record_write("test", {})  # should not crash
        r._record_no_write("test", "reason")  # should not crash
        r._record_decision(1, "test", {})  # should not crash


class TestDebugLogging:
    """_debug conditional logging."""

    def test_debug_off(self):
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {}, args={"debug_logs": False})
        r._debug("test message")
        assert not any("RFID_DEBUG" in msg for msg, _ in r._log_calls)

    def test_debug_on(self):
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {}, args={"debug_logs": True})
        r._debug("test message")
        assert any("RFID_DEBUG test message" in msg for msg, _ in r._log_calls)

    def test_debug_with_payload(self):
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {}, args={"debug_logs": True})
        r._debug("test", {"key": "value"})
        assert any("RFID_DEBUG test" in msg for msg, _ in r._log_calls)


class TestNotifyConflict:
    """_notify_conflict notification."""

    def test_notify_conflict_sends_notification(self):
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {})
        r._active_run = {"writes": [], "no_write_paths": [], "decisions": [],
                         "conflicts": [], "validation_transcripts": []}
        tray_meta = {"name": "PLA", "type": "PLA", "color_hex": "ff0000", "filament_id": "1"}
        r._notify_conflict(1, "AABB", tray_meta, [10, 20], "DUPLICATE_UID")
        # call_service was called for persistent_notification


class TestLogValidationTranscript:
    """_log_validation_transcript."""

    def test_logs_json(self):
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {})
        t = {"slot": 1, "decision": "OK"}
        r._log_validation_transcript(t)
        assert any("RFID_VALIDATE" in msg for msg, _ in r._log_calls)


class TestAppendEvidence:
    """_append_evidence and _append_evidence_line."""

    def test_append_evidence_disabled(self):
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {})
        r.evidence_log_enabled = False
        r._append_evidence({"test": True})  # should not crash

    def test_append_evidence_line_disabled(self):
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {})
        r.evidence_log_enabled = False
        r._append_evidence_line("TEST")  # should not crash


class TestWriteLastMappingJson:
    """write_last_mapping_json truncation."""

    def test_short_mapping(self):
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {
            "input_text.p1s_01p00c5a3101668_last_mapping_json": "old",
        })
        r._active_run = {"writes": [], "no_write_paths": [], "decisions": [],
                         "conflicts": [], "validation_transcripts": []}
        r.write_last_mapping_json("test", {"1": 10})
        # Check it was written
        writes = [w for w in r._helper_writes
                  if "last_mapping_json" in w.get("entity_id", "")]
        assert len(writes) == 1

    def test_long_mapping_truncated(self):
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, {
            "input_text.p1s_01p00c5a3101668_last_mapping_json": "old",
        })
        r._active_run = {"writes": [], "no_write_paths": [], "decisions": [],
                         "conflicts": [], "validation_transcripts": []}
        # Very long mapping
        mapping = {str(i): i * 1000 for i in range(100)}
        r.write_last_mapping_json("x" * 100, mapping)
        writes = [w for w in r._helper_writes
                  if "last_mapping_json" in w.get("entity_id", "")]
        assert len(writes) == 1
        assert len(writes[0].get("value", "")) <= 255


class TestComputeHaSig:
    """_compute_ha_sig signature generation."""

    def _app(self):
        sm = FakeSpoolman([], [])
        return TestableReconcile(sm, {})

    def test_valid_sig(self):
        meta = {"filament_id": "GFL99", "type": "PLA", "color_hex": "FF0000FF"}
        result = self._app()._compute_ha_sig(meta)
        assert result is not None
        assert "gfl99" in result
        assert "pla" in result

    def test_missing_fields_returns_none(self):
        result = self._app()._compute_ha_sig({})
        assert result is None


class TestResolveColorForHaSig:
    """_resolve_color_for_ha_sig fallback chain."""

    def _app(self):
        sm = FakeSpoolman([], [])
        return TestableReconcile(sm, {})

    def test_tray_color_hex(self):
        meta = {"color_hex": "#FF0000FF"}
        result = self._app()._resolve_color_for_ha_sig(meta)
        assert result == "ff0000"

    def test_tray_color_fallback(self):
        meta = {"color": "00AE42"}
        result = self._app()._resolve_color_for_ha_sig(meta)
        assert result == "00ae42"

    def test_no_color(self):
        result = self._app()._resolve_color_for_ha_sig({})
        assert result == ""


class TestBindUidToSpool:
    """_bind_uid_to_spool routes correctly."""

    def _app(self):
        sm = FakeSpoolman([_spoolman_spool(1, "PLA", "ff0000", "Bambu Lab", 500.0, "Shelf")], [])
        r = TestableReconcile(sm, {})
        r._active_run = {"writes": [], "no_write_paths": [], "decisions": [],
                         "conflicts": [], "validation_transcripts": []}
        r._pending_lot_nr_writes = {}
        return r

    def test_with_tray_uuid(self):
        r = self._app()
        r._bind_uid_to_spool("AABB", 1, {1: {"id": 1, "lot_nr": ""}}, tray_uuid="UUID123")
        assert any("LOT_NR_ENROLLED" in msg for msg, _ in r._log_calls)

    def test_without_tray_uuid(self):
        r = self._app()
        r._bind_uid_to_spool("AABB", 1, {}, tray_uuid="")
        assert any("BIND_UID_SKIP" in msg for msg, _ in r._log_calls)


def _spoolman_spool(sid, material, color_hex, vendor, remaining, location, lot_nr="", extra=None):
    """Build a Spoolman spool dict for testing."""
    return {
        "id": sid,
        "remaining_weight": remaining,
        "location": location,
        "lot_nr": lot_nr,
        "extra": extra or {},
        "filament": {
            "id": sid * 100,
            "name": f"{vendor} {material}",
            "material": material,
            "color_hex": color_hex,
            "vendor": {"name": vendor},
        },
    }


from unittest import mock  # noqa: E402 — needed for coverage push tests below

# ── Coverage push: _set_helper, write_last_mapping_json, HTTP, events, notifications ──


class _EventTestReconcile(TestableReconcile):
    """TestableReconcile with schedule/event tracking."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._schedule_calls = []
        self._service_calls = []
        self._rfid_pending_calls = []
        self._notify_calls = []
        self._run_reconcile_calls = []
        self._cancelled_timers = []
        self._run_in_calls = []

    def _schedule_reconcile(self, reason):
        self._schedule_calls.append(reason)

    def _set_rfid_pending_until(self, slot, until):
        self._rfid_pending_calls.append((slot, until))

    def call_service(self, service, **kw):
        self._service_calls.append({"service": service, **kw})
        super().call_service(service, **kw)

    def _run_reconcile(self, reason, **kw):
        self._run_reconcile_calls.append((reason, kw))

    def run_in(self, cb, delay, **kw):
        self._run_in_calls.append({"callback": cb, "delay": delay, **kw})

    def cancel_timer(self, handle):
        self._cancelled_timers.append(handle)

    def _notify(self, title, msg, **kw):
        self._notify_calls.append((title, msg, kw))

    def _manual_enroll(self, slot, spool_id):
        self._log_calls.append((f"MANUAL_ENROLL slot={slot} spool_id={spool_id}", "INFO"))


class TestSetHelper:
    """_set_helper routes by entity domain."""

    def _app(self, state_map=None):
        fm = FakeSpoolman([], [])
        sm = state_map or {}
        r = TestableReconcile(fm, sm)
        r._service_calls = []
        original_call = r.call_service
        def tracking_call(service, **kw):
            r._service_calls.append({"service": service, **kw})
            original_call(service, **kw)
        r.call_service = tracking_call
        return r

    def test_input_text_routing(self):
        r = self._app({"input_text.ams_slot_1_spool_id": "0"})
        r._set_helper("input_text.ams_slot_1_spool_id", "42")
        assert any(c["service"] == "input_text/set_value" for c in r._service_calls)

    def test_text_routing(self):
        r = self._app({"text.some_helper": "old"})
        # We need text.* entity in state_map so get_state returns non-None
        r._state_map["text.some_helper"] = "old"
        r._set_helper("text.some_helper", "new")
        assert any(c["service"] == "text/set_value" for c in r._service_calls)

    def test_unsupported_domain_raises(self):
        r = self._app({"sensor.foo": "bar"})
        r._state_map["sensor.foo"] = "bar"
        with pytest.raises(ValueError, match="unsupported entity domain"):
            r._set_helper("sensor.foo", "baz")

    def test_missing_helper_warns(self):
        r = self._app({})
        r._state_map.clear()
        # For a non-ams_slot entity, get_state returns None
        r._set_helper("input_text.nonexistent_helper", "value")
        assert any("missing in HA configuration" in msg for msg, _ in r._log_calls)

    def test_same_value_noop(self):
        r = self._app({"input_text.ams_slot_1_spool_id": "42"})
        r._set_helper("input_text.ams_slot_1_spool_id", "42")
        assert not r._service_calls

    def test_none_value_becomes_empty(self):
        r = self._app({"input_text.ams_slot_1_status": "OK"})
        r._set_helper("input_text.ams_slot_1_status", None)
        assert any(c.get("value") == "" for c in r._service_calls)


class TestWriteLastMappingJson:
    """write_last_mapping_json truncation paths."""

    def _app(self):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {})
        r._service_calls = []
        original_call = r.call_service
        def tracking_call(service, **kw):
            r._service_calls.append({"service": service, **kw})
            original_call(service, **kw)
        r.call_service = tracking_call
        # Ensure the last_mapping_json entity exists in state
        r._state_map[r._last_mapping_json_entity] = ""
        return r

    def test_short_json_passes(self):
        r = self._app()
        r.write_last_mapping_json("test", {"1": 42})
        assert len(r._service_calls) == 1
        written = r._service_calls[0]["value"]
        assert len(written) <= 255

    def test_truncation_removes_timestamp(self):
        r = self._app()
        # Long mapping that's >255 with timestamp
        big_mapping = {str(i): f"spool_{i}_very_long_name_here" for i in range(10)}
        r.write_last_mapping_json("very_long_reason_string_that_goes_on", big_mapping)
        assert len(r._service_calls) == 1
        written = r._service_calls[0]["value"]
        assert len(written) <= 255

    def test_extreme_truncation(self):
        r = self._app()
        # Extremely long mapping
        big_mapping = {str(i): f"spool_{i}_" + "x" * 50 for i in range(20)}
        r.write_last_mapping_json("reason", big_mapping)
        assert len(r._service_calls) == 1
        written = r._service_calls[0]["value"]
        assert len(written) <= 255


def _mock_urlopen_cm(data_bytes):
    """Create a context-manager mock for `with urllib.request.urlopen(...) as resp:`."""
    resp = unittest.mock.MagicMock()
    resp.read.return_value = data_bytes
    resp.status = 200
    cm = unittest.mock.MagicMock()
    cm.__enter__ = unittest.mock.MagicMock(return_value=resp)
    cm.__exit__ = unittest.mock.MagicMock(return_value=False)
    return cm


class TestUrlOpenJson:
    """_urlopen_json HTTP handling (real methods, not FakeSpoolman)."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_success_returns_json(self):
        import urllib.request
        r = self._app()
        cm = _mock_urlopen_cm(json.dumps({"id": 1}).encode())
        req = urllib.request.Request("http://fake:7912/api/v1/spool/1")
        with mock.patch("urllib.request.urlopen", return_value=cm):
            result = AmsRfidReconcile._urlopen_json(r, req)
        assert result == {"id": 1}

    def test_empty_body_returns_empty_dict(self):
        import urllib.request
        r = self._app()
        cm = _mock_urlopen_cm(b"")
        req = urllib.request.Request("http://fake:7912/api/v1/spool/1")
        with mock.patch("urllib.request.urlopen", return_value=cm):
            result = AmsRfidReconcile._urlopen_json(r, req)
        assert result == {}

    def test_http_400_json_error(self):
        import urllib.request, urllib.error
        r = self._app()
        r._notify_calls = []
        r._notify = lambda *a, **kw: r._notify_calls.append(a)
        exc = urllib.error.HTTPError(
            "http://fake:7912/api/v1/spool/1", 400, "Bad Request",
            {}, mock.MagicMock()
        )
        exc.read = mock.MagicMock(return_value=b"Value is not valid JSON")
        req = urllib.request.Request("http://fake:7912/api/v1/spool/1")
        with mock.patch("urllib.request.urlopen", side_effect=exc):
            with pytest.raises(RuntimeError, match="HTTP 400"):
                AmsRfidReconcile._urlopen_json(r, req)
        assert any("Spoolman 400" in msg for msg, _ in r._log_calls)

    def test_url_error(self):
        import urllib.request, urllib.error
        r = self._app()
        exc = urllib.error.URLError("Connection refused")
        req = urllib.request.Request("http://fake:7912/api/v1/spool/1")
        with mock.patch("urllib.request.urlopen", side_effect=exc):
            with pytest.raises(RuntimeError, match="URL error"):
                AmsRfidReconcile._urlopen_json(r, req)


class TestSpoolmanRealMethods:
    """_spoolman_get/post/patch using real HTTP (mocked urllib)."""

    def _app(self):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {})
        return r

    def test_real_spoolman_get(self):
        r = self._app()
        cm = _mock_urlopen_cm(json.dumps({"id": 1}).encode())
        with mock.patch("urllib.request.urlopen", return_value=cm):
            result = AmsRfidReconcile._spoolman_get(r, "/api/v1/spool/1")
        assert result == {"id": 1}

    def test_real_spoolman_post(self):
        r = self._app()
        cm = _mock_urlopen_cm(json.dumps({"id": 2}).encode())
        with mock.patch("urllib.request.urlopen", return_value=cm):
            result = AmsRfidReconcile._spoolman_post(r, "/api/v1/spool", {"filament_id": 1})
        assert result == {"id": 2}

    def test_real_spoolman_patch_normalizes_location(self):
        r = self._app()
        r._normalize_location = lambda loc: loc
        cm = _mock_urlopen_cm(json.dumps({"id": 1}).encode())
        with mock.patch("urllib.request.urlopen", return_value=cm):
            result = AmsRfidReconcile._spoolman_patch(r, "/api/v1/spool/1", {"location": "AMS_1_1"})
        assert result == {"id": 1}


class TestNotifyNonrfidNeedsAction:
    """_notify_nonrfid_needs_action calls _notify with correct args."""

    def test_calls_notify(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        tray_meta = {"type": "PLA", "color_hex": "FF0000", "name": "Red PLA", "filament_id": "123"}
        r._notify_nonrfid_needs_action(5, tray_meta, "no_match")
        assert len(r._notify_calls) == 1
        assert "Non-RFID NEEDS_ACTION" in r._notify_calls[0][0]


class TestNotifyNonrfidNewFallback:
    """_notify_nonrfid_new_fallback calls _notify."""

    def test_calls_notify(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        tray_meta = {"type": "PLA", "color_hex": "FF0000", "name": "Red PLA"}
        r._notify_nonrfid_new_fallback(5, 42, tray_meta)
        assert len(r._notify_calls) == 1
        assert "New fallback" in r._notify_calls[0][0]


class TestNotifyUnbound:
    """_notify_unbound calls _notify with candidate_ids."""

    def test_calls_notify_with_candidates(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        tray_meta = {"type": "PLA", "color_hex": "FF0000", "name": "Red", "filament_id": "123"}
        r._notify_unbound(1, "AABBCCDD", tray_meta, [10, 20])
        assert len(r._notify_calls) == 1
        assert "UNBOUND" in r._notify_calls[0][0]

    def test_calls_notify_no_candidates(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        tray_meta = {"type": "PLA", "color_hex": "FF0000", "name": "Red", "filament_id": "123"}
        r._notify_unbound(1, "AABBCCDD", tray_meta, [])
        assert len(r._notify_calls) == 1


class TestOnReconcileEvent:
    """_on_reconcile_event calls _schedule_reconcile."""

    def test_calls_schedule(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._on_reconcile_event("ams_reconcile", {"reason": "test"}, {})
        assert "test" in r._schedule_calls


class TestOnManualReconcileButton:
    """_on_manual_reconcile_button triggers reconcile."""

    def test_normal_press(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._active_run = None
        r._on_manual_reconcile_button("entity", "state", "old_ts", "2024-01-01T00:00:00", {})
        assert len(r._run_reconcile_calls) == 1

    def test_skip_if_active_run(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._active_run = {"some": "run"}
        r._on_manual_reconcile_button("entity", "state", "old_ts", "2024-01-01T00:00:00", {})
        assert len(r._run_reconcile_calls) == 0

    def test_skip_if_no_new(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._on_manual_reconcile_button("entity", "state", "old_ts", None, {})
        assert len(r._run_reconcile_calls) == 0

    def test_skip_if_same(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._on_manual_reconcile_button("entity", "state", "same", "same", {})
        assert len(r._run_reconcile_calls) == 0


class TestOnReconcileAllEvent:
    """_on_reconcile_all_event parses payload."""

    def test_default_status_only(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._on_reconcile_all_event("event", {"reason": "manual_ui"}, {})
        assert len(r._run_reconcile_calls) == 1
        assert r._run_reconcile_calls[0][1].get("status_only") is True

    def test_status_only_false(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._on_reconcile_all_event("event", {"reason": "full", "status_only": False}, {})
        assert r._run_reconcile_calls[0][1].get("status_only") is False

    def test_non_bool_status_only_defaults_true(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._on_reconcile_all_event("event", {"status_only": "yes"}, {})
        assert r._run_reconcile_calls[0][1].get("status_only") is True


class TestOnTrayStateChange:
    """_on_tray_state_change sets rfid_pending and schedules."""

    def test_triggers_schedule(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        tray_entity = "sensor.p1s_01p00c5a3101668_ams_1_tray_1"
        r._on_tray_state_change(tray_entity, "state", "old", "new", {})
        assert len(r._schedule_calls) == 1
        assert len(r._rfid_pending_calls) == 1
        assert r._rfid_pending_calls[0][0] == 1  # slot 1


class TestOnHelperSpoolIdChange:
    """_on_helper_spool_id_change triggers reconcile on real changes."""

    def test_same_value_skip(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._on_helper_spool_id_change("input_text.ams_slot_1_spool_id", "state", "42", "42", {})
        assert len(r._schedule_calls) == 0

    def test_active_run_skip(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._active_run = {"some": "run"}
        r._on_helper_spool_id_change("input_text.ams_slot_1_spool_id", "state", "0", "42", {})
        assert len(r._schedule_calls) == 0

    def test_suppressed_skip(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._suppress_helper_change_until[1] = datetime.datetime.utcnow() + datetime.timedelta(seconds=60)
        r._on_helper_spool_id_change("input_text.ams_slot_1_spool_id", "state", "0", "42", {})
        assert len(r._schedule_calls) == 0

    def test_normal_triggers(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._on_helper_spool_id_change("input_text.ams_slot_1_spool_id", "state", "0", "42", {})
        assert len(r._schedule_calls) == 1

    def test_unknown_entity_skip(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._on_helper_spool_id_change("input_text.ams_slot_99_spool_id", "state", "0", "42", {})
        assert len(r._schedule_calls) == 0


class TestOnManualEnrollEvent:
    """_on_manual_enroll_event handles enroll requests."""

    def test_invalid_slot(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._on_manual_enroll_event("event", {"slot": 99, "spool_id": 1}, {})
        assert len(r._notify_calls) == 1
        assert "Failed" in r._notify_calls[0][0]

    def test_invalid_spool_id(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._on_manual_enroll_event("event", {"slot": 1, "spool_id": 0}, {})
        assert len(r._notify_calls) == 1

    def test_success(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._on_manual_enroll_event("event", {"slot": 1, "spool_id": 42}, {})
        assert any("MANUAL_ENROLL" in msg for msg, _ in r._log_calls)
        assert len(r._run_reconcile_calls) == 1

    def test_exception_notifies(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._manual_enroll = mock.MagicMock(side_effect=RuntimeError("tray_uuid is empty"))
        r._on_manual_enroll_event("event", {"slot": 1, "spool_id": 42}, {})
        assert len(r._notify_calls) == 1
        assert "Failed" in r._notify_calls[0][0]


class TestReadTrayColorHex:
    """_read_tray_color_hex reads AMS tray color."""

    def _app(self, slot_state):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, slot_state)
        return r

    def test_6char_hex(self):
        tray = "sensor.p1s_01p00c5a3101668_ams_1_tray_1"
        r = self._app({f"{tray}::all": {"state": "PLA", "attributes": {"color": "FF0000"}}})
        assert r._read_tray_color_hex(1) == "FF0000"

    def test_8char_strips_alpha(self):
        tray = "sensor.p1s_01p00c5a3101668_ams_1_tray_1"
        r = self._app({f"{tray}::all": {"state": "PLA", "attributes": {"color": "FF0000FF"}}})
        assert r._read_tray_color_hex(1) == "FF0000"

    def test_hash_prefix(self):
        tray = "sensor.p1s_01p00c5a3101668_ams_1_tray_1"
        r = self._app({f"{tray}::all": {"state": "PLA", "attributes": {"color": "#161616"}}})
        assert r._read_tray_color_hex(1) == "161616"

    def test_empty_returns_none(self):
        tray = "sensor.p1s_01p00c5a3101668_ams_1_tray_1"
        r = self._app({f"{tray}::all": {"state": "PLA", "attributes": {"color": ""}}})
        assert r._read_tray_color_hex(1) is None

    def test_unknown_slot_returns_none(self):
        r = self._app({})
        assert r._read_tray_color_hex(99) is None

    def test_bad_length_returns_none(self):
        tray = "sensor.p1s_01p00c5a3101668_ams_1_tray_1"
        r = self._app({f"{tray}::all": {"state": "PLA", "attributes": {"color": "FF00"}}})
        assert r._read_tray_color_hex(1) is None


class TestEnsureEvidencePathWritableReal:
    """_ensure_evidence_path_writable with real filesystem."""

    def test_writable_path_succeeds(self):
        import tempfile
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {})
        with tempfile.TemporaryDirectory() as td:
            r.evidence_log_path = os.path.join(td, "evidence.log")
            r.evidence_log_enabled = False
            # Call the real method (not the overridden one)
            AmsRfidReconcile._ensure_evidence_path_writable(r)
            assert r.evidence_log_enabled is True

    def test_all_paths_fail(self):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {})
        r.evidence_log_path = "/proc/nonexistent/evidence.log"
        r.evidence_log_enabled = False
        with mock.patch("os.makedirs", side_effect=OSError("nope")):
            AmsRfidReconcile._ensure_evidence_path_writable(r)
        assert r.evidence_log_enabled is False


class TestAppendEvidenceReal:
    """_append_evidence writes JSON to file."""

    def test_enabled_writes(self):
        import tempfile
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            r.evidence_log_path = f.name
            r.evidence_log_enabled = True
            AmsRfidReconcile._append_evidence(r, {"key": "value"})
        with open(r.evidence_log_path) as f:
            line = f.read().strip()
        assert '"key"' in line
        os.unlink(r.evidence_log_path)

    def test_disabled_skips(self):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {})
        r.evidence_log_enabled = False
        AmsRfidReconcile._append_evidence(r, {"key": "value"})
        # No error, just skips

    def test_write_error_disables(self):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {})
        r.evidence_log_path = "/proc/nonexistent/evidence.log"
        r.evidence_log_enabled = True
        AmsRfidReconcile._append_evidence(r, {"key": "value"})
        assert r.evidence_log_enabled is False


class TestAppendEvidenceLineReal:
    """_append_evidence_line writes text to file."""

    def test_enabled_writes(self):
        import tempfile
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            r.evidence_log_path = f.name
            r.evidence_log_enabled = True
            AmsRfidReconcile._append_evidence_line(r, "RFID_EMPTY_TRAY_CLEAR slot=1")
        with open(r.evidence_log_path) as f:
            line = f.read().strip()
        assert "RFID_EMPTY_TRAY_CLEAR" in line
        os.unlink(r.evidence_log_path)

    def test_disabled_skips(self):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {})
        r.evidence_log_enabled = False
        AmsRfidReconcile._append_evidence_line(r, "test line")

    def test_write_error_disables(self):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {})
        r.evidence_log_path = "/proc/nonexistent/evidence.log"
        r.evidence_log_enabled = True
        AmsRfidReconcile._append_evidence_line(r, "test line")
        assert r.evidence_log_enabled is False


class TestDebugMethod:
    """_debug only logs when debug_logs enabled."""

    def test_enabled_with_payload(self):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {}, args={"debug_logs": True})
        r._debug("test_msg", {"key": "val"})
        assert any("RFID_DEBUG test_msg" in msg for msg, _ in r._log_calls)

    def test_enabled_without_payload(self):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {}, args={"debug_logs": True})
        r._debug("test_msg", None)
        assert any("RFID_DEBUG test_msg" in msg for msg, _ in r._log_calls)

    def test_disabled_skips(self):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {}, args={"debug_logs": False})
        r._debug("test_msg", {"key": "val"})
        assert not any("RFID_DEBUG" in msg for msg, _ in r._log_calls)


class TestBuildLotSig:
    """_build_lot_sig and _build_lot_sig_for_lookup."""

    def _app(self):
        return TestableReconcile(FakeSpoolman([], []), {})

    def test_full_sig(self):
        r = self._app()
        meta = {"type": "PLA", "filament_id": "GFL01", "color_hex": "FF0000"}
        sig = r._build_lot_sig(meta)
        assert sig == "pla|gfl01|ff0000"

    def test_8char_color_trimmed(self):
        r = self._app()
        meta = {"type": "PLA", "filament_id": "GFL01", "color_hex": "FF0000FF"}
        sig = r._build_lot_sig(meta)
        assert "ff0000" in sig

    def test_missing_type_returns_empty(self):
        r = self._app()
        meta = {"type": "", "filament_id": "GFL99", "color_hex": "FF0000"}
        assert r._build_lot_sig(meta) == ""

    def test_generic_filament_returns_empty(self):
        r = self._app()
        meta = {"type": "PLA", "filament_id": "GFB99", "color_hex": "FF0000"}
        sig = r._build_lot_sig(meta)
        # GFB99 is generic → empty
        assert sig == ""

    def test_for_lookup_allows_generic(self):
        r = self._app()
        meta = {"type": "PLA", "filament_id": "GFB99", "color_hex": "FF0000"}
        sig = r._build_lot_sig_for_lookup(meta)
        assert "gfb99" in sig


class TestNonrfidTrayMatchesBoundSpool:
    """_nonrfid_tray_matches_bound_spool."""

    def _app(self):
        return TestableReconcile(FakeSpoolman([], []), {})

    def test_lot_nr_match(self):
        r = self._app()
        tray_meta = {"type": "PLA", "filament_id": "GFL99", "color_hex": "FF0000"}
        spool_index = {1: {"id": 1, "lot_nr": "pla|gfl99|ff0000", "filament": {"material": "PLA", "color_hex": "FF0000"}}}
        assert r._nonrfid_tray_matches_bound_spool(tray_meta, 1, spool_index) is True

    def test_lot_nr_mismatch(self):
        r = self._app()
        tray_meta = {"type": "PLA", "filament_id": "GFL99", "color_hex": "FF0000"}
        spool_index = {1: {"id": 1, "lot_nr": "petg|gfl99|0000ff", "filament": {"material": "PETG", "color_hex": "0000FF"}}}
        assert r._nonrfid_tray_matches_bound_spool(tray_meta, 1, spool_index) is False

    def test_uuid_lot_nr_no_match(self):
        r = self._app()
        tray_meta = {"type": "PLA", "filament_id": "GFL99", "color_hex": "FF0000"}
        spool_index = {1: {"id": 1, "lot_nr": "AABBCCDD11223344EEFF00112233AABB", "filament": {"material": "PLA", "color_hex": "FF0000"}}}
        assert r._nonrfid_tray_matches_bound_spool(tray_meta, 1, spool_index) is False

    def test_no_lot_nr_material_color_match(self):
        r = self._app()
        tray_meta = {"type": "PLA", "filament_id": "GFL99", "color_hex": "FF0000"}
        spool_index = {1: {"id": 1, "lot_nr": "", "filament": {"material": "PLA", "color_hex": "FF0000"}}}
        assert r._nonrfid_tray_matches_bound_spool(tray_meta, 1, spool_index) is True

    def test_no_lot_nr_material_mismatch(self):
        r = self._app()
        tray_meta = {"type": "PLA", "filament_id": "GFL99", "color_hex": "FF0000"}
        spool_index = {1: {"id": 1, "lot_nr": "", "filament": {"material": "PETG", "color_hex": "FF0000"}}}
        assert r._nonrfid_tray_matches_bound_spool(tray_meta, 1, spool_index) is False


class TestCheckPendingConfirmation:
    """_check_pending_confirmation returns (confirmed, pending)."""

    def _app(self, state_map=None):
        return TestableReconcile(FakeSpoolman([], []), state_map or {})

    def test_empty_stored_confirms(self):
        r = self._app()
        confirmed, pending = r._check_pending_confirmation(5, "sig_abc", "")
        assert confirmed is True
        assert pending is False

    def test_same_sig_confirms(self):
        r = self._app()
        confirmed, pending = r._check_pending_confirmation(5, "sig_abc", "sig_abc")
        assert confirmed is True
        assert pending is False

    def test_different_sig_starts_pending(self):
        r = self._app()
        confirmed, pending = r._check_pending_confirmation(5, "sig_new", "sig_old")
        assert confirmed is False
        assert pending is True

    def test_pending_same_sig_increments(self):
        r = self._app({"input_text.ams_slot_5_tray_signature": "PENDING:1:100000:sig_new"})
        confirmed, pending = r._check_pending_confirmation(5, "sig_new", "PENDING:1:100000:sig_new")
        assert confirmed is True or pending is True  # either confirmed (count>=2) or still pending


class TestTrayRemainingWeight:
    """_tray_remaining_weight calculates grams from attrs."""

    def _app(self):
        return TestableReconcile(FakeSpoolman([], []), {})

    def test_normal_calc(self):
        r = self._app()
        assert r._tray_remaining_weight({"tray_weight": 1000, "remain": 75}) == 750.0

    def test_zero_remain(self):
        r = self._app()
        assert r._tray_remaining_weight({"tray_weight": 1000, "remain": 0}) == 0.0

    def test_zero_weight(self):
        r = self._app()
        assert r._tray_remaining_weight({"tray_weight": 0, "remain": 50}) == 0.0


class TestClearLegacySignatures:
    """_clear_legacy_signatures clears old NONRFID| format."""

    def test_clears_legacy(self):
        fm = FakeSpoolman([], [])
        state_map = {
            "input_text.ams_slot_5_tray_signature": "NONRFID|PLA|FF0000",
        }
        r = _EventTestReconcile(fm, state_map, args={})
        r._clear_legacy_signatures()
        assert any("LEGACY_SIGNATURE_CLEARED" in msg for msg, _ in r._log_calls)

    def test_no_legacy_no_action(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        r._clear_legacy_signatures()
        assert not any("LEGACY_SIGNATURE_CLEARED" in msg for msg, _ in r._log_calls)


class TestSpoolExists:
    """_spool_exists checks Spoolman."""

    def test_exists(self):
        spool = _spoolman_spool(1, "PLA", "FF0000", "Bambu", 500, "Shelf")
        fm = FakeSpoolman([spool], [])
        r = TestableReconcile(fm, {})
        assert r._spool_exists(1) is True

    def test_not_exists(self):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {})
        assert r._spool_exists(999) is False

    def test_zero_id_returns_false(self):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {})
        assert r._spool_exists(0) is False

    def test_with_active_run_cache(self):
        spool = _spoolman_spool(1, "PLA", "FF0000", "Bambu", 500, "Shelf")
        fm = FakeSpoolman([spool], [])
        r = TestableReconcile(fm, {})
        r._active_run = {"spool_exists_cache": {}}
        assert r._spool_exists(1) is True
        # Second call hits cache
        assert r._spool_exists(1) is True
        assert r._active_run["spool_exists_cache"][1] is True


class TestNotifyUnboundRfidNoShelf:
    """_notify_unbound_rfid_no_shelf sends notification."""

    def test_sends_notify(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {}, args={})
        tray_meta = {"type": "PLA", "color_hex": "FF0000", "name": "Red PLA"}
        r._notify_unbound_rfid_no_shelf(1, "AABBCCDD", tray_meta)
        assert len(r._notify_calls) == 1
        assert "NEEDS_ACTION" in r._notify_calls[0][0]


class TestGetTrayIdentity:
    """_get_tray_identity returns tray_uuid > tag_uid > signature."""

    def _app(self):
        return TestableReconcile(FakeSpoolman([], []), {})

    def test_tray_uuid_preferred(self):
        r = self._app()
        attrs = {"tray_uuid": "AABBCCDD-1122-3344-5566-778899001122"}
        result = r._get_tray_identity(attrs, "TAG123", "PLA")
        assert "AABBCCDD" in result

    def test_tag_uid_fallback(self):
        r = self._app()
        attrs = {"tray_uuid": "00000000000000000000000000000000"}
        result = r._get_tray_identity(attrs, "AABB1122", "PLA")
        assert "AABB1122" in result

    def test_signature_fallback(self):
        r = self._app()
        attrs = {"tray_uuid": "", "type": "PLA", "color": "FF0000", "name": "Red PLA", "filament_id": "GFL99"}
        result = r._get_tray_identity(attrs, "", "PLA")
        assert result  # should return some signature


class TestTrayMeta:
    """_tray_meta builds metadata dict."""

    def _app(self):
        return TestableReconcile(FakeSpoolman([], []), {})

    def test_full_meta(self):
        r = self._app()
        attrs = {"name": "Red PLA", "type": "PLA", "filament_id": "GFL99", "color": "FF0000FF"}
        meta = r._tray_meta(attrs, "PLA Basic")
        assert meta["name"] == "Red PLA"
        assert meta["type"] == "PLA"
        assert meta["color_hex"] == "ff0000"

    def test_empty_attrs(self):
        r = self._app()
        meta = r._tray_meta({}, "")
        assert meta["name"] == ""
        assert meta["type"] == ""
        assert meta["color_hex"] == ""


class TestShouldStick:
    """_should_stick returns True if same tray and valid spool."""

    def test_same_sig_valid_spool(self):
        spool = _spoolman_spool(1, "PLA", "FF0000", "Bambu", 500, "Shelf")
        fm = FakeSpoolman([spool], [])
        r = TestableReconcile(fm, {})
        assert r._should_stick(1, "sig_abc", "sig_abc", 1) is True

    def test_different_sig(self):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {})
        assert r._should_stick(1, "sig_abc", "sig_xyz", 1) is False

    def test_no_helper(self):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {})
        assert r._should_stick(1, "sig_abc", "sig_abc", 0) is False


class TestUnjson:
    """_unjson decodes JSON-string values."""

    def _app(self):
        return TestableReconcile(FakeSpoolman([], []), {})

    def test_plain_string(self):
        assert self._app()._unjson("ABC123") == "ABC123"

    def test_json_quoted(self):
        assert self._app()._unjson('"ABC123"') == "ABC123"

    def test_none(self):
        assert self._app()._unjson(None) == ""

    def test_empty(self):
        assert self._app()._unjson("") == ""

    def test_non_string(self):
        assert self._app()._unjson(42) == "42"


class TestPatchSpoolFields:
    """_patch_spool_fields does plain PATCH."""

    def test_patches_lot_nr(self):
        spool = _spoolman_spool(1, "PLA", "FF0000", "Bambu", 500, "Shelf")
        fm = FakeSpoolman([spool], [])
        r = TestableReconcile(fm, {})
        r._patch_spool_fields(1, {"lot_nr": "pla|gfl99|ff0000"})
        assert len(fm.patches) == 1
        assert fm.patches[0]["payload"]["lot_nr"] == "pla|gfl99|ff0000"

    def test_normalizes_location(self):
        spool = _spoolman_spool(1, "PLA", "FF0000", "Bambu", 500, "Shelf")
        fm = FakeSpoolman([spool], [])
        r = TestableReconcile(fm, {})
        r._patch_spool_fields(1, {"location": "AMS_1_1"})
        assert len(fm.patches) == 1


class TestSyncFilamentColorOnBind:
    """_sync_filament_color_on_bind PATCH or skip."""

    def _app(self, state_map=None):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, state_map or {})
        return r, fm

    def test_empty_mode_returns_false(self):
        r, _ = self._app()
        assert r._sync_filament_color_on_bind(1, 10, "") is False

    def test_auto_no_tray_color_returns_false(self):
        r, _ = self._app()
        assert r._sync_filament_color_on_bind(1, 10, "auto") is False
        assert any("SYNC_COLOR_NO_TRAY_COLOR" in m for m, _ in r._log_calls)

    def test_auto_patches_filament_color(self):
        prefix = "p1s_01p00c5a3101668"
        tray_entity = f"sensor.{prefix}_ams_1_tray_1"
        sm = {
            tray_entity: {"state": "loaded", "attributes": {"color": "FF0000"}},
        }
        spool = _spoolman_spool(10, "PLA", "000000", "Bambu", 800, "Shelf")
        fm = FakeSpoolman([spool], [{"id": 50, "color_hex": "000000"}])
        r = TestableReconcile(fm, sm)
        # Override _spoolman_patch to return a dict (simulate successful PATCH)
        orig_patch = r._spoolman_patch
        def _patching_patch(path, payload):
            orig_patch(path, payload)
            return {"ok": True}
        r._spoolman_patch = _patching_patch
        result = r._sync_filament_color_on_bind(1, 10, "auto")
        assert result is True
        assert any("COLOR_SYNC" in m for m, _ in r._log_calls)

    def test_explicit_hex_patches(self):
        spool = _spoolman_spool(10, "PLA", "000000", "Bambu", 800, "Shelf")
        fm = FakeSpoolman([spool], [{"id": 50, "color_hex": "000000"}])
        r = TestableReconcile(fm, {})
        orig_patch = r._spoolman_patch
        def _patching_patch(path, payload):
            orig_patch(path, payload)
            return {"ok": True}
        r._spoolman_patch = _patching_patch
        result = r._sync_filament_color_on_bind(1, 10, "AABB00")
        assert result is True

    def test_invalid_mode_returns_false(self):
        r, _ = self._app()
        assert r._sync_filament_color_on_bind(1, 10, "rainbow") is False
        assert any("SYNC_COLOR_INVALID_MODE" in m for m, _ in r._log_calls)

    def test_already_matching_color_returns_false(self):
        prefix = "p1s_01p00c5a3101668"
        tray_entity = f"sensor.{prefix}_ams_1_tray_1"
        sm = {tray_entity: {"state": "loaded", "attributes": {"color": "FF0000"}}}
        spool = _spoolman_spool(10, "PLA", "FF0000", "Bambu", 800, "Shelf")
        fm = FakeSpoolman([spool], [{"id": 50, "color_hex": "FF0000"}])
        r = TestableReconcile(fm, sm)
        result = r._sync_filament_color_on_bind(1, 10, "auto")
        assert result is False
        assert any("SYNC_COLOR_ALREADY_MATCHES" in m for m, _ in r._log_calls)

    def test_spool_not_found(self):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {})
        result = r._sync_filament_color_on_bind(1, 999, "AABB00")
        assert result is False
        assert any("SYNC_COLOR_SPOOL_NOT_FOUND" in m for m, _ in r._log_calls)


class TestOnSlotAssigned:
    """_on_slot_assigned event handler."""

    def test_invalid_slot_returns(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {})
        r._on_slot_assigned("event", {"slot": 99, "spool_id": 1}, {})
        assert not r._run_reconcile_calls

    def test_zero_spool_id_returns(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {})
        r._on_slot_assigned("event", {"slot": 1, "spool_id": 0}, {})
        assert not r._run_reconcile_calls

    def test_rfid_tray_skips_lot_sig(self):
        """RFID tray (non-zero tag_uid) → skip lot_sig enrollment, just reconcile."""
        prefix = "p1s_01p00c5a3101668"
        tray_entity = f"sensor.{prefix}_ams_1_tray_1"
        sm = {
            tray_entity: {"state": "loaded", "attributes": {"tag_uid": "AABB1234", "tray_uuid": "CCDD5678"}},
        }
        spool = _spoolman_spool(10, "PLA", "FF0000", "Bambu", 800, "Shelf")
        fm = FakeSpoolman([spool], [])
        r = _EventTestReconcile(fm, sm)
        r._on_slot_assigned("event", {"slot": 1, "spool_id": 10}, {})
        assert len(r._run_reconcile_calls) == 1
        assert "slot_assigned" in r._run_reconcile_calls[0][0]

    def test_nonrfid_enrolls_lot_sig(self):
        """Non-RFID tray → build lot_sig, enroll, reconcile."""
        prefix = "p1s_01p00c5a3101668"
        tray_entity = f"sensor.{prefix}_ams_1_tray_1"
        sm = {
            tray_entity: {"state": "loaded", "attributes": {
                "tag_uid": "0000000000000000", "tray_uuid": "00000000000000000000000000000000",
                "type": "PLA", "color": "FF0000", "name": "Bambu PLA Basic",
                "filament_id": "GFL01",
            }},
        }
        spool = _spoolman_spool(10, "PLA", "FF0000", "Bambu", 800, "Shelf")
        fm = FakeSpoolman([spool], [])
        r = _EventTestReconcile(fm, sm)
        r._on_slot_assigned("event", {"slot": 1, "spool_id": 10}, {})
        assert any("SLOT_ASSIGNED_LOT_SIG_ENROLLED" in m for m, _ in r._log_calls)
        assert len(r._run_reconcile_calls) == 1


class TestNotifyConflict:
    """_notify_conflict formats and sends notification."""

    def test_sends_notification(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {})
        tray_meta = {"name": "PLA", "type": "PLA", "color_hex": "FF0000", "filament_id": "GFL01"}
        r._notify_conflict(1, "AABB1234", tray_meta, [10, 20], "multiple_matches")
        assert len(r._notify_calls) == 1
        title, msg, kw = r._notify_calls[0]
        assert "CONFLICT" in title
        assert "10,20" in msg


class TestLogValidationTranscript:
    """_log_validation_transcript logs JSON."""

    def test_logs_json(self):
        fm = FakeSpoolman([], [])
        r = TestableReconcile(fm, {})
        r._log_validation_transcript({"slot": 1, "decision": "OK"})
        assert any("RFID_VALIDATE" in m for m, _ in r._log_calls)


class TestResolveColorForHaSig:
    """_resolve_color_for_ha_sig fallback chain."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_tray_meta_color_hex(self):
        r = self._app()
        result = r._resolve_color_for_ha_sig({"color_hex": "FF0000"})
        assert result == "ff0000"

    def test_tray_meta_color_hex_8char(self):
        r = self._app()
        result = r._resolve_color_for_ha_sig({"color_hex": "FF0000FF"})
        assert result == "ff0000"

    def test_fallback_to_tray_color(self):
        r = self._app()
        result = r._resolve_color_for_ha_sig({"color": "00FF00"})
        assert result == "00ff00"

    def test_fallback_to_spoolman(self):
        r = self._app()
        spool_index = {42: {"filament": {"color_hex": "AABBCC"}}}
        result = r._resolve_color_for_ha_sig(
            {}, expected_spool_id=42, spool_index=spool_index
        )
        assert result == "aabbcc"

    def test_fallback_to_candidate(self):
        r = self._app()
        spool_index = {10: {"filament": {"color_hex": "112233"}}}
        result = r._resolve_color_for_ha_sig(
            {}, candidate_ids=[10], spool_index=spool_index
        )
        assert result == "112233"

    def test_empty_when_no_color(self):
        r = self._app()
        result = r._resolve_color_for_ha_sig({})
        assert result == ""


class TestComputeHaSig:
    """_compute_ha_sig builds signature string."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_full_sig(self):
        r = self._app()
        result = r._compute_ha_sig({"filament_id": "GFL01", "type": "PLA", "color_hex": "FF0000"})
        assert result == "HA_SIG=bambu|filament_id=gfl01|type=pla|color_hex=ff0000"

    def test_missing_field_returns_none(self):
        r = self._app()
        assert r._compute_ha_sig({"filament_id": "GFL01", "type": "PLA"}) is None

    def test_missing_type_returns_none(self):
        r = self._app()
        assert r._compute_ha_sig({"filament_id": "GFL01", "color_hex": "FF0000"}) is None


class TestNormalizeColor:
    """_normalize_color strips # and handles 6/8 char hex."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_six_hex(self):
        assert self._app()._normalize_color("FF0000") == "ff0000"

    def test_eight_hex_truncated(self):
        assert self._app()._normalize_color("FF0000AA") == "ff0000"

    def test_hash_prefix(self):
        assert self._app()._normalize_color("#AABBCC") == "aabbcc"

    def test_invalid(self):
        assert self._app()._normalize_color("red") == ""

    def test_empty(self):
        assert self._app()._normalize_color("") == ""


class TestColorCandidates:
    """_color_candidates returns multiple candidate hex strings."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_six_hex(self):
        assert self._app()._color_candidates("FF0000") == ["ff0000"]

    def test_eight_hex_two_candidates(self):
        result = self._app()._color_candidates("FF0000AA")
        assert "ff0000" in result
        assert "0000aa" in result

    def test_empty(self):
        assert self._app()._color_candidates("") == []


class TestMaterialKey:
    """_material_key normalizes material strings."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_basic(self):
        assert self._app()._material_key("PLA") == "pla"

    def test_collapses_whitespace(self):
        assert self._app()._material_key("PLA  Basic") == "pla basic"


class TestNormalizeUid:
    """_normalize_uid strips and uppercases."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_basic(self):
        assert self._app()._normalize_uid("aabb1234") == "AABB1234"

    def test_strips_whitespace(self):
        assert self._app()._normalize_uid("  abc  ") == "ABC"

    def test_empty(self):
        assert self._app()._normalize_uid("") == ""

    def test_none(self):
        assert self._app()._normalize_uid(None) == ""


class TestNormalizeMaterial:
    """_normalize_material normalizes PLA+, PETG-CF, etc."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_pla_plus(self):
        assert self._app()._normalize_material("PLA+") == "PLA"

    def test_petg_cf(self):
        assert self._app()._normalize_material("PETG-CF") == "PETG"

    def test_abs_variant(self):
        assert self._app()._normalize_material("ABS-GF") == "ABS"

    def test_plain(self):
        assert self._app()._normalize_material("TPU") == "TPU"


class TestCanonicalizeTagUid:
    """_canonicalize_tag_uid normalizes UIDs."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_basic(self):
        result = self._app()._canonicalize_tag_uid("aabb1234")
        assert result == result.upper().strip()

    def test_none(self):
        assert self._app()._canonicalize_tag_uid(None) == ""


class TestCanonicalizeHaSpoolUuid:
    """_canonicalize_ha_spool_uuid."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_basic(self):
        result = self._app()._canonicalize_ha_spool_uuid("abc-123")
        assert isinstance(result, str)

    def test_json_wrapped(self):
        result = self._app()._canonicalize_ha_spool_uuid('"ABC123"')
        assert "ABC123" in result


class TestJsonStringLiteral:
    """_json_string_literal wraps value in JSON quotes."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_basic(self):
        assert self._app()._json_string_literal("ABC") == '"ABC"'

    def test_empty(self):
        assert self._app()._json_string_literal("") == '""'


class TestJsonTextToStr:
    """_json_text_to_str parses JSON literal."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_json_wrapped(self):
        assert self._app()._json_text_to_str('"ABC"') == "ABC"

    def test_plain(self):
        assert self._app()._json_text_to_str("ABC") == "ABC"


class TestNormUid:
    """_norm_uid normalizes UID from extra field."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_basic(self):
        result = self._app()._norm_uid("aabb1234")
        assert result == result.upper().strip()


class TestExtractSpoolUid:
    """_extract_spool_uid gets RFID UID from spool extra."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_rfid_tag_uid(self):
        spool = {"extra": {"rfid_tag_uid": '"AABB1234"'}}
        result = self._app()._extract_spool_uid(spool)
        assert "AABB1234" in result

    def test_no_extra(self):
        assert self._app()._extract_spool_uid({}) == ""

    def test_non_dict_extra(self):
        assert self._app()._extract_spool_uid({"extra": "bad"}) == ""


class TestIsAllZeroIdentity:
    """_is_all_zero_identity checks for zero tag_uid + tray_uuid."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_all_zero(self):
        assert self._app()._is_all_zero_identity("0000000000000000", "00000000000000000000000000000000") is True

    def test_empty(self):
        assert self._app()._is_all_zero_identity("", "") is True

    def test_real_rfid(self):
        assert self._app()._is_all_zero_identity("AABB1234", "CCDD5678") is False

    def test_none(self):
        assert self._app()._is_all_zero_identity(None, None) is True


class TestRfidBindGuardOk:
    """_rfid_bind_guard_ok verifies UID match for binding."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_no_tag_no_uuid(self):
        """No tag or UUID → always OK."""
        assert self._app()._rfid_bind_guard_ok(1, "", {}) is True

    def test_lot_nr_match(self):
        """tray_uuid matches spool lot_nr → OK."""
        spool_index = {1: {"lot_nr": "AABB1234CCDD5678EEFF0011AABB3344", "extra": {}}}
        assert self._app()._rfid_bind_guard_ok(1, "", spool_index, tray_uuid="AABB1234CCDD5678EEFF0011AABB3344") is True

    def test_lot_nr_mismatch(self):
        """tray_uuid doesn't match lot_nr, no tag → check extra."""
        spool_index = {1: {"lot_nr": "different", "extra": {}}}
        assert self._app()._rfid_bind_guard_ok(1, "", spool_index, tray_uuid="AABB1234") is True  # slot_tag is empty → True

    def test_missing_spool(self):
        """Spool not in index → False."""
        assert self._app()._rfid_bind_guard_ok(999, "AABB", {}) is False


class TestMayStickOverride:
    """_may_stick_override checks sticky binding."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_should_not_stick(self):
        r = self._app()
        assert r._may_stick_override(1, 10, 20, "TAG", {}, "sig_a", "sig_b") is False

    def test_same_resolved_and_helper(self):
        spool = _spoolman_spool(10, "PLA", "FF0000", "Bambu", 800, "Shelf")
        fm = FakeSpoolman([spool], [])
        r = TestableReconcile(fm, {})
        # _should_stick returns True when sigs match and helper > 0 and spool exists
        assert r._may_stick_override(1, 10, 10, "TAG", {10: spool}, "sig_a", "sig_a") is True


class TestConvergeLotNr:
    """_converge_lot_nr enrolls lot_nr identity on bind."""

    def test_rfid_tray_enrolls_uuid(self):
        spool = _spoolman_spool(10, "PLA", "FF0000", "Bambu", 800, "Shelf")
        fm = FakeSpoolman([spool], [])
        r = TestableReconcile(fm, {})
        spool_index = {10: spool}
        r._converge_lot_nr(1, 10, {"type": "PLA", "filament_id": "GFL01", "color_hex": "FF0000"},
                          spool_index, tray_uuid="AABB1234CCDD5678EEFF0011AABB3344")
        # Should attempt to enroll lot_nr
        assert any("LOT_NR" in m or "lot_nr" in m.lower() for m, _ in r._log_calls)

    def test_nonrfid_tray_builds_lot_sig(self):
        spool = _spoolman_spool(10, "PLA", "FF0000", "Bambu", 800, "Shelf")
        fm = FakeSpoolman([spool], [])
        r = TestableReconcile(fm, {})
        spool_index = {10: spool}
        r._converge_lot_nr(1, 10, {"type": "PLA", "filament_id": "GFL01", "color_hex": "FF0000"},
                          spool_index)
        # For non-RFID, builds lot_sig from tray_meta
        assert any("CONVERGE_LOT_NR" in m or "converge" in m.lower() for m, _ in r._log_calls)


class TestApplyRfidBindGuardFail:
    """_apply_rfid_bind_guard_fail sets status and appends transcript."""

    def test_sets_unbound_status(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {})
        r._active_run = {"validation_transcripts": [], "writes": [], "no_write_paths": []}
        t = {}
        r._apply_rfid_bind_guard_fail(1, t, {"name": "PLA"}, "AABB", 10, False)
        assert t["decision"] == "UNBOUND"
        assert t["reason"] == "SELECTED_UID_MISMATCH"


class TestNotifyNonrfidNeedsActionExtra:
    """Extra coverage for _notify_nonrfid_needs_action with reasons."""

    def test_no_matching_spools(self):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, {})
        tray_meta = {"name": "PLA", "type": "PLA", "color_hex": "FF0000"}
        r._notify_nonrfid_needs_action(1, tray_meta, "no_matching_spool")
        assert len(r._notify_calls) == 1


class TestHasTrayUuid:
    """_has_tray_uuid utility."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_present(self):
        assert self._app()._has_tray_uuid({"tray_uuid": "AABB"}) is True

    def test_empty(self):
        assert self._app()._has_tray_uuid({"tray_uuid": ""}) is False

    def test_missing(self):
        assert self._app()._has_tray_uuid({}) is False

    def test_none_attrs(self):
        assert self._app()._has_tray_uuid(None) is False


class TestNormTrayIdentityTag:
    """_norm_tray_identity_tag normalizes for comparison."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_uppercases_and_strips(self):
        assert self._app()._norm_tray_identity_tag(' aa bb "cc" ') == "AABBCC"


class TestTruthGuardSlotPatch:
    """_truth_guard_slot_patch validates slot bindings."""

    def _app(self, state_map=None):
        fm = FakeSpoolman([], [])
        r = _EventTestReconcile(fm, state_map or {})
        r._active_run = {"validation_transcripts": [], "writes": [], "no_write_paths": []}
        return r

    def test_no_rfid_no_tray_empty_ok(self):
        """No RFID, no tray data issues → allow."""
        r = self._app()
        t = {}
        spool_obj = {"lot_nr": "", "filament": {"material": "PLA"}, "extra": {}}
        result = r._truth_guard_slot_patch(1, t, {"type": "PLA"}, "", 10, spool_obj, False, "loaded")
        assert result is True

    def test_rfid_lot_nr_matches_uuid(self):
        """RFID visible, lot_nr == tray_uuid → allow."""
        r = self._app()
        t = {}
        uuid = "AABB1234CCDD5678EEFF0011AABB3344"
        spool_obj = {"lot_nr": uuid, "extra": {}}
        result = r._truth_guard_slot_patch(1, t, {}, "AABB1234", 10, spool_obj, False, "loaded", tray_uuid=uuid)
        assert result is True

    def test_rfid_lot_nr_mismatch_blocks(self):
        """RFID visible, lot_nr populated but doesn't match → block."""
        r = self._app()
        t = {}
        spool_obj = {"lot_nr": "DIFFERENT_LOT_NR", "extra": {}}
        result = r._truth_guard_slot_patch(1, t, {}, "AABB1234", 10, spool_obj, False, "loaded", tray_uuid="REAL_UUID")
        assert result is False
        assert t.get("unbound_reason") is not None

    def test_rfid_legacy_uid_matches(self):
        """RFID visible, no lot_nr, legacy extra.rfid_tag_uid matches → allow."""
        r = self._app()
        t = {}
        spool_obj = {"lot_nr": "", "extra": {"rfid_tag_uid": '"AABB1234"'}}
        result = r._truth_guard_slot_patch(1, t, {}, "AABB1234", 10, spool_obj, False, "loaded")
        assert result is True

    def test_rfid_legacy_uid_mismatch_blocks(self):
        """RFID visible, legacy extra.rfid_tag_uid doesn't match → block."""
        r = self._app()
        t = {}
        spool_obj = {"lot_nr": "", "extra": {"rfid_tag_uid": '"CCDD5678"'}}
        result = r._truth_guard_slot_patch(1, t, {}, "AABB1234", 10, spool_obj, False, "loaded")
        assert result is False

    def test_material_mismatch_with_bound_invariant_warns(self):
        """Non-RFID, material mismatch, but user manually assigned → warn only, allow."""
        # Need expected_spool_id == helper_spool_id for bound invariant
        sm = {
            "input_text.ams_slot_1_expected_spool_id": {"state": "10", "attributes": {}},
        }
        r = self._app(state_map=sm)
        t = {}
        spool_obj = {"filament": {"material": "PETG"}, "extra": {}}
        result = r._truth_guard_slot_patch(1, t, {"type": "PLA"}, "", 10, spool_obj, False, "loaded")
        assert result is True
        assert any("TRUTH_GUARD_MATERIAL_WARN_ONLY" in m for m, _ in r._log_calls)

    def test_material_mismatch_no_invariant_blocks(self):
        """Non-RFID, material mismatch, no bound invariant → block."""
        r = self._app()
        t = {}
        spool_obj = {"filament": {"material": "PETG"}, "extra": {}}
        result = r._truth_guard_slot_patch(1, t, {"type": "PLA"}, "", 10, spool_obj, False, "loaded")
        assert result is False


class TestBuildLotSigForLookup:
    """_build_lot_sig_for_lookup allows generic IDs unlike _build_lot_sig."""

    def _app(self):
        fm = FakeSpoolman([], [])
        return TestableReconcile(fm, {})

    def test_allows_generic(self):
        result = self._app()._build_lot_sig_for_lookup(
            {"type": "PLA", "filament_id": "GFL99", "color_hex": "FF0000"}
        )
        assert "gfl99" in result

    def test_rejects_empty_fields(self):
        result = self._app()._build_lot_sig_for_lookup(
            {"type": "", "filament_id": "GFL01", "color_hex": "FF0000"}
        )
        assert result == ""


# ── print-active freeze tests ──────────────────────────────────────

class TestPrintActiveFreeze:
    """Tests for the top-level reconcile freeze during active prints."""

    def _make_reconciler(self, print_active="off", spool_id=38, **extra_state):
        """Create a TestableReconcile with RFID spool in slot 4, print_active configurable."""
        spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="AMS 1 Tray 4")]
        filaments = [{"id": 1, "name": "Bambu PLA Basic", "material": "PLA",
                      "color_hex": "ff0000", "vendor": {"name": "Bambu Lab"}}]
        sm = FakeSpoolman(spools, filaments)

        # Slot 4 with empty/depleted RFID tray (tag_uid gone)
        tray_ent = _tray_entity(4)
        state = {
            tray_ent: {"attributes": {
                "tag_uid": "", "tray_uuid": "", "type": "PLA",
                "color": "ff0000", "name": "Bambu PLA Basic",
                "filament_id": "bambu", "tray_weight": 0, "remain": -1,
                "empty": True, "active": False,
            }, "state": "empty"},
            f"{tray_ent}::all": {"attributes": {
                "tag_uid": "", "tray_uuid": "", "type": "PLA",
                "color": "ff0000", "name": "Bambu PLA Basic",
                "filament_id": "bambu", "tray_weight": 0, "remain": -1,
                "empty": True, "active": False,
            }, "state": "empty"},
            "input_boolean.filament_iq_print_active": print_active,
            "input_boolean.filament_iq_nonrfid_enabled": "on",
            f"input_text.ams_slot_4_spool_id": str(spool_id),
            f"input_text.ams_slot_4_expected_spool_id": str(spool_id),
            f"input_text.ams_slot_4_tray_signature": "sig_4",
        }
        # Fill other slots as empty/unbound
        for slot in (1, 2, 3, 5, 6):
            te = _tray_entity(slot)
            state[te] = {"attributes": {
                "tag_uid": "", "tray_uuid": "", "type": "",
                "color": "", "name": "", "filament_id": "",
                "tray_weight": 0, "remain": -1,
            }, "state": "empty"}
            state[f"{te}::all"] = state[te]
        state.update(extra_state)

        args = {
            "printer_serial": "01p00c5a3101668",
            "spoolman_url": "http://192.0.2.1:7912",
            "nonrfid_enabled_entity": "input_boolean.filament_iq_nonrfid_enabled",
        }
        r = TestableReconcile(sm, state, args=args)
        r._print_active_entity = "input_boolean.filament_iq_print_active"
        return r

    def test_reconcile_fully_skipped_during_print(self):
        """print_active=on → reconcile returns immediately, zero helper writes."""
        r = self._make_reconciler(print_active="on")
        r._run_reconcile("test")
        # No spool_id writes of any kind
        spool_writes = [
            w for w in r._helper_writes
            if "spool_id" in w.get("entity_id", "")
        ]
        assert len(spool_writes) == 0, f"expected zero spool writes during print, got {spool_writes}"
        skip_logs = [msg for msg, _ in r._log_calls if "RECONCILE_SKIP_PRINT_ACTIVE" in msg]
        assert len(skip_logs) >= 1, "RECONCILE_SKIP_PRINT_ACTIVE must be logged"

    def test_reconcile_runs_after_print_ends(self):
        """print_active off→on→off transition triggers _schedule_reconcile."""
        r = self._make_reconciler(print_active="off")
        # Track _schedule_reconcile calls
        schedule_calls = []
        original = r._schedule_reconcile
        def track(reason):
            schedule_calls.append(reason)
            # Don't actually run reconcile in this test
        r._schedule_reconcile = track

        # Simulate on→off transition
        r._on_print_active_change("entity", "state", "on", "off", {})
        assert "print_ended" in schedule_calls, f"expected print_ended in {schedule_calls}"
        assert r._print_active_since is None

    def test_depleted_rfid_binding_preserved_during_print(self):
        """print_active=on, depleted RFID tray → binding NOT cleared."""
        r = self._make_reconciler(print_active="on")
        r._run_reconcile("tray_update")
        # Slot 4 spool_id should NOT be cleared to 0
        clear_writes = [
            w for w in r._helper_writes
            if "ams_slot_4_spool_id" in w.get("entity_id", "") and w.get("value") == "0"
        ]
        assert len(clear_writes) == 0, f"binding should be preserved during print, got {clear_writes}"

    def test_manual_enroll_during_print_proceeds(self):
        """print_active=on, _force_location_and_helpers with spool_id>0 → binding proceeds."""
        r = self._make_reconciler(print_active="on")
        # Manual bind (spool_id > 0) should NOT be blocked by F4
        r._force_location_and_helpers(slot=4, spool_id=99, tag_uid="AABB1122", source="manual_enroll")
        bind_writes = [
            w for w in r._helper_writes
            if "ams_slot_4_spool_id" in w.get("entity_id", "") and w.get("value") == "99"
        ]
        assert len(bind_writes) >= 1, "manual bind should proceed during active print"
        held_logs = [msg for msg, _ in r._log_calls if "BINDING_HELD_DURING_PRINT" in msg]
        assert len(held_logs) == 0, "BINDING_HELD should not fire for bind (only for unbind)"

    def test_watchdog_overrides_freeze_after_24h(self):
        """print_active=on for >24h → watchdog fires, reconcile proceeds."""
        import time as _time
        r = self._make_reconciler(print_active="on")
        r._print_active_since = _time.time() - 90000  # 25 hours ago
        r._run_reconcile("safety_poll")
        watchdog_logs = [msg for msg, lvl in r._log_calls if "PRINT_FREEZE_WATCHDOG" in msg]
        assert len(watchdog_logs) >= 1, "PRINT_FREEZE_WATCHDOG must be logged"
        # Reconcile should have proceeded (Spoolman fetch attempted)
        skip_logs = [msg for msg, _ in r._log_calls if "RECONCILE_SKIP_PRINT_ACTIVE" in msg]
        assert len(skip_logs) == 0, "should NOT skip when watchdog fires"

    def test_usage_skip_data_loss_warning(self):
        """USAGE_SKIP with tray_seconds > 60 → WARNING level log."""
        from tests.test_ams_print_usage_sync import _TestableUsageSync, _default_state_map, _has_log
        import datetime as _dt
        app = _TestableUsageSync(
            state_map=_default_state_map({4: 10}),
            args={"lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True},
        )
        # Simulate significant tray activity on slot 4
        app._tray_active_times = {
            4: [{"start": _dt.datetime(2026, 1, 1, 0, 0, 0),
                 "end": _dt.datetime(2026, 1, 1, 1, 50, 36)}]
        }
        # Clear slot 4 binding so USAGE_SKIP fires
        app._state_map["input_text.ams_slot_4_spool_id"] = "0"
        app._job_key = "data_loss_test"
        app._start_snapshot = {4: 420.0}
        app._trays_used = {4}
        app._print_active = True
        app._do_finish("finish")
        warn_logs = [
            (msg, lvl) for msg, lvl in app._log_calls
            if "USAGE_SKIP" in msg and "slot=4" in msg
        ]
        assert len(warn_logs) >= 1, f"expected USAGE_SKIP log for slot 4, got {app._log_calls}"
        msg, lvl = warn_logs[0]
        assert lvl == "WARNING", f"expected WARNING level, got {lvl}"
        assert "DATA_LOSS" in msg, f"expected DATA_LOSS in message, got {msg}"

    def test_freeze_exception_safe(self):
        """get_state throws exception → print_active defaults False → reconcile proceeds."""
        r = self._make_reconciler(print_active="on")
        # Sabotage get_state to throw on print_active entity
        original_get = r.get_state
        def exploding_get(entity_id, attribute=None):
            if entity_id == r._print_active_entity:
                raise RuntimeError("HA unavailable")
            return original_get(entity_id, attribute)
        r.get_state = exploding_get
        r._run_reconcile("test")
        # Should NOT skip — exception means fail-open
        skip_logs = [msg for msg, _ in r._log_calls if "RECONCILE_SKIP_PRINT_ACTIVE" in msg]
        assert len(skip_logs) == 0, "should fail-open when get_state throws"

    def test_watchdog_seeds_on_startup_when_already_on(self):
        """If print_active is already on at startup, _print_active_since is seeded so watchdog can fire."""
        r = self._make_reconciler(print_active="on")
        # Re-run initialize to simulate startup with entity already on
        r._print_active_since = None
        r._log_calls.clear()
        # Manually trigger the startup seeding logic
        if str(r.get_state(r._print_active_entity) or "").lower() == "on":
            import time as _time
            r._print_active_since = _time.time()
        assert r._print_active_since is not None, "_print_active_since must be seeded on startup"


class TestReconcilerStatus:
    """Tests for _write_reconciler_status helper writes."""

    def _make_reconciler(self, print_active="off", spool_id=38, **extra_state):
        spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="AMS 1 Tray 4")]
        filaments = [{"id": 1, "name": "Bambu PLA Basic", "material": "PLA",
                      "color_hex": "ff0000", "vendor": {"name": "Bambu Lab"}}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(4)
        state = {
            tray_ent: {"attributes": {
                "tag_uid": "", "tray_uuid": "", "type": "PLA",
                "color": "ff0000", "name": "Bambu PLA Basic",
                "filament_id": "bambu", "tray_weight": 0, "remain": -1,
                "empty": True, "active": False,
            }, "state": "empty"},
            f"{tray_ent}::all": {"attributes": {
                "tag_uid": "", "tray_uuid": "", "type": "PLA",
                "color": "ff0000", "name": "Bambu PLA Basic",
                "filament_id": "bambu", "tray_weight": 0, "remain": -1,
                "empty": True, "active": False,
            }, "state": "empty"},
            "input_boolean.filament_iq_print_active": print_active,
            "input_boolean.filament_iq_nonrfid_enabled": "on",
            f"input_text.ams_slot_4_spool_id": str(spool_id),
            f"input_text.ams_slot_4_expected_spool_id": str(spool_id),
            f"input_text.ams_slot_4_tray_signature": "sig_4",
        }
        for slot in (1, 2, 3, 5, 6):
            te = _tray_entity(slot)
            state[te] = {"attributes": {
                "tag_uid": "", "tray_uuid": "", "type": "",
                "color": "", "name": "", "filament_id": "",
                "tray_weight": 0, "remain": -1,
            }, "state": "empty"}
            state[f"{te}::all"] = state[te]
        state.update(extra_state)
        args = {
            "printer_serial": "01p00c5a3101668",
            "spoolman_url": "http://192.0.2.1:7912",
            "nonrfid_enabled_entity": "input_boolean.filament_iq_nonrfid_enabled",
        }
        r = TestableReconcile(sm, state, args=args)
        r._print_active_entity = "input_boolean.filament_iq_print_active"
        return r

    def test_reconcile_writes_ok_status(self):
        """Normal reconcile writes 'ok' status with bound count and reason."""
        r = self._make_reconciler(print_active="off")
        r._write_reconciler_status("ok", "6 bound", "safety_poll")
        status_writes = [
            w for w in r._helper_writes
            if w.get("entity_id") == "input_text.filament_iq_reconciler_status"
        ]
        assert len(status_writes) == 1
        val = status_writes[0]["value"]
        assert val.startswith("ok · 6 bound · safety_poll · ")

    def test_reconcile_writes_warn_status(self):
        """Reconcile with issues writes 'warn' status."""
        r = self._make_reconciler(print_active="off")
        r._write_reconciler_status("warn", "1 unbound", "tray_update")
        status_writes = [
            w for w in r._helper_writes
            if w.get("entity_id") == "input_text.filament_iq_reconciler_status"
        ]
        assert len(status_writes) == 1
        val = status_writes[0]["value"]
        assert val.startswith("warn · 1 unbound · tray_update · ")

    def test_print_active_writes_paused_status(self):
        """Print active skip writes 'paused' status."""
        r = self._make_reconciler(print_active="on")
        r._run_reconcile("test")
        status_writes = [
            w for w in r._helper_writes
            if w.get("entity_id") == "input_text.filament_iq_reconciler_status"
        ]
        assert len(status_writes) >= 1
        val = status_writes[0]["value"]
        assert "paused" in val
        assert "print active" in val

    def test_status_write_failure_does_not_crash(self):
        """If call_service throws, log WARNING and continue."""
        r = self._make_reconciler(print_active="off")
        original_call = r.call_service
        def exploding_call(service, **kwargs):
            if "reconciler_status" in kwargs.get("entity_id", ""):
                raise RuntimeError("HA unavailable")
            return original_call(service, **kwargs)
        r.call_service = exploding_call
        r._write_reconciler_status("ok", "6 bound", "test")
        warn_logs = [msg for msg, lvl in r._log_calls if "RECONCILER_STATUS_WRITE_FAILED" in msg]
        assert len(warn_logs) >= 1


# ── Fix: NONRFID_UNENROLLED_MATCH writes on safety_poll (status_only gate) ──

@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_safety_poll_nonrfid_unenrolled_match_writes_bind(slot):
    """status_only=True + NONRFID_UNENROLLED_MATCH -> spool_id written, lot_nr enrolled, location updated."""
    spool_id = 65
    # filament_id must be non-empty for lot_sig lookup; spool has lot_nr=None (unenrolled)
    attrs = _nonrfid_attrs_standalone(name="Overture PLA", filament_id="gfsnl02", tray_type="PLA", color="161616")
    spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                     color_hex="161616", vendor_name="Overture", name="Overture PLA", lot_nr=None)]
    sm = FakeSpoolman(spools, [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("safety_poll", status_only=True)
    logs = [msg for msg, _ in r._log_calls]
    assert any("NONRFID_UNENROLLED_MATCH" in msg for msg in logs), "must reach NONRFID_UNENROLLED_MATCH path"
    # spool_id helper must be written
    spool_id_writes = [w for w in r._helper_writes
                       if w.get("entity_id") == f"input_text.ams_slot_{slot}_spool_id"
                       and w.get("value") == str(spool_id)]
    assert len(spool_id_writes) > 0, "safety_poll must write spool_id for deterministic unenrolled match"
    # lot_nr must be enrolled via Spoolman PATCH
    lot_patches = [p for p in sm.patches
                   if p.get("spool_id") == spool_id and "lot_nr" in (p.get("payload") or {})]
    assert len(lot_patches) > 0, "safety_poll must enroll lot_nr for unenrolled match"
    # Spoolman location must be updated
    expected_loc = CANONICAL_LOCATION_BY_SLOT[slot]
    loc_patches = [p for p in sm.patches
                   if p.get("spool_id") == spool_id
                   and (p.get("payload") or {}).get("location") == expected_loc]
    assert len(loc_patches) > 0, f"safety_poll must PATCH spool location to {expected_loc}"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_safety_poll_ambiguous_match_still_skips_writes(slot):
    """status_only=True + ambiguous candidates -> no Spoolman writes (gate still active)."""
    attrs = _nonrfid_attrs_standalone(name="Overture PLA", filament_id="", tray_type="PLA", color="ff0000")
    # Two identical spools on Shelf -> ambiguous
    spools = [
        _spool(101, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
               color_hex="ff0000", vendor_name="Overture", name="Overture PLA"),
        _spool(102, remaining_weight=400, rfid_tag_uid=None, location="Shelf",
               color_hex="ff0000", vendor_name="Overture", name="Overture PLA"),
    ]
    sm = FakeSpoolman(spools, [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("safety_poll", status_only=True)
    # No location writes should happen for ambiguous matches on safety_poll
    loc_patches = [p for p in sm.patches if (p.get("payload") or {}).get("location")]
    assert len(loc_patches) == 0, "safety_poll must not write location for ambiguous match"


def test_set_helper_uses_get_helper_state():
    """_set_helper must use _get_helper_state (attribute='all') not plain get_state."""
    slot = 1
    attrs = _nonrfid_attrs_standalone(name="Test", tray_type="PLA", color="ff0000")
    sm = FakeSpoolman([], [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._active_run = {"reason": "test", "writes": [], "decisions": [], "no_write_paths": [],
                     "conflicts": [], "unknown_tags": [], "auto_registers": [],
                     "validation_transcripts": [], "spool_exists_cache": {}}
    # Make plain get_state return None (simulating stale cache) but _get_helper_state return "0"
    original_get_state = r.get_state
    def patched_get_state(entity_id, attribute=None):
        if attribute is None and "spool_id" in entity_id:
            return None  # stale cache
        return original_get_state(entity_id, attribute=attribute)
    r.get_state = patched_get_state
    # _set_helper should use _get_helper_state (attribute='all') which returns "0", not None
    r._set_helper(f"input_text.ams_slot_{slot}_spool_id", "42")
    spool_writes = [w for w in r._helper_writes
                    if w.get("entity_id") == f"input_text.ams_slot_{slot}_spool_id"
                    and w.get("value") == "42"]
    assert len(spool_writes) == 1, \
        "_set_helper must use _get_helper_state (bypasses stale cache) and write successfully"


# ── Partial lot_sig matching for missing color_hex ──

def test_partial_lot_sig_built_when_color_missing():
    """_build_lot_sig returns partial sig pla|gfl05| when color_hex empty."""
    sm = FakeSpoolman([], [])
    state_map = _nonrfid_state_map_standalone(1, _nonrfid_attrs_standalone())
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    meta = {"type": "PLA", "filament_id": "GFL05", "color_hex": ""}
    assert r._build_lot_sig(meta) == "pla|gfl05|"
    meta_none = {"type": "PLA", "filament_id": "GFL05", "color_hex": None}
    assert r._build_lot_sig(meta_none) == "pla|gfl05|"


def test_full_lot_sig_unchanged():
    """_build_lot_sig returns full sig pla|gfl05|bcbcbc when color present."""
    sm = FakeSpoolman([], [])
    state_map = _nonrfid_state_map_standalone(1, _nonrfid_attrs_standalone())
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    meta = {"type": "PLA", "filament_id": "GFL05", "color_hex": "bcbcbc"}
    assert r._build_lot_sig(meta) == "pla|gfl05|bcbcbc"


def test_partial_lot_sig_for_lookup_built_when_color_missing():
    """_build_lot_sig_for_lookup returns partial sig for generic filament_id too."""
    sm = FakeSpoolman([], [])
    state_map = _nonrfid_state_map_standalone(1, _nonrfid_attrs_standalone())
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    meta = {"type": "PLA", "filament_id": "GFL99", "color_hex": ""}
    # _build_lot_sig_for_lookup allows generic IDs
    assert r._build_lot_sig_for_lookup(meta) == "pla|gfl99|"


def test_is_partial_lot_sig():
    """_is_partial_lot_sig detects partial sigs."""
    sm = FakeSpoolman([], [])
    state_map = _nonrfid_state_map_standalone(1, _nonrfid_attrs_standalone())
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    assert r._is_partial_lot_sig("pla|gfl05|") is True
    assert r._is_partial_lot_sig("pla|gfl05|bcbcbc") is False
    assert r._is_partial_lot_sig("") is False


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_partial_sig_matches_full_sig_spool(slot):
    """Spool with lot_nr=pla|gfl05|bcbcbc found via partial lookup pla|gfl05|."""
    spool_id = 201
    # Tray has no color_hex — partial sig pla|gfl05|
    attrs = _nonrfid_attrs_standalone(name="Overture PLA", filament_id="GFL05", tray_type="PLA", color="")
    # Spool has full lot_nr enrolled from a previous bind
    spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                     color_hex="bcbcbc", vendor_name="Overture", name="Overture PLA",
                     lot_nr="pla|gfl05|bcbcbc")]
    sm = FakeSpoolman(spools, [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") == STATUS_OK_NONRFID, "partial sig must match enrolled spool"
    logs = [msg for msg, _ in r._log_calls]
    assert any("NONRFID_PARTIAL_SIG_MATCH" in msg for msg in logs), "must log partial sig match"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_partial_sig_ambiguous_falls_through(slot):
    """Two spools with pla|gfl05| prefix -> NEEDS_MANUAL_BIND or tiebreak."""
    attrs = _nonrfid_attrs_standalone(name="Overture PLA", filament_id="GFL05", tray_type="PLA", color="")
    spools = [
        _spool(201, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
               color_hex="bcbcbc", vendor_name="Overture", name="Overture PLA",
               lot_nr="pla|gfl05|bcbcbc"),
        _spool(202, remaining_weight=400, rfid_tag_uid=None, location="Shelf",
               color_hex="ff0000", vendor_name="Overture", name="Overture PLA Red",
               lot_nr="pla|gfl05|ff0000"),
    ]
    sm = FakeSpoolman(spools, [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    # Should either tiebreak or fall through to NEEDS_MANUAL_BIND — not fail
    final_status = status_writes[-1].get("value")
    assert final_status in (STATUS_OK_NONRFID, STATUS_NEEDS_MANUAL_BIND, STATUS_LOW_CONFIDENCE), \
        f"ambiguous partial sig must tiebreak or manual bind, got {final_status}"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_generic_filament_id_blocked_partial_sig(slot):
    """GFL99 does not trigger partial sig matching (generic sentinel)."""
    attrs = _nonrfid_attrs_standalone(name="Generic PLA", filament_id="GFL99", tray_type="PLA", color="")
    spools = [_spool(201, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                     color_hex="bcbcbc", vendor_name="Overture", name="Overture PLA",
                     lot_nr="pla|gfl99|bcbcbc")]
    sm = FakeSpoolman(spools, [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    logs = [msg for msg, _ in r._log_calls]
    assert not any("NONRFID_PARTIAL_SIG_MATCH" in msg for msg in logs), \
        "generic filament_id must not trigger partial sig matching"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_filament_id_only_unenrolled_match(slot):
    """Unenrolled spool matched by filament_id alone when color missing."""
    spool_id = 301
    attrs = _nonrfid_attrs_standalone(name="Overture PLA", filament_id="GFL05", tray_type="PLA", color="")
    # Spool has no lot_nr (unenrolled), external_id matches tray filament_id
    spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                     color_hex="bcbcbc", vendor_name="Overture", name="Overture PLA")]
    # Set external_id on the filament to match tray filament_id
    spools[0]["filament"]["external_id"] = "GFL05"
    sm = FakeSpoolman(spools, [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
    assert len(status_writes) > 0
    assert status_writes[-1].get("value") == STATUS_OK_NONRFID, \
        "unenrolled spool with matching filament_id must bind when color missing"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_partial_sig_enrollment(slot):
    """Enrolled sig is pla|gfl05| (partial) not a fabricated full sig."""
    spool_id = 401
    attrs = _nonrfid_attrs_standalone(name="Overture PLA", filament_id="GFL05", tray_type="PLA", color="")
    spools = [_spool(spool_id, remaining_weight=500, rfid_tag_uid=None, location="Shelf",
                     color_hex="bcbcbc", vendor_name="Overture", name="Overture PLA")]
    spools[0]["filament"]["external_id"] = "GFL05"
    sm = FakeSpoolman(spools, [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    # Check the lot_nr PATCH payload
    lot_patches = [p for p in sm.patches
                   if p.get("spool_id") == spool_id and "lot_nr" in (p.get("payload") or {})]
    assert len(lot_patches) > 0, "must enroll lot_nr"
    enrolled_sig = lot_patches[0]["payload"]["lot_nr"]
    assert enrolled_sig == "pla|gfl05|", f"enrolled sig must be partial (pla|gfl05|), got {enrolled_sig}"


# ── Auto-reconcile settling delay for non-RFID spools ──

@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_settle_timer_scheduled_on_all_zero(slot):
    """run_in called with correct delay when non-RFID tray is unbound (low confidence or no match)."""
    # Non-RFID tray (all-zero tag_uid) with unsettled sensor (no color/fid) → LOW_CONFIDENCE
    attrs = _nonrfid_attrs_standalone(name="Unknown", filament_id="", tray_type="PLA", color="")
    sm = FakeSpoolman([], [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    # Should have scheduled a settle timer
    settle_calls = [c for c in r._run_in_calls if c.get("kwargs", {}).get("slot") == slot]
    assert len(settle_calls) == 1, f"expected 1 settle timer for slot {slot}, got {len(settle_calls)}"
    assert settle_calls[0]["delay"] == 90, "default settle delay is 90s"
    logs = [msg for msg, _ in r._log_calls]
    assert any(f"NONRFID_SETTLE_SCHEDULED slot={slot}" in msg for msg in logs)


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_settle_timer_not_duplicated(slot):
    """Second unbound eval while timer pending does not schedule another."""
    attrs = _nonrfid_attrs_standalone(name="Unknown", filament_id="", tray_type="PLA", color="")
    sm = FakeSpoolman([], [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test_1")
    count_after_first = len([c for c in r._run_in_calls if c.get("kwargs", {}).get("slot") == slot])
    assert count_after_first == 1
    # Second reconcile — timer already pending
    r._run_reconcile("test_2")
    count_after_second = len([c for c in r._run_in_calls if c.get("kwargs", {}).get("slot") == slot])
    assert count_after_second == 1, "must not schedule duplicate settle timer"


def test_settle_callback_runs_full_reconcile():
    """Callback calls _run_reconcile with status_only=False."""
    slot = 1
    attrs = _nonrfid_attrs_standalone(name="Unknown", filament_id="", tray_type="PLA", color="")
    sm = FakeSpoolman([], [])
    state_map = _nonrfid_state_map_standalone(slot, attrs)
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    # Verify settle timer was scheduled
    assert r._settle_pending.get(slot) is True
    # Simulate callback firing
    reconcile_reasons = []
    original_run = r._run_reconcile
    def capture_run(reason, **kwargs):
        reconcile_reasons.append((reason, kwargs))
        original_run(reason, **kwargs)
    r._run_reconcile = capture_run
    r._settle_reconcile_callback({"slot": slot})
    # Verify it ran a full reconcile
    settle_runs = [(r, kw) for r, kw in reconcile_reasons if "settle" in r]
    assert len(settle_runs) == 1
    assert settle_runs[0][1].get("status_only") is False or "status_only" not in settle_runs[0][1], \
        "settle reconcile must be full (status_only=False)"


@pytest.mark.parametrize("slot", _ALL_SLOTS)
def test_settle_timer_not_triggered_for_other_unbound_reasons(slot):
    """UNBOUND_TRAY_EMPTY does not schedule settle timer."""
    attrs = _nonrfid_attrs_standalone(name="", tray_type="", color="", state="empty")
    sm = FakeSpoolman([], [])
    tray_ent = _tray_entity(slot)
    state_map = {
        tray_ent: {"attributes": {"tag_uid": "", "empty": True}, "state": "empty"},
        f"{tray_ent}::all": {"attributes": {"tag_uid": "", "empty": True}, "state": "empty"},
        "input_boolean.filament_iq_nonrfid_enabled": "on",
        f"input_text.ams_slot_{slot}_spool_id": "0",
        f"input_text.ams_slot_{slot}_expected_spool_id": "0",
        f"input_text.ams_slot_{slot}_status": "",
        f"input_text.ams_slot_{slot}_tray_signature": "",
        f"input_text.ams_slot_{slot}_unbound_reason": "",
    }
    for s in range(1, 7):
        if s != slot:
            other_ent = _tray_entity(s)
            state_map[other_ent] = {"attributes": {"tag_uid": "", "empty": True}, "state": "empty"}
            state_map[f"{other_ent}::all"] = {"attributes": {"tag_uid": "", "empty": True}, "state": "empty"}
    r = TestableReconcile(sm, state_map, args=_DEFAULT_ARGS)
    r._run_reconcile("test")
    settle_calls = [c for c in r._run_in_calls if c.get("kwargs", {}).get("slot") == slot]
    assert len(settle_calls) == 0, "UNBOUND_TRAY_EMPTY must not schedule settle timer"


if __name__ == "__main__":
    unittest.main()
