"""Filament IQ consumption harness (IN-1494).

Drives the REAL AppDaemon apps (filament_iq.ams_print_usage_sync and filament_iq.runout_tracker) through a
scenario timeline, with three fakes at the edges and nothing else replaced:

  FakeHA        states + attributes, listen_state callbacks in registration order, run_in timers on a
                controlled clock, call_service (input_text / input_boolean writes land back in the state map;
                notifications are recorded as alerts). Template sensors from ha-config/packages/filament_iq.yaml
                are computed on read: p1s_tray_N_fuel_gauge_remaining (slots 1-6 only, as in the package) and
                ams_slot_N_remaining_g, plus the Spoolman integration's sensor.spoolman_spool_N.
  FakeSpoolman  urllib.request.urlopen is patched: GET /spool, GET /spool/N, PUT /spool/N/use, PATCH /spool/N.
                Every write is recorded, tagged with the print it happened in.
  FakeClock     time.time / datetime.utcnow / datetime.now inside the app module; no sleeps.

The 3MF reaches the app through the ha-bambulab cache path (the real _try_cache_3mf + parse_slice_info_file):
the harness writes <n>-<task>.slice_info.config into a temp cache dir with mtime = the fake clock.

Scenario format: see tests/harness/README.md. Fake data only (placeholder serial, 192.0.2.x URLs).
"""

from __future__ import annotations

import datetime as _real_datetime
import io
import json
import os
import re
import sys
import types
import urllib.error
import urllib.request

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_APPS = os.path.join(_REPO, "apps")
if _APPS not in sys.path:
    sys.path.insert(0, _APPS)

# The apps import `hassapi`. Other test modules may already have put a minimal fake in sys.modules; the
# harness subclasses override every Hass API the apps call (get_state, set_state, listen_state, listen_event,
# run_in, call_service, log), so whichever fake base class is installed doesn't matter.
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

from filament_iq import ams_print_usage_sync as _usage_mod  # noqa: E402
from filament_iq import runout_tracker as _runout_mod  # noqa: E402

PREFIX = "p1s_your_printer_serial"
SPOOLMAN_URL = "http://192.0.2.1:7912"
EPOCH = 1_795_000_000.0          # fake clock start (late 2026); any value works
FUEL_GAUGE_SLOTS = (1, 2, 3, 4, 5, 6)   # the HA package defines fuel gauge sensors for these only
ALL_SLOTS = (1, 2, 3, 4, 5, 6, 7, 8)
AMS_UNITS = [                    # as in the live apps.yaml: AMS 2 Pro 1-4, three HT 5-7, external 8
    {"type": "ams_2_pro", "ams_index": 0, "slots": [1, 2, 3, 4]},
    {"type": "ams_ht", "ams_index": 128, "slots": [5]},
    {"type": "ams_ht", "ams_index": 129, "slots": [6]},
    {"type": "ams_ht", "ams_index": 130, "slots": [7]},
    {"type": "external", "slots": [8]},
]
SLOT_TO_AMS_TRAY = {1: (0, 0), 2: (0, 1), 3: (0, 2), 4: (0, 3), 5: (128, 0), 6: (129, 0), 7: (130, 0), 8: (255, 0)}
NO_TAG = "0000000000000000"
NO_UUID = "0" * 32
ALERT_SERVICES = ("persistent_notification/create", "script/notify_jerry")
# The end-of-print summary push (_send_notification) is routine, not an alert.
SUMMARY_TITLES = ("\u2705 Print Complete", "\u274c Print Failed", "\u26a0\ufe0f Print Cancelled")


# ── clock ────────────────────────────────────────────────────────────────────────────────────────────────

class FakeClock:
    def __init__(self, start=EPOCH):
        self.now = float(start)

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def iso(self):
        return _real_datetime.datetime.fromtimestamp(self.now, _real_datetime.timezone.utc).isoformat()


def _datetime_module(clock):
    """A stand-in for the `datetime` module whose datetime.utcnow()/now() read the fake clock."""

    class _DT(_real_datetime.datetime):
        @classmethod
        def utcnow(cls):
            return _real_datetime.datetime.fromtimestamp(clock.now, _real_datetime.timezone.utc).replace(tzinfo=None)

        @classmethod
        def now(cls, tz=None):
            d = _real_datetime.datetime.fromtimestamp(clock.now, _real_datetime.timezone.utc)
            return d.astimezone(tz) if tz else d.replace(tzinfo=None)

    mod = types.ModuleType("datetime")
    mod.datetime = _DT
    mod.timedelta = _real_datetime.timedelta
    mod.timezone = _real_datetime.timezone
    mod.date = _real_datetime.date
    return mod


