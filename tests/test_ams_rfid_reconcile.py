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
    DEPRECATED_LOCATION_TO_CANONICAL,
    TRAY_ENTITY_BY_SLOT,
    FULL_SPOOL_G,
    NEXT_MAN_MIN_MARGIN_G,
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
        self.patches.append({"path": path, "payload": payload})
        if path.startswith("/api/v1/spool/"):
            try:
                sid = int(path.split("/")[-1])
                if sid in self.spools:
                    s = self.spools[sid]
                    if "extra" in payload:
                        s["extra"] = {**s.get("extra", {}), **payload["extra"]}
                    if "location" in payload:
                        s["location"] = payload["location"]
            except ValueError:
                pass

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
        if service == "input_text/set_value":
            self._helper_writes.append(kwargs)
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
        """S1: Unknown UID + exactly 1 metadata match -> binds and writes rfid_uid."""
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

        self.assertGreater(len(sm.patches), 0, "expected at least one PATCH (bind rfid_uid)")
        bind_patch = next((p for p in sm.patches if "extra" in p["payload"] and "rfid_tag_uid" in p["payload"]["extra"]), None)
        self.assertIsNotNone(bind_patch, "expected PATCH with extra.rfid_tag_uid")
        val = bind_patch["payload"]["extra"]["rfid_tag_uid"]
        self.assertEqual(json.loads(val) if (val.startswith('"') and val.endswith('"')) else val, tag)
        self.assertEqual(bind_patch["path"], "/api/v1/spool/101")

        summary = getattr(r, "_last_summary", None)
        self.assertIsNotNone(summary)
        auto_regs = [e for e in summary.get("auto_registers", []) if e.get("kind") == "AUTO_REGISTER_RFID_METADATA_MATCH"]
        self.assertEqual(len(auto_regs), 1)
        self.assertEqual(auto_regs[0]["spool_id"], 101)

        status_writes = [w for w in r._helper_writes if "status" in w.get("entity_id", "")]
        ok_write = next((w for w in status_writes if w.get("value") == "OK"), None)
        self.assertIsNotNone(ok_write)

    def test_s2_unknown_uid_two_matches_next_man_up_binds(self):
        """S2: Unknown UID + 2 metadata matches, next-man-up decisive -> binds to lowest."""
        tag = "BBCCDDEE00112233"
        # 100g vs 500g -> margin 400 >= 200
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

        bind_patch = next((p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})), None)
        self.assertIsNotNone(bind_patch)
        self.assertEqual(bind_patch["path"], "/api/v1/spool/201", "should bind to lowest (201)")

        summary = getattr(r, "_last_summary", None)
        self.assertIsNotNone(summary)
        tiebreak = [e for e in summary.get("auto_registers", []) if "TIEBREAK" in e.get("kind", "")]
        self.assertGreater(len(tiebreak), 0)

        status_writes = [w for w in r._helper_writes if "status" in w.get("entity_id", "")]
        ok_write = next((w for w in status_writes if w.get("value") == "OK"), None)
        self.assertIsNotNone(ok_write)

    def test_s3_unknown_uid_two_matches_weights_close_conflict_no_writes(self):
        """S3: Unknown UID + 2 metadata matches, weights too close -> CONFLICT, no writes."""
        tag = "CCDDEEFF00112233"
        # 200g vs 250g -> margin 50 < 200
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
        self.assertEqual(len(bind_patches), 0, "no bind when CONFLICT")

        summary = getattr(r, "_last_summary", None)
        self.assertIsNotNone(summary)
        conflicts = summary.get("conflicts_detected", [])
        self.assertGreater(len(conflicts), 0)
        self.assertEqual(conflicts[0]["reason"], "AMBIGUOUS_METADATA_NO_UNREGISTERED")

        conflict_events = [e for e in summary.get("auto_registers", []) if "CONFLICT" in e.get("kind", "")]
        self.assertGreater(len(conflict_events), 0)

        status_writes = [w for w in r._helper_writes if "status" in w.get("entity_id", "")]
        conflict_status = next((w for w in status_writes if "CONFLICT" in str(w.get("value", ""))), None)
        self.assertIsNotNone(conflict_status)

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
        """S4: Unknown UID + 2 reds (842g vs 1000g, both initial 1000) -> bind to 842 (prefer_used)."""
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

        bind_patch = next((p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})), None)
        self.assertIsNotNone(bind_patch, "should bind when prefer_used applies")
        self.assertEqual(bind_patch["path"], "/api/v1/spool/501", "should bind to 842g spool (501)")

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
        result = r._find_deterministic_candidates(spools, tray_meta, slot=1)
        self.assertEqual(result, [702], "only Shelf spool 702 should be candidate; New 701 excluded")
        # When only New spool exists, result should be empty
        result_new_only = r._find_deterministic_candidates(spools[:1], tray_meta, slot=1)
        self.assertEqual(result_new_only, [], "location New only -> no candidates")

    def test_location_new_excluded_from_deterministic_candidates(self):
        """Spools with location 'New' are excluded from deterministic selection; only Shelf/empty/unknown eligible."""
        tag = "NEWLOCEXCL001122"
        # Spool 701 = New (excluded), 702 = Shelf (only candidate) -> bind to 702
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

        # Prefer: bind patch to 702
        bind_patch = next((p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})), None)
        if bind_patch is not None:
            self.assertEqual(bind_patch["path"], "/api/v1/spool/702", "must bind to 702 (Shelf), not 701 (New)")
        else:
            # Fallback: assert from summary that slot 1 selected 702 (e.g. auto_register or transcript)
            summary = getattr(r, "_last_summary", None)
            self.assertIsNotNone(summary)
            transcripts = summary.get("validation_transcripts", [])
            slot1 = next((t for t in transcripts if t.get("slot") == 1), None)
            self.assertIsNotNone(slot1, "slot 1 transcript missing")
            self.assertEqual(slot1.get("final_spool_id"), 702, "slot 1 must select spool 702 (Shelf), not 701 (New)")
            self.assertEqual(slot1.get("final_slot_status"), "OK", "slot 1 must be OK when 702 is sole candidate")

    def test_location_new_only_unbound(self):
        """When only candidates have location 'New', no bind -> UNBOUND."""
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
        self.assertEqual(len(bind_patches), 0, "must not bind when only candidates are location New")
        status_writes = [w for w in r._helper_writes if "status" in w.get("entity_id", "")]
        unbound = next((w for w in status_writes if "UNBOUND" in str(w.get("value", ""))), None)
        self.assertIsNotNone(unbound)

    def test_s5_two_reds_strict_mode_refuses(self):
        """S5: Same two reds with strict_mode_reregister=True -> CONFLICT STRICT_MODE_MULTIPLE_CANDIDATES, no bind."""
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
        self.assertEqual(len(bind_patches), 0, "strict mode must not auto-pick")

        summary = getattr(r, "_last_summary", None)
        self.assertIsNotNone(summary)
        conflicts = summary.get("conflicts_detected", [])
        self.assertGreater(len(conflicts), 0)
        self.assertEqual(conflicts[0]["reason"], "STRICT_MODE_MULTIPLE_CANDIDATES")

    def test_flow_b_ha_sig_one_candidate_binds(self):
        """Flow B: Unknown UID + exactly 1 HA_SIG match in comment -> auto-binds."""
        tag = "DDEEFF0011223344"
        ha_sig = "HA_SIG=bambu|filament_id=bambu|type=pla|color_hex=ff0000"
        # Spool fails Flow A (vendor != Bambu) but passes Flow B (comment=HA_SIG, ha_spool_uuid set, no rfid)
        spools = [
            _spool(
                401,
                filament_id=1,
                remaining_weight=500,
                rfid_tag_uid=None,
                location="Shelf",
                color_hex="ff0000",
                material="PLA",
                comment=ha_sig,
                ha_spool_uuid=json.dumps("flow-b-test-uuid"),
                vendor_name="Other",
            ),
        ]
        filaments = [{"id": 1, "name": "Generic PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Other"}, "external_id": "generic"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        attrs = {"tag_uid": tag, "type": "PLA", "color": "ff0000", "name": "Bambu PLA Basic",
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

        bind_patch = next((p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})), None)
        self.assertIsNotNone(bind_patch, "expected PATCH with extra.rfid_tag_uid (Flow B bind)")
        self.assertEqual(bind_patch["path"], "/api/v1/spool/401")
        val = bind_patch["payload"]["extra"]["rfid_tag_uid"]
        self.assertEqual(json.loads(val) if (val.startswith('"') and val.endswith('"')) else val, tag)

        summary = getattr(r, "_last_summary", None)
        self.assertIsNotNone(summary)
        flow_b_regs = [e for e in summary.get("auto_registers", []) if e.get("kind") == "FLOW_B_HA_SIG_BOUND"]
        self.assertEqual(len(flow_b_regs), 1)
        self.assertEqual(flow_b_regs[0]["spool_id"], 401)

        status_writes = [w for w in r._helper_writes if "status" in w.get("entity_id", "")]
        ok_write = next((w for w in status_writes if w.get("value") == "OK"), None)
        self.assertIsNotNone(ok_write)

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
        """Plain-string extra write fails with 400 'not valid JSON'; retry with JSON literal succeeds."""
        tag = "AABBCCDD00112233"
        spools = [_spool(601, remaining_weight=500, rfid_tag_uid=None, location="Shelf")]
        filaments = [{"id": 1, "name": "Bambu PLA", "material": "PLA", "color_hex": "ff0000",
                     "vendor": {"name": "Bambu Lab"}, "external_id": "bambu"}]
        sm = RetryFakeSpoolman(spools, filaments)
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

        self.assertEqual(sm._extra_patch_count, 2, "retry path: first fails, second succeeds")
        bind_patches = [p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})]
        self.assertGreaterEqual(len(bind_patches), 1)
        last = bind_patches[-1]
        val = last["payload"]["extra"]["rfid_tag_uid"]
        self.assertEqual(json.loads(val) if (val.startswith('"') and val.endswith('"')) else val, tag)

    def test_flow_b_ha_sig_color_hex_with_leading_hash(self):
        """Flow B: color_hex with leading # in tray is normalized for HA_SIG."""
        tag = "FF00112233445566"
        ha_sig = "HA_SIG=bambu|filament_id=gfa00|type=pla|color_hex=c12e1f"
        spools = [
            _spool(701, filament_id=1, rfid_tag_uid=None, location="Shelf", color_hex="c12e1f", material="PLA",
                   comment=ha_sig, ha_spool_uuid=json.dumps("uuid-701"), vendor_name="Other"),
        ]
        filaments = [{"id": 1, "name": "Generic", "material": "PLA", "color_hex": "c12e1f",
                     "vendor": {"name": "Other"}, "external_id": "gfa00"}]
        sm = FakeSpoolman(spools, filaments)
        tray_ent = _tray_entity(1)
        attrs = {"tag_uid": tag, "type": "PLA", "color": "#c12e1f", "name": "Bambu", "filament_id": "gfa00",
                 "tray_weight": 1000, "remain": 50}
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

        bind_patch = next((p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})), None)
        self.assertIsNotNone(bind_patch, "Flow B should bind when color has leading #")
        self.assertEqual(bind_patch["path"], "/api/v1/spool/701")

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
        """Given tray entity for slot 4 (physical), when it resolves OK, we PATCH extra.rfid_tag_uid and location=AMS1_Slot4."""
        tag = "C7D26F7B00000100"
        spools = [_spool(601, remaining_weight=500, rfid_tag_uid=None, location="Shelf", color_hex="ff0000")]
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
        self.assertGreater(len(location_patches), 0, "expected a PATCH with location=AMS1_Slot4")
        self.assertEqual(location_patches[0]["path"], "/api/v1/spool/601")
        rfid_patches = [p for p in sm.patches if "extra" in p.get("payload", {}) and "rfid_tag_uid" in p["payload"].get("extra", {})]
        self.assertGreater(len(rfid_patches), 0, "expected a PATCH with extra.rfid_tag_uid")
        status_writes = [w for w in r._helper_writes if "ams_slot_4_status" in w.get("entity_id", "")]
        ok_write = next((w for w in status_writes if w.get("value") == "OK"), None)
        self.assertIsNotNone(ok_write, "slot 4 status should be OK")

    def test_ha_sig_stamped_on_ok_when_comment_missing(self):
        """On successful bind (status OK), spool comment is stamped with HA_SIG when missing or blank."""
        tag = "C7D8E9F000112233"
        spools = [_spool(101, remaining_weight=500, rfid_tag_uid=None, location="Shelf", comment="")]
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
        self.assertEqual(comment_patches[0]["path"], "/api/v1/spool/101")
        status_writes = [w for w in r._helper_writes if "status" in w.get("entity_id", "")]
        ok_write = next((w for w in status_writes if w.get("value") == "OK"), None)
        self.assertIsNotNone(ok_write, "status should be OK for this bind")

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
        self.assertEqual(len(slot3_clears), 5, "expected five helper clears for slot 3")
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
        spools = [_spool(101, remaining_weight=500, rfid_tag_uid=tag, location="AMS1_Slot1", color_hex="ff0000")]
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


if __name__ == "__main__":
    unittest.main()
