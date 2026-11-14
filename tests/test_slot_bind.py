"""IN-1052 S7a: guarded bind / unbind of a spool to an AMS slot, on request (print-ops' slot card).

Bind matches HA's script.ams_slot_assign_and_update (helpers, location, RFID enroll on a tagged tray (IN-1174
decision B), FILAMENT_IQ_SLOT_ASSIGNED, unbound_reason cleared); it refuses during a print, for 5 min after one,
when paused, on a stale page (compare-before-write), on a bound slot (no one-step replace) and on RFID conflicts.
Every request is answered on sensor.filament_iq_slot_bind_result."""
import datetime

import pytest

import test_ams_rfid_reconcile as h
from filament_iq.ams_rfid_reconcile import (SLOT_BIND_EVENT, SLOT_BIND_RESULT_ENTITY, SLOT_BIND_SETTLE_S,
                                            SLOT_UNBIND_EVENT)

PRINT_ACTIVE = "input_boolean.filament_iq_print_active"
TRAY_UUID = "A1B2C3D4E5F60718293A4B5C6D7E8F90"


def _iso(seconds_ago):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds_ago)
    return t.isoformat()


class Bind(h.TestableReconcile):
    def __init__(self, spoolman, state_map):
        super().__init__(spoolman, state_map, args=h._DEFAULT_ARGS)
        self._print_active_entity = PRINT_ACTIVE
        self.events, self.results = [], []

    def fire_event(self, event, **kw):
        self.events.append((event, kw))

    def set_state(self, entity_id, state=None, attributes=None):
        self.results.append((entity_id, state, dict(attributes or {})))

    def _run_reconcile(self, *a, **k):     # the bind itself is under test, not a full reconcile pass
        pass

    def answer(self):
        entity, state, attrs = self.results[-1]
        assert entity == SLOT_BIND_RESULT_ENTITY
        return state, attrs

    def helper(self, slot, suffix):
        w = [x for x in self._helper_writes if x.get("entity_id") == f"input_text.ams_slot_{slot}_{suffix}"]
        return w[-1]["value"] if w else None


def world(slot=2, bound=0, rfid=False, ended_s_ago=3600, active="off", paused=False, spools=None, other=None):
    attrs = h._nonrfid_attrs_standalone(name="Overture PLA")
    if rfid:
        attrs.update(tag_uid="1122334455667788", tray_uuid=TRAY_UUID)
    sm = h._nonrfid_state_map_standalone(slot, attrs, helper_spool_id=bound, expected_spool_id=bound)
    if ended_s_ago is not None:
        sm[PRINT_ACTIVE] = {"state": active, "attributes": {}, "last_changed": _iso(ended_s_ago)}
    else:
        sm[PRINT_ACTIVE] = {"state": active, "attributes": {}}
    sm["input_boolean.filament_iq_enabled"] = "off" if paused else "on"
    for s, sid in (other or {}).items():
        sm[f"input_text.ams_slot_{s}_spool_id"] = str(sid)
    spoolman = h.FakeSpoolman(spools if spools is not None else [h._spool(42, location="Shelf"),
                                                                 h._spool(7, location="AMS1_Slot2")],
                              [h._bambu_filament()])
    return Bind(spoolman, sm)


def bind(r, slot=2, spool_id=42, expected=0, request_id="req-1", **extra):
    r._on_bind_request(SLOT_BIND_EVENT, {"request_id": request_id, "slot": slot, "spool_id": spool_id,
                                         "expected_spool_id": expected, **extra}, {})
    return r.answer()


def unbind(r, slot=2, expected=7, request_id="req-2"):
    r._on_unbind_request(SLOT_UNBIND_EVENT, {"request_id": request_id, "slot": slot, "expected_spool_id": expected}, {})
    return r.answer()


def nothing_written(r):
    return r._helper_writes == [] and r._spoolman.patches == [] and r.events == []


def test_bind_non_rfid_matches_the_ha_script():
    r = world()
    state, a = bind(r)
    assert state == "ok", a
    assert a["request_id"] == "req-1" and a["slot"] == 2 and a["spool_id"] == 42
    assert r.helper(2, "spool_id") == "42" and r.helper(2, "expected_spool_id") == "42"
    assert {"path": "/api/v1/spool/42", "payload": {"location": h.CANONICAL_LOCATION_BY_SLOT[2]},
            "spool_id": 42} in r._spoolman.patches
    assert r.events == [("FILAMENT_IQ_SLOT_ASSIGNED", {"slot": 2, "spool_id": 42, "sync_color_hex": "auto"})]
    assert r.helper(2, "unbound_reason") == "" or r.helper(2, "unbound_reason") is None   # already "" in the map
    assert a["done"] == ["helpers", "location", "slot_assigned_event", "unbound_reason_cleared"]


def test_bind_rfid_enrolls_the_tray_uuid_first():
    r = world(rfid=True)
    state, a = bind(r)
    assert state == "ok", a
    assert a["done"][0] == "rfid_enroll"
    lot = [p for p in r._spoolman.patches if p["spool_id"] == 42 and "lot_nr" in p["payload"]]
    assert lot and lot[-1]["payload"]["lot_nr"] == TRAY_UUID


def test_rfid_tag_already_on_another_spool_refuses_and_writes_nothing():
    r = world(rfid=True, spools=[h._spool(42), h._spool(9, lot_nr=TRAY_UUID)])
    state, a = bind(r)
    assert state == "refused" and "RFID enroll" in a["why"] and "9" in a["why"]
    assert nothing_written(r)


def test_a_spool_enrolled_to_another_tag_refuses():
    r = world(rfid=True, spools=[h._spool(42, lot_nr="F" * 32)])
    state, a = bind(r)
    assert state == "refused" and "another RFID tag" in a["why"] and nothing_written(r)