def _time_module(clock):
    import time as _t
    mod = types.ModuleType("time")
    mod.time = clock.time
    mod.monotonic = clock.monotonic
    mod.sleep = lambda s: None
    mod.strftime = _t.strftime
    mod.localtime = _t.localtime
    mod.gmtime = _t.gmtime
    return mod


# ── Spoolman ─────────────────────────────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, body, status=200):
        self._buf = io.BytesIO(json.dumps(body).encode())
        self.status = status

    def read(self):
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSpoolman:
    """In-memory Spoolman. remaining = initial - used, like Spoolman (no clamp at 0)."""

    def __init__(self, harness):
        self.h = harness
        self.spools = {}
        self.writes = []          # every PUT /use and PATCH: {print, kind, spool_id, body, t}

    def add(self, spool_id, remaining_g, initial_g, filament, lot_nr):
        self.spools[spool_id] = {
            "id": spool_id,
            "initial_weight": float(initial_g),
            "used_weight": round(float(initial_g) - float(remaining_g), 2),
            "remaining_weight": float(remaining_g),
            "filament": dict(filament),
            "lot_nr": lot_nr,
            "location": "AMS",
            "archived": False,
        }

    def _set_remaining(self, spool, remaining):
        spool["remaining_weight"] = round(float(remaining), 2)
        spool["used_weight"] = round(spool["initial_weight"] - spool["remaining_weight"], 2)

    def urlopen(self, req, timeout=None):
        url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        method = req.get_method() if isinstance(req, urllib.request.Request) else "GET"
        path = url[len(SPOOLMAN_URL):] if url.startswith(SPOOLMAN_URL) else url
        body = None
        if isinstance(req, urllib.request.Request) and req.data:
            body = json.loads(req.data.decode())
        path_only = path.split("?")[0]
        if method == "GET" and path_only in ("/api/v1/health", "/api/v1/info"):
            return _Resp({"status": "healthy"})
        if method == "GET" and path_only == "/api/v1/spool":
            return _Resp([dict(s) for s in self.spools.values()])
        m = re.fullmatch(r"/api/v1/spool/(\d+)(/use)?", path_only)
        if not m:
            raise urllib.error.URLError(f"fake spoolman: no route {method} {path}")
        spool = self.spools.get(int(m.group(1)))
        if spool is None:
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        if method == "GET" and not m.group(2):
            return _Resp(dict(spool))
        if method == "PUT" and m.group(2):
            use = float(body.get("use_weight", 0))
            self._set_remaining(spool, spool["remaining_weight"] - use)
            self.writes.append({"print": self.h.current_print, "kind": "use", "spool_id": spool["id"],
                                "grams": use, "t": self.h.clock.now - EPOCH})
            return _Resp(dict(spool))
        if method == "PATCH" and not m.group(2):
            if "remaining_weight" in body:
                self._set_remaining(spool, body["remaining_weight"])
            for k in ("location", "archived"):
                if k in body:
                    spool[k] = body[k]
            self.writes.append({"print": self.h.current_print, "kind": "patch", "spool_id": spool["id"],
                                "body": dict(body), "t": self.h.clock.now - EPOCH})
            return _Resp(dict(spool))
        raise urllib.error.URLError(f"fake spoolman: no route {method} {path}")


# ── Home Assistant ───────────────────────────────────────────────────────────────────────────────────────

