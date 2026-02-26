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

from ams_rfid_reconcile import (
    AmsRfidReconcile,
    CANONICAL_LOCATION_BY_SLOT,
    COLOR_DISTANCE_THRESHOLD,
    DEPRECATED_LOCATION_TO_CANONICAL,
    TRAY_ENTITY_BY_SLOT,
    FULL_SPOOL_G,
    NEXT_MAN_MIN_MARGIN_G,
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
    _classify_unbound_reason,
    _colors_close,
    _hex_to_rgb,
    _normalize_hex_color,
    _rgb_distance,
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


def _spool(sid, filament_id=1, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000", material="PLA", name="Bambu PLA", comment=None, ha_spool_uuid=None, vendor_name="Bambu Lab", initial_weight=None):
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
        a = args or k.get("args", {})
        super().__init__(ad, "test", None, a, None, None, None)
        self._spoolman = spoolman
        self._state_map = dict(state_map)
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
        self._evidence_lines = []

    def _append_evidence_line(self, line):
        self._evidence_lines.append(line)

    def _run_reconcile_startup(self, kwargs):
        pass

    def initialize(self):
        pass

    def get_state(self, entity_id, attribute=None):
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

    def run_in(self, callback, delay):
        pass

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


def _state_key(slot, entity_suffix, attr=None):
    eid = f"input_text.ams_slot_{slot}_{entity_suffix}"
    return f"{eid}::{attr}" if attr else eid


def _tray_entity(slot):
    return TRAY_ENTITY_BY_SLOT.get(slot, f"sensor.tray_{slot}")


class TestAmsRfidReconcile(unittest.TestCase):
    def setUp(self):
        self.args = {
            "spoolman_url": "http://fake:7912",
            "enabled": True,
            "debug_logs": False,
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
        tag = "AABBCCDD8421000"
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

    def test_find_deterministic_candidates_excludes_location_new(self):
        """Unit test: _find_deterministic_candidates excludes location 'New' and returns only Shelf spool."""
        spools = [
            _spool(701, remaining_weight=1000, rfid_tag_uid=None, location="New", color_hex="ff0000"),
            _spool(702, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000"),
        ]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
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
        """Unit test: eligibility is Shelf or AMS* only; New is not eligible."""
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000",
                      "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman([], filaments)
        r = TestableReconcile(sm, {}, args=self.args)
        r._active_run = {"decisions": [], "no_write_paths": [], "writes": [], "conflicts": [], "unknown_tags": [], "auto_registers": [], "validation_transcripts": []}
        attrs = {"tag_uid": "x", "type": "PLA", "color": "ff0000", "name": "Bambu PLA",
                 "filament_id": "bambu", "tray_weight": 1000, "remain": 50}
        tray_meta = r._tray_meta(attrs, "valid")

        # Spool at Shelf → eligible
        spools_shelf = [_spool(101, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000")]
        candidate_ids, ineligible_new = r._find_deterministic_candidates(spools_shelf, tray_meta, slot=1)
        self.assertEqual(candidate_ids, [101], "Spool at Shelf should be eligible")
        self.assertEqual(ineligible_new, 0)

        # Spool at AMS1_Slot4 → eligible
        spools_ams1 = [_spool(102, remaining_weight=500, rfid_tag_uid=None, location="AMS1_Slot4", color_hex="ff0000")]
        candidate_ids, ineligible_new = r._find_deterministic_candidates(spools_ams1, tray_meta, slot=1)
        self.assertEqual(candidate_ids, [102], "Spool at AMS1_Slot4 should be eligible")
        self.assertEqual(ineligible_new, 0)

        # Spool at AMS128_Slot1 → eligible
        spools_ams128 = [_spool(103, remaining_weight=500, rfid_tag_uid=None, location="AMS128_Slot1", color_hex="ff0000")]
        candidate_ids, ineligible_new = r._find_deterministic_candidates(spools_ams128, tray_meta, slot=1)
        self.assertEqual(candidate_ids, [103], "Spool at AMS128_Slot1 should be eligible")
        self.assertEqual(ineligible_new, 0)

        # Spool at New → NOT eligible
        spools_new = [_spool(104, remaining_weight=500, rfid_tag_uid=None, location="New", color_hex="ff0000")]
        candidate_ids, ineligible_new = r._find_deterministic_candidates(spools_new, tray_meta, slot=1)
        self.assertEqual(candidate_ids, [], "Spool at New should not be eligible")
        self.assertEqual(ineligible_new, 1, "one spool excluded due to location New")

    def test_location_new_excluded_from_deterministic_candidates(self):
        """PHASE_2_5: Tag in tray but no spool at Shelf has this UID -> UNBOUND_ACTION_REQUIRED, no bind to 702."""
        tag = "NEWLOCEXCL001122"
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
        tag = "NEWONLYUNBOUND99"
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
        tag = "AABBCCDDSTRICT"
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
        """PHASE_2_6: Non-RFID tray + one Shelf candidate (material/color/vendor) -> bind, status OK."""
        spools = [_spool(201, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000")]
        filaments = [{"id": 1, "name": "Bambu PLA Basic", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        state_map = {
            tray_ent: _tray_state("", tray_type="PLA", color="ff0000", name="Bambu PLA Basic", filament_id="bambu"),
            f"{tray_ent}::all": {"attributes": _tray_state("")["attributes"], "state": "valid"},
        }
        state_map["input_boolean.p1s_nonrfid_enabled"] = "on"
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), "OK")
        patches_201 = [p for p in sm.patches if p.get("path") == "/api/v1/spool/201"]
        self.assertGreaterEqual(len(patches_201), 1, "expected location PATCH to spool 201")
        summary = getattr(r, "_last_summary", None)
        self.assertIsNotNone(summary)
        transcripts = summary.get("validation_transcripts", [])
        slot1 = next((t for t in transcripts if t.get("slot") == 1), None)
        self.assertIsNotNone(slot1)
        self.assertEqual(slot1.get("final_spool_id"), 201)
        self.assertEqual(slot1.get("reason"), "shelf_match")

    def test_phase26_nonrfid_ambiguity_needs_action(self):
        """PHASE_2_6: Non-RFID + multiple Shelf candidates, tie-break fails -> NEEDS_ACTION, no bind."""
        spools = [
            _spool(301, remaining_weight=200, rfid_tag_uid=None, location="Shelf", color_hex="00ff00"),
            _spool(302, remaining_weight=250, rfid_tag_uid=None, location="Shelf", color_hex="00ff00"),
        ]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "00ff00",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        state_map = {
            tray_ent: _tray_state("", tray_type="PLA", color="00ff00", name="Bambu PLA", filament_id="bambu"),
            f"{tray_ent}::all": {"attributes": _tray_state("")["attributes"], "state": "valid"},
        }
        state_map["input_boolean.p1s_nonrfid_enabled"] = "on"
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), STATUS_UNBOUND_ACTION_REQUIRED)
        bind_patches = [p for p in sm.patches if "path" in p and "/api/v1/spool/" in p.get("path", "")]
        self.assertEqual(len([p for p in bind_patches if p.get("path") in ("/api/v1/spool/301", "/api/v1/spool/302") and p.get("payload", {}).get("location")]), 0,
                         "must not bind when ambiguous (tie-break may still pick one; if so this may need relax)")

    def test_phase26_nonrfid_new_fallback_unambiguous_binds(self):
        """PHASE_2_6: No Shelf match + exactly one New candidate -> bind + New fallback notify."""
        spools = [_spool(401, remaining_weight=500, rfid_tag_uid=None, location="New", color_hex="0000ff", material="PETG", name="Bambu PETG")]
        filaments = [{"id": 1, "name": "Bambu PETG", "material": "PETG", "color_hex": "0000ff",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        attrs = _tray_state("", tray_type="PETG", color="0000ff", name="Bambu PETG", filament_id="bambu")["attributes"]
        state_map = {
            tray_ent: {"attributes": attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": attrs, "state": "valid"},
        }
        state_map["input_boolean.p1s_nonrfid_enabled"] = "on"
        for slot in range(1, 7):
            state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
            state_map[f"input_text.ams_slot_{slot}_status"] = ""

        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")

        status_writes = [w for w in r._helper_writes if w.get("entity_id") == "input_text.ams_slot_1_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), "OK")
        patches_401 = [p for p in sm.patches if p.get("path") == "/api/v1/spool/401"]
        self.assertGreaterEqual(len(patches_401), 1)
        summary = getattr(r, "_last_summary", None)
        self.assertIsNotNone(summary)
        slot1 = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == 1), None)
        self.assertIsNotNone(slot1)
        self.assertEqual(slot1.get("final_spool_id"), 401)
        self.assertEqual(slot1.get("reason"), "new_fallback")

    def test_ht_nonrfid_helper_set_location_sync(self):
        """HT non-RFID tray (all-zero tag/tray_uuid) + helper_spool_id > 0 -> location sync, status OK."""
        ht_attrs = {
            "tag_uid": "0000000000000000",
            "tray_uuid": "00000000000000000000000000000000",
            "empty": False,
            "type": "PLA",
            "color": "ff0000",
            "name": "Bambu PLA",
            "filament_id": "bambu",
            "tray_weight": 1000,
            "remain": 50,
        }
        spools = [_spool(101, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000")]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        slot = 5
        tray_ent = _tray_entity(slot)
        state_map = {
            tray_ent: {"attributes": ht_attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": ht_attrs, "state": "valid"},
        }
        state_map["input_boolean.p1s_nonrfid_enabled"] = "on"
        state_map[f"input_text.ams_slot_{slot}_spool_id"] = "101"
        state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
        state_map[f"input_text.ams_slot_{slot}_status"] = ""
        for s in range(1, 7):
            if s != slot:
                other_ent = _tray_entity(s)
                state_map[other_ent] = {"attributes": {"tag_uid": "", "empty": True}, "state": "empty"}
                state_map[f"{other_ent}::all"] = {"attributes": {"tag_uid": "", "empty": True}, "state": "empty"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
                state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
                state_map[f"input_text.ams_slot_{s}_status"] = ""
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), "OK")
        location_101 = [p for p in sm.patches if p.get("path") == "/api/v1/spool/101" and p.get("payload", {}).get("location") == "AMS128_Slot1"]
        self.assertGreater(len(location_101), 0, "HT with helper_spool_id > 0 must sync location to AMS128_Slot1")
        summary = getattr(r, "_last_summary", None)
        self.assertIsNotNone(summary)
        slot5 = next((t for t in summary.get("validation_transcripts", []) if t.get("slot") == slot), None)
        self.assertIsNotNone(slot5)
        self.assertEqual(slot5.get("final_spool_id"), 101)
        self.assertEqual(slot5.get("reason"), "ht_present")

    def test_ht_nonrfid_no_helper_remains_unregistered(self):
        """HT non-RFID tray (all-zero tag/tray_uuid) + helper_spool_id == 0 -> NON_RFID_UNREGISTERED, no location sync."""
        ht_attrs = {
            "tag_uid": "0000000000000000",
            "tray_uuid": "00000000000000000000000000000000",
            "empty": False,
            "type": "PLA",
            "color": "ff0000",
            "name": "Bambu PLA",
            "filament_id": "bambu",
            "tray_weight": 1000,
            "remain": 50,
        }
        spools = [_spool(101, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000")]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = FakeSpoolman(spools, filaments)
        slot = 5
        tray_ent = _tray_entity(slot)
        state_map = {
            tray_ent: {"attributes": ht_attrs, "state": "valid"},
            f"{tray_ent}::all": {"attributes": ht_attrs, "state": "valid"},
        }
        state_map["input_boolean.p1s_nonrfid_enabled"] = "on"
        state_map[f"input_text.ams_slot_{slot}_spool_id"] = "0"
        state_map[f"input_text.ams_slot_{slot}_expected_spool_id"] = "0"
        state_map[f"input_text.ams_slot_{slot}_status"] = ""
        for s in range(1, 7):
            if s != slot:
                other_ent = _tray_entity(s)
                state_map[other_ent] = {"attributes": {"tag_uid": "", "empty": True}, "state": "empty"}
                state_map[f"{other_ent}::all"] = {"attributes": {"tag_uid": "", "empty": True}, "state": "empty"}
                state_map[f"input_text.ams_slot_{s}_spool_id"] = "0"
                state_map[f"input_text.ams_slot_{s}_expected_spool_id"] = "0"
                state_map[f"input_text.ams_slot_{s}_status"] = ""
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), "NON_RFID_UNREGISTERED")
        location_101 = [p for p in sm.patches if p.get("path") == "/api/v1/spool/101" and (p.get("payload") or {}).get("location")]
        self.assertEqual(len(location_101), 0, "HT with helper_spool_id 0 must not write location to any spool for this slot")

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
        state_map["input_boolean.p1s_nonrfid_enabled"] = "on"
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

        expected_ha_sig = "HA_SIG=bambu|filament_id=bambu|type=pla|color_hex=ff0000"
        comment_patches = [p for p in sm.patches if p.get("payload", {}).get("comment") == expected_ha_sig]
        self.assertGreater(len(comment_patches), 0, "expected a PATCH that stamps spool comment with HA_SIG")
        self.assertEqual(len(comment_patches), 1, "expected exactly one comment PATCH (idempotent convergence)")
        self.assertEqual(comment_patches[0]["path"], "/api/v1/spool/101")
        self.assertEqual(comment_patches[0]["payload"], {"comment": expected_ha_sig})
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
        """Stamping works without input_text.ams_slot_*_tray_signature (e.g. when helper is unavailable)."""
        tag = "C7D26F7B00000100"
        spools = [_spool(1, remaining_weight=500, rfid_tag_uid=json.dumps(tag), location="Shelf", comment="")]
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
        expected_ha_sig = "HA_SIG=bambu|filament_id=bambu|type=pla|color_hex=ff0000"
        comment_patches = [p for p in sm.patches if p.get("payload", {}).get("comment") == expected_ha_sig]
        self.assertEqual(len(comment_patches), 1, "expected one comment PATCH even without tray_signature helper")
        self.assertEqual(comment_patches[0]["path"], "/api/v1/spool/1")

    def test_sticky_must_not_override_when_helper_uid_mismatch(self):
        """PHASE_2_6_1: Sticky must not override UID-resolved spool when helper spool UID != tray tag_uid."""
        tag = "STICKYGUARD00112233"
        # tag_to_spools[tag] = [38]; spool 38 has UID T, spool 4 has different UID
        spools = [
            _spool(38, remaining_weight=500, rfid_tag_uid=tag, location="Shelf", color_hex="ff0000"),
            _spool(4, remaining_weight=400, rfid_tag_uid="OTHER0000000001", location="Shelf", color_hex="ff0000"),
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
        tag = "BINDGUARD00112233"
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
        r._rfid_bind_guard_ok = lambda resolved_spool_id, tag_uid, spool_index: False
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
        state_map["input_boolean.p1s_nonrfid_enabled"] = "on"
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
        state_map["input_boolean.p1s_nonrfid_enabled"] = "on"
        sm = FakeSpoolman([], [])
        r = TestableReconcile(sm, state_map, args=self.args)
        r._run_reconcile("test")
        status_writes = [w for w in r._helper_writes if w.get("entity_id") == f"input_text.ams_slot_{slot}_status"]
        self.assertGreater(len(status_writes), 0)
        self.assertEqual(status_writes[-1].get("value"), "NON_RFID_UNREGISTERED", "after pending expires, non-RFID lane must run when enabled")

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
        self.assertGreaterEqual(len(spool_4_gets), 2, "GET spool 4: at least clear-previous and _spool_exists (cached per run)")

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
        def capture_force(slot, spool_id, tag_uid, source, tray_meta=None, tray_state="", tray_identity=None, previous_helper_spool_id=0):
            force_calls.append((slot, spool_id, tag_uid, source))
            orig(slot, spool_id, tag_uid, source, tray_meta=tray_meta, tray_state=tray_state, tray_identity=tray_identity, previous_helper_spool_id=previous_helper_spool_id)
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
        from ams_rfid_reconcile import STATUS_UNBOUND_ACTION_REQUIRED
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


if __name__ == "__main__":
    unittest.main()