@pytest.mark.parametrize("kw,req,why", [
    ({"bound": 7}, {"expected": 7}, "unbind it first"),                          # decision 3: no one-step replace
    ({"bound": 7}, {"expected": 0}, "reload"),                                   # stale page: compare-before-write
    ({"active": "on"}, {}, "not off"),                                           # a print is running
    ({"ended_s_ago": 60}, {}, "a print ended 60 s ago"),                         # the post-finish reconcile window
    ({"ended_s_ago": SLOT_BIND_SETTLE_S - 2}, {}, "after a finish"),
    ({"ended_s_ago": None}, {}, "unreadable"),                                   # fail closed
    ({"paused": True}, {}, "paused"),
    ({}, {"spool_id": 99}, "no spool 99"),
    ({"spools": [h._spool(42, location="Shelf")]}, {"slot": 9}, "not a physical AMS slot"),
    ({"other": {3: 42}}, {}, "already bound to slot 3"),
    ({}, {"request_id": "bad id!"}, "request_id"),
    ({}, {"spool_id": 0}, "positive"),
])
def test_bind_refusals_write_nothing(kw, req, why):
    r = world(**kw)
    state, a = bind(r, **req)
    assert state == "refused" and why in a["why"], a
    assert nothing_written(r)


def test_archived_spool_refuses():
    s = h._spool(42)
    s["archived"] = True
    r = world(spools=[s])
    state, a = bind(r)
    assert state == "refused" and "archived" in a["why"] and nothing_written(r)


def test_settled_exactly_at_the_limit_is_allowed():
    r = world(ended_s_ago=SLOT_BIND_SETTLE_S + 1)
    assert bind(r)[0] == "ok"


def test_a_running_reconcile_refuses():
    r = world()
    r._active_run = {"reason": "test"}
    state, a = bind(r)
    assert state == "refused" and "reconcile is running" in a["why"] and nothing_written(r)


def test_a_failure_part_way_says_what_was_done():
    r = world()
    def boom(path, payload):
        raise RuntimeError("HTTP 500")
    r._spoolman_patch = boom
    state, a = bind(r)
    assert state == "error" and a["done"] == ["helpers"] and "HTTP 500" in a["why"]


def test_unbind_clears_the_helpers_and_shelves_the_spool():
    r = world(bound=7)
    state, a = unbind(r)
    assert state == "ok", a
    assert r.helper(2, "spool_id") == "0" and r.helper(2, "expected_spool_id") == "0"
    assert r._spoolman.patches[-1]["payload"] == {"location": "Shelf"} and r._spoolman.patches[-1]["spool_id"] == 7
    assert not [p for p in r._spoolman.patches if "lot_nr" in p["payload"]]          # enrollment left alone


@pytest.mark.parametrize("kw,expected,why", [
    ({}, 0, "nothing to unbind"),
    ({"bound": 7}, 8, "reload"),
    ({"bound": 7, "active": "on"}, 7, "not off"),
    ({"bound": 7, "ended_s_ago": 30}, 7, "after a finish"),
])
def test_unbind_refusals_write_nothing(kw, expected, why):
    r = world(**kw)
    state, a = unbind(r, expected=expected)
    assert state == "refused" and why in a["why"], a
    assert nothing_written(r)


def test_missing_expected_is_refused():
    r = world()
    r._on_bind_request(SLOT_BIND_EVENT, {"request_id": "r", "slot": 2, "spool_id": 42}, {})
    state, a = r.answer()
    assert state == "refused" and "expected_spool_id is required" in a["why"] and nothing_written(r)


def _hide(r, *entity_ids):
    """Make these helpers look missing in HA (as _get_helper_state sees them)."""
    real = r._get_helper_state
    r._get_helper_state = lambda e: None if e in entity_ids else real(e)


@pytest.mark.parametrize("suffix", ["spool_id", "expected_spool_id", "unbound_reason"])
def test_a_missing_helper_refuses_before_anything_is_written(suffix):
    r = world(rfid=True)
    _hide(r, f"input_text.ams_slot_2_{suffix}")
    state, a = bind(r)
    assert state == "refused" and f"ams_slot_2_{suffix} is missing" in a["why"] and nothing_written(r), a


def test_unbind_with_a_missing_helper_refuses():
    r = world(bound=7)
    _hide(r, "input_text.ams_slot_2_expected_spool_id")
    state, a = unbind(r)
    assert state == "refused" and "missing" in a["why"] and nothing_written(r), a


def test_a_skipped_helper_write_is_an_error_not_ok():
    # gate follow-up (filament-iq#134): _set_helper skipping a write must not be reported as done
    r = world()
    real = r._set_helper
    r._set_helper = lambda e, v: False if e.endswith("_unbound_reason") else real(e, v)
    state, a = bind(r)
    assert state == "error" and "unbound_reason was not written" in a["why"], a
    assert a["done"] == ["helpers", "location", "slot_assigned_event"]


def test_a_skipped_spool_id_write_stops_before_location():
    r = world(bound=7)
    real = r._set_helper
    r._set_helper = lambda e, v: False if e.endswith("_expected_spool_id") else real(e, v)
    state, a = unbind(r)
    assert state == "error" and a["done"] == [] and r._spoolman.patches == [], a


def test_set_helper_reports_whether_the_value_is_set():
    r = world()
    assert r._set_helper("input_text.ams_slot_2_spool_id", "42") is True          # written
    r._state_map["input_text.ams_slot_2_status"] = "x"
    assert r._set_helper("input_text.ams_slot_2_status", "x") is True             # already equal
    assert r._set_helper("input_text.no_such_helper", "1") is False               # missing in HA