class FakeHA:
    def __init__(self, clock, spoolman):
        self.clock = clock
        self.spoolman = spoolman
        self.entities = {}        # entity_id -> {"state", "attributes", "last_changed"}
        self.listeners = []       # (app, callback, entity, attribute, kwargs)
        self.timers = []          # [due, seq, app, callback, kwargs]
        self._seq = 0
        self.alerts = []          # {"service", "title", "message", "t"}
        self.summaries = []       # end-of-print summary pushes
        self.service_calls = []

    # state ---------------------------------------------------------------------------------------------
    def _derived(self, entity_id):
        m = re.fullmatch(r"sensor\.p1s_tray_(\d)_fuel_gauge_remaining", entity_id)
        if m:
            slot = int(m.group(1))
            if slot not in FUEL_GAUGE_SLOTS:
                return None
            attrs = self.entities.get(tray_entity(slot), {}).get("attributes", {})
            try:
                g = float(attrs.get("tray_weight") or 0) * float(attrs.get("remain") or 0) / 100
            except (TypeError, ValueError):
                g = 0.0
            return {"state": str(round(max(0.0, g), 1)), "attributes": {}, "last_changed": self.clock.iso()}
        m = re.fullmatch(r"sensor\.ams_slot_(\d)_remaining_g", entity_id)
        if m:
            sid = self._spool_id(int(m.group(1)))
            spool = self.spoolman.spools.get(sid)
            g = round(spool["remaining_weight"]) if spool else 0
            return {"state": str(g), "attributes": {}, "last_changed": self.clock.iso()}
        m = re.fullmatch(r"sensor\.spoolman_spool_(\d+)", entity_id)
        if m:
            spool = self.spoolman.spools.get(int(m.group(1)))
            if spool is None:
                return None
            fil = spool["filament"]
            return {"state": str(spool["remaining_weight"]),
                    "attributes": {"remaining_weight": spool["remaining_weight"], "color_hex": fil.get("color_hex"),
                                   "material": fil.get("material"), "filament_material": fil.get("material")},
                    "last_changed": self.clock.iso()}
        return None

    def _spool_id(self, slot):
        try:
            return int(self.entities.get(f"input_text.ams_slot_{slot}_spool_id", {}).get("state") or 0)
        except ValueError:
            return 0

    def _record(self, entity_id):
        return self.entities.get(entity_id) or self._derived(entity_id)

    def get_state(self, entity_id, attribute=None):
        rec = self._record(entity_id)
        if rec is None:
            return None
        if attribute is None:
            return rec["state"]
        if attribute == "all":
            return {"entity_id": entity_id, "state": rec["state"], "attributes": dict(rec["attributes"]),
                    "last_changed": rec["last_changed"], "last_updated": rec["last_changed"]}
        if attribute in ("last_changed", "last_updated"):
            return rec["last_changed"]
        return rec["attributes"].get(attribute)

    def set(self, entity_id, state=None, attributes=None, replace_attributes=False):
        """Set a state and/or attributes, then fire the listeners that care (in registration order)."""
        old = self.entities.get(entity_id)
        old_all = self.get_state(entity_id, "all") if old else None
        new = {"state": old["state"] if old else "unknown",
               "attributes": {} if (replace_attributes or not old) else dict(old["attributes"]),
               "last_changed": old["last_changed"] if old else self.clock.iso()}
        if state is not None:
            new["state"] = str(state)
        if attributes:
            new["attributes"].update(attributes)
        if old is None or new["state"] != old["state"]:
            new["last_changed"] = self.clock.iso()
        self.entities[entity_id] = new
        new_all = self.get_state(entity_id, "all")
        for app, cb, ent, attr, kw in list(self.listeners):
            if ent != entity_id:
                continue
            if attr is None:
                o, n = (old["state"] if old else None), new["state"]
            elif attr == "all":
                o, n = old_all, new_all
                if o is not None and o["state"] == n["state"] and o["attributes"] == n["attributes"]:
                    continue
            else:
                o = old["attributes"].get(attr) if old else None
                n = new["attributes"].get(attr)
            if o == n:
                continue
            cb(entity_id, attr, o, n, kw)

    # services ------------------------------------------------------------------------------------------
    def call_service(self, service, **kw):
        service = service.replace(".", "/")
        self.service_calls.append({"service": service, "t": self.clock.now - EPOCH, **kw})
        if service == "input_text/set_value":
            self.set(kw["entity_id"], str(kw.get("value", "")))
        elif service == "input_boolean/turn_on":
            self.set(kw["entity_id"], "on")
        elif service == "input_boolean/turn_off":
            self.set(kw["entity_id"], "off")
        elif service in ALERT_SERVICES or service.startswith("notify/"):
            if kw.get("title") in SUMMARY_TITLES:
                self.summaries.append({"title": kw.get("title"), "message": kw.get("message", "")})
                return
            self.alerts.append({"service": service, "title": kw.get("title", ""), "message": kw.get("message", ""),
                                "t": self.clock.now - EPOCH})

    # timers --------------------------------------------------------------------------------------------
    def run_in(self, app, callback, delay, kwargs):
        self._seq += 1
        self.timers.append([self.clock.now + float(delay), self._seq, app, callback, kwargs])
        return f"timer_{self._seq}"

    def cancel_timer(self, handle):
        seq = int(str(handle).split("_")[-1])
        self.timers = [t for t in self.timers if t[1] != seq]

    def advance_to(self, t_abs):
        """Run every timer due up to t_abs in due order (a timer may schedule more), then set the clock."""
        while True:
            due = sorted((t for t in self.timers if t[0] <= t_abs), key=lambda t: (t[0], t[1]))
            if not due:
                break
            tmr = due[0]
            self.timers.remove(tmr)
            self.clock.now = max(self.clock.now, tmr[0])
            tmr[3](dict(tmr[4]))
        self.clock.now = max(self.clock.now, t_abs)


def tray_entity(slot):
    if slot == 8:
        return f"sensor.{PREFIX}_externalspool_external_spool"
    ams, idx = SLOT_TO_AMS_TRAY[slot]
    unit = 1 if ams == 0 else ams
    return f"sensor.{PREFIX}_ams_{unit}_tray_{idx + 1}"


def _bind_app(cls):
    """Subclass an app so every Hass API call goes to the harness's FakeHA."""

    class Bound(cls):
        def __init__(self, ha, name, args):
            try:
                super().__init__(None, name, None, args, None, None, None)
            except TypeError:
                pass
            self.args = args
            self.name = name
            self._ha = ha
            self.logs = []

        def get_state(self, entity_id=None, attribute=None, **kw):
            return self._ha.get_state(entity_id, attribute)

        def set_state(self, entity_id, state=None, attributes=None, **kw):
            self._ha.set(entity_id, state, attributes)

        def listen_state(self, callback, entity=None, attribute=None, **kw):
            self._ha.listeners.append((self, callback, entity, attribute, kw))
            return f"listen_{len(self._ha.listeners)}"

        def listen_event(self, callback, event=None, **kw):
            return "event_listener"

        def run_in(self, callback, delay, **kw):
            return self._ha.run_in(self, callback, delay, kw)

        def cancel_timer(self, handle, *a, **kw):
            self._ha.cancel_timer(handle)

        def call_service(self, service, **kw):
            return self._ha.call_service(service, **kw)

        def log(self, msg, level="INFO", *a, **kw):
            self.logs.append((self._ha.clock.now - EPOCH, level, str(msg)))

    Bound.__name__ = f"Harness{cls.__name__}"
    return Bound


# ── scenario runner ──────────────────────────────────────────────────────────────────────────────────────

def _hex(color):
    c = str(color).lstrip("#").upper()
    return c[:6]


class Harness:
    def __init__(self, scenario, tmpdir):
        self.sc = scenario
        self.tmp = str(tmpdir)
        self.clock = FakeClock()
        self.current_print = None
        self.spoolman = FakeSpoolman(self)
        self.ha = FakeHA(self.clock, self.spoolman)
        self.cache_dir = os.path.join(self.tmp, "ha-bambulab", "prints", "cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.data_dir = os.path.join(self.tmp, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.prints = {}          # label -> {"task", "status"}
        self.print_order = []
        self._slots = {}

    # setup ---------------------------------------------------------------------------------------------
    def _seed_entities(self):
        ha = self.ha
        ha.set("input_boolean.filament_iq_enabled", "on")
        ha.set(f"sensor.{PREFIX}_print_status", "idle")
        ha.set(f"sensor.{PREFIX}_task_name", "")
        ha.set(f"sensor.{PREFIX}_gcode_file_downloaded", "")
        ha.set(f"sensor.{PREFIX}_print_weight", "0")
        ha.set(f"sensor.{PREFIX}_print_progress", "0")
        ha.set(f"sensor.{PREFIX}_active_tray", "none", {"ams_index": None, "tray_index": None})
        for slot in ALL_SLOTS:
            ha.set(f"input_text.ams_slot_{slot}_spool_id", "0")
            ha.set(f"input_boolean.ams_slot_{slot}_ran_out", "off")
            ha.set(tray_entity(slot), "Empty", self._empty_tray_attrs())
        for s in self.sc.get("slots", []):
            self._load_slot(s)

    @staticmethod
    def _empty_tray_attrs():
        return {"empty": True, "active": False, "tag_uid": NO_TAG, "tray_uuid": NO_UUID, "remain": 0,
                "tray_weight": 0, "remain_enabled": True, "color": "#00000000", "type": "", "name": ""}

    def _load_slot(self, s):
        slot = int(s["slot"])
        spool_id = int(s["spool_id"])
        rfid = bool(s.get("rfid", False))
        material = s.get("material", "PLA")
        color = _hex(s.get("color", "FFFFFF"))
        filament_id = s.get("filament_id", f"GFL{material[:2].upper()}")
        tray_weight = float(s.get("tray_weight", 1000))
        initial = float(s.get("initial_g", tray_weight if rfid else 1000))
        if rfid:
            remaining = float(s.get("spoolman_remaining_g", tray_weight * float(s["remain"]) / 100))
            uuid = s.get("tray_uuid") or f"{spool_id:032X}"
            lot_nr = uuid
        else:
            remaining = float(s["spoolman_remaining_g"])
            lot_nr = f"{material}|{filament_id}|{color}"
        filament = {"id": int(s.get("filament_db_id", 1000 + hash((material, color)) % 1000)),
                    "name": s.get("name", f"Fake {material} {color}"), "material": material,
                    "color_hex": color, "vendor": {"name": "Fake"}}
        if spool_id not in self.spoolman.spools:
            self.spoolman.add(spool_id, remaining, initial, filament, lot_nr)
        attrs = {"empty": False, "active": False, "type": material, "color": f"#{color}FF",
                 "name": filament["name"], "filament_id": filament_id, "remain_enabled": True}
        if rfid:
            attrs.update({"tag_uid": s.get("tag_uid", f"{spool_id:016X}"), "tray_uuid": s.get("tray_uuid") or f"{spool_id:032X}",
                          "remain": int(s["remain"]), "tray_weight": tray_weight})
        else:
            attrs.update({"tag_uid": NO_TAG, "tray_uuid": NO_UUID, "remain": -1, "tray_weight": 0})
        self._slots[slot] = {"spool_id": spool_id, "rfid": rfid, "material": material, "color": color}
        self.ha.set(tray_entity(slot), filament["name"], attrs, replace_attributes=True)
        self.ha.set(f"input_text.ams_slot_{slot}_spool_id", str(spool_id))

    def _start_apps(self):
        args = {
            "module": "filament_iq.ams_print_usage_sync", "class": "AmsPrintUsageSync", "enabled": True,
            "spoolman_url": SPOOLMAN_URL, "printer_serial": "YOUR_PRINTER_SERIAL", "printer_model": "p1s",
            "printer_ip": "", "dry_run": False, "min_consumption_g": 2, "max_consumption_g": 1000,
            "threemf_enabled": True, "printer_ftps_port": 990,
            "lifecycle_phase1_enabled": True, "lifecycle_phase2_enabled": True, "lifecycle_phase3_enabled": True,
            "weight_reconcile_enabled": True, "bambulab_cache_path": self.cache_dir + "/",
            "data_dir": self.data_dir, "ams_units": AMS_UNITS, "notify_service": "mobile_app_YOUR_DEVICE",
        }
        args.update(self.sc.get("usage_args", {}))
        self.usage = _bind_app(_usage_mod.AmsPrintUsageSync)(self.ha, "ams_print_usage_sync", args)
        self.usage.initialize()
        rargs = {"enabled": True, "printer_serial": "YOUR_PRINTER_SERIAL", "printer_model": "p1s",
                 "data_dir": self.data_dir, "ams_units": AMS_UNITS, "startup_delay_seconds": 15}
        self.runout = _bind_app(_runout_mod.RunoutTracker)(self.ha, "ams_runout_tracker", rargs)
        self.runout.initialize()

    # events --------------------------------------------------------------------------------------------
    def _set_active(self, slot):
        e = f"sensor.{PREFIX}_active_tray"
        if slot is None:
            self.ha.set(e, "none", {"ams_index": None, "tray_index": None})
            return
        ams, idx = SLOT_TO_AMS_TRAY[int(slot)]
        for s in ALL_SLOTS:
            if self.ha.entities[tray_entity(s)]["attributes"].get("active"):
                self.ha.set(tray_entity(s), attributes={"active": False})
        self.ha.set(tray_entity(int(slot)), attributes={"active": True})
        name = self.ha.entities[tray_entity(int(slot))]["state"]
        self.ha.set(e, name, {"ams_index": ams, "tray_index": idx})

    def _write_3mf(self, task, filaments):
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>", "  <plate>"]
        for i, f in enumerate(filaments):
            lines.append(
                f'    <filament id="{i}" used_g="{float(f["used_g"]):.2f}" used_m="{float(f.get("used_m", 0)):.2f}" '
                f'color="#{_hex(f["color"])}FF" type="{f.get("material", "PLA")}" '
                f'tray_info_idx="{f.get("filament_id", "GFL99")}"/>')
        lines += ["  </plate>", "</config>"]
        path = os.path.join(self.cache_dir, f"1-{task}.slice_info.config")
        with open(path, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        os.utime(path, (self.clock.now, self.clock.now))

    def _event(self, ev):
        kind = ev["event"]
        ha = self.ha
        st = f"sensor.{PREFIX}_print_status"
        if kind == "print_start":
            label = ev.get("label", ev["task"])
            self.current_print = label
            self.prints[label] = {"task": ev["task"], "status": None}
            self.print_order.append(label)
            for s in ev.get("slots", []):      # spools loaded/changed between prints
                self._load_slot(s)
            if ev.get("threemf") is not None:
                self._write_3mf(ev["task"], ev["threemf"])
            ha.set(f"sensor.{PREFIX}_task_name", ev["task"])
            ha.set(f"sensor.{PREFIX}_gcode_file_downloaded", f"1-{ev['task']}.gcode")
            total = sum(float(f["used_g"]) for f in ev.get("threemf") or [])
            ha.set(f"sensor.{PREFIX}_print_weight", str(ev.get("print_weight_g", round(total, 2))))
            ha.set(f"sensor.{PREFIX}_print_progress", "0")
            ha.set(st, "prepare")
            ha.set(st, "running")
            self._set_active(ev.get("active_slot"))
        elif kind == "active_slot":
            self._set_active(ev["slot"])
        elif kind == "rfid":
            ha.set(tray_entity(int(ev["slot"])), attributes={"remain": int(ev["remain"])})
        elif kind == "progress":
            ha.set(f"sensor.{PREFIX}_print_progress", str(ev["pct"]))
        elif kind == "runout":
            # The AMS reports the tray empty and drops its RFID data (scenario may keep the tag: keep_tag=true),
            # then feeds the continuation tray, if any.
            slot = int(ev["slot"])
            attrs = {"empty": True, "remain": 0}
            if not ev.get("keep_tag", False):
                attrs.update({"tag_uid": NO_TAG, "tray_uuid": NO_UUID, "tray_weight": 0})
            if not ev.get("keep_filament_info", False):
                attrs.update({"color": "#00000000", "type": "", "name": ""})
            ha.set(tray_entity(slot), "Empty", attrs)
            if ev.get("to") is not None:
                self._set_active(None)
                self._set_active(int(ev["to"]))
        elif kind == "tray_empty":
            ha.set(tray_entity(int(ev["slot"])), "Empty", {"empty": True})
        elif kind == "offline":
            ha.set(st, "unavailable")
            ha.set(f"sensor.{PREFIX}_active_tray", "unavailable", {"ams_index": None, "tray_index": None})
        elif kind == "online":
            ha.set(st, ev.get("status", "running"))
            if ev.get("active_slot") is not None:
                self._set_active(ev["active_slot"])
        elif kind == "pause":
            ha.set(st, "pause")
        elif kind == "resume":
            ha.set(st, "running")
        elif kind == "end":
            status = ev["status"]
            if self.current_print:
                self.prints[self.current_print]["status"] = status
            ha.set(st, status)
            self._set_active(None)
        elif kind == "idle":
            ha.set(st, "idle")
        else:
            raise ValueError(f"unknown event {kind!r}")

    def run(self, monkeypatch):
        monkeypatch.setattr(urllib.request, "urlopen", self.spoolman.urlopen)
        monkeypatch.setattr(_usage_mod, "time", _time_module(self.clock))
        monkeypatch.setattr(_usage_mod, "datetime", _datetime_module(self.clock))
        self._seed_entities()
        self._start_apps()
        self.ha.advance_to(self.clock.now + 20)       # runout tracker startup_init, app settle
        t0 = self.clock.now
        last = 0.0
        for ev in sorted(self.sc["timeline"], key=lambda e: float(e["t"])):
            self.ha.advance_to(t0 + float(ev["t"]))
            self._event(ev)
            last = float(ev["t"])
        self.ha.advance_to(t0 + last + float(self.sc.get("settle_s", 300)))   # deferred RFID reconcile (60 s)
        return self.result()

    # result --------------------------------------------------------------------------------------------
    def result(self):
        charged, per_print = {}, {}
        for w in self.spoolman.writes:
            if w["kind"] != "use":
                continue
            charged[w["spool_id"]] = round(charged.get(w["spool_id"], 0.0) + w["grams"], 2)
            pp = per_print.setdefault(w["print"], {})
            pp[w["spool_id"]] = round(pp.get(w["spool_id"], 0.0) + w["grams"], 2)
        methods = {}
        for _, _, msg in self.usage.logs:
            m = re.search(r"USAGE_PATCHED slot=\d+ spool_id=(\d+) consumption_g=\S+ method=(\S+)", msg)
            if m:
                methods.setdefault(int(m.group(1)), []).append(m.group(2))
        spools = {sid: {"remaining_g": s["remaining_weight"], "location": s["location"], "archived": s["archived"]}
                  for sid, s in self.spoolman.spools.items()}
        return {"charged": charged, "per_print": per_print, "methods": methods, "spools": spools,
                "alerts": list(self.ha.alerts), "writes": list(self.spoolman.writes)}

    def log_text(self):
        return "\n".join(f"{t:8.1f} {lvl:7} {msg}" for t, lvl, msg in self.usage.logs + self.runout.logs)


def check(scenario, res):
    """Compare a result to the scenario's `expect` block. Returns a list of human-readable mismatches."""
    exp = scenario["expect"]
    tol = float(exp.get("tolerance_g", 1.0))
    bad = []
    for sid, want in (exp.get("charged") or {}).items():
        got = res["charged"].get(int(sid), 0.0)
        if abs(got - float(want)) > tol:
            bad.append(f"spool {sid}: charged {got:.2f} g, expected {float(want):.2f} g (±{tol:g})")
    if exp.get("charged") is not None:
        for sid, got in res["charged"].items():
            if str(sid) not in exp["charged"] and abs(got) > tol:
                bad.append(f"spool {sid}: charged {got:.2f} g, expected nothing")
    for label, want_map in (exp.get("per_print") or {}).items():
        got_map = res["per_print"].get(label, {})
        for sid, want in want_map.items():
            got = got_map.get(int(sid), 0.0)
            if abs(got - float(want)) > tol:
                bad.append(f"print {label} spool {sid}: charged {got:.2f} g, expected {float(want):.2f} g (±{tol:g})")
    for sid, want in (exp.get("remaining_g") or {}).items():
        got = res["spools"][int(sid)]["remaining_g"]
        if abs(got - float(want)) > tol:
            bad.append(f"spool {sid}: remaining {got:.2f} g after, expected {float(want):.2f} g (±{tol:g})")
    for sid in exp.get("emptied", []):
        s = res["spools"][int(sid)]
        if s["remaining_g"] > tol or s["location"] != "Empty":
            bad.append(f"spool {sid}: expected zeroed and location Empty, got {s['remaining_g']:.2f} g, "
                       f"location {s['location']}")
    for sid in exp.get("not_emptied", []):
        s = res["spools"][int(sid)]
        if s["location"] == "Empty" or s["archived"]:
            bad.append(f"spool {sid}: marked Empty/archived but it did not run out")
    for sid, want in (exp.get("methods") or {}).items():
        got = res["methods"].get(int(sid), [])
        if want not in got:
            bad.append(f"spool {sid}: method {got or 'none'}, expected {want}")
    allowed = [a.lower() for a in exp.get("alerts", [])]
    for a in res["alerts"]:
        if not any(x in (a["title"] + " " + a["message"]).lower() for x in allowed):
            bad.append(f"unexpected alert at t={a['t']:.0f}: {a['service']} {a['title']!r}")
    for want in allowed:
        if not any(want in (a["title"] + " " + a["message"]).lower() for a in res["alerts"]):
            bad.append(f"expected an alert matching {want!r}, none sent")
    return bad
