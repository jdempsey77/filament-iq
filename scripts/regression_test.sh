#!/usr/bin/env python3
import subprocess, json, os, sys, re

env = {}
deploy_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy.env")
with open(deploy_env) as f:
    for line in f:
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            env[k] = v.strip('"').strip("'")

VERBOSE = "--verbose" in sys.argv
URL = env.get("HOME_ASSISTANT_URL", "")
TOKEN = env.get("HOME_ASSISTANT_TOKEN", "")
SPOOLMAN = env.get("SPOOLMAN_URL", "")
PRINTER = env.get("PRINTER_PREFIX", "p1s_01p00a1b2c3d4e5f")
AUTH = "Authorization: Bearer " + TOKEN

PASS = WARN = FAIL = 0
GP = GW = GF = 0

def curl_get(url):
    r = subprocess.run(["curl", "-s", "-H", AUTH, url], capture_output=True, text=True)
    try: return json.loads(r.stdout)
    except: return {}

def curl_get_raw(url):
    r = subprocess.run(["curl", "-s", url], capture_output=True, text=True)
    return r.stdout

def http_code(entity):
    r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                        "-H", AUTH, URL + "/api/states/" + entity],
                       capture_output=True, text=True)
    return r.stdout.strip()

def get_entity(entity):
    return curl_get(URL + "/api/states/" + entity)

def p(msg):
    global PASS, GP
    PASS += 1; GP += 1
    if VERBOSE: print("  PASS ", msg)

def w(msg):
    global WARN, GW
    WARN += 1; GW += 1
    print("  WARN ", msg)

def f(msg):
    global FAIL, GF
    FAIL += 1; GF += 1
    print("  FAIL ", msg)

def group(name):
    global GP, GW, GF
    print(f"  -> {name}: {GP} passed, {GW} warned, {GF} failed")
    GP = GW = GF = 0

def state(d): return d.get("state", "")
def attrs(d): return d.get("attributes", {})

VALID_OPERATOR = {"printing_normally","idle","failed_requires_intervention",
                  "paused","waiting_for_user","unknown","unavailable"}

BOOLEANS = [
    "input_boolean.filament_iq_nonrfid_enabled",
    "input_boolean.filament_iq_print_active",
    "input_boolean.filament_iq_needs_reconcile",
    "input_boolean.filament_iq_debug_finish_trigger",
    "input_boolean.filament_iq_startup_suppress_swap",
    "input_boolean.filament_iq_debug_mode",
    "input_boolean.filament_iq_decrement_on_failed",
    "input_boolean.filament_iq_auto_mode_opinionated",
]
TEXTS = [
    "input_text.filament_iq_trays_used_this_print",
    # filament_iq_last_mapping_json is now a command_line sensor, checked in GROUP 3
    "input_text.filament_iq_start_json",
    "input_text.filament_iq_end_json",
    "input_text.filament_iq_active_job_key",
    "input_text.filament_iq_last_active_tray",
    "input_text.filament_iq_last_print_status_transition",
    "input_text.filament_iq_finish_automation_checkpoint",
    "input_text.filament_iq_slot_to_spool_binding_json",
    "input_text.bambu_printer_access_code",
    "input_text.spoolman_base_url",
]
TEXT_MAX_EXEMPT = {"input_text.bambu_printer_access_code"}
BUTTONS = ["input_button.filament_iq_reconcile_now", "input_button.filament_iq_weight_snapshot_now"]
SELECTS = ["input_select.spoolman_new_spool_filament"]
NUMBERS = [f"input_number.filament_iq_{se}_slot_{i}_g" for i in range(1,7) for se in ("start","end")]
DATETIMES = ["input_datetime.filament_iq_print_start_time", "input_datetime.filament_iq_print_end_time"]
ALL = BOOLEANS + TEXTS + BUTTONS + SELECTS + NUMBERS + DATETIMES + ["sensor.filament_iq_operator_status"]

# Connectivity
print("\nChecking HA connectivity...")
d = curl_get(URL + "/api/")
if not d and not isinstance(d, dict):
    print("FAIL: Cannot reach HA API"); sys.exit(1)
r = subprocess.run(["curl","-s","-o","/dev/null","-w","%{http_code}","-H",AUTH,URL+"/api/"],
                   capture_output=True, text=True)
if r.stdout.strip() != "200":
    print(f"FAIL: HA API returned {r.stdout.strip()}"); sys.exit(1)
print("PASS: HA API reachable")

# GROUP 1
print("\n[GROUP 1] Entity existence and domain")
for entity in ALL:
    code = http_code(entity)
    if code == "200":
        d = get_entity(entity)
        if state(d) == "unavailable": w(f"{entity} (unavailable)")
        else: p(entity)
    else: f(f"{entity} (HTTP {code})")
group("GROUP 1")

# GROUP 2
print("\n[GROUP 2] Helper type correctness")
for entity in BOOLEANS:
    d = get_entity(entity); s = state(d)
    if s in ("on","off"): p(f"{entity} ({s})")
    elif s in ("unavailable","unknown"): w(f"{entity} ({s})")
    else: f(f"{entity} -- expected on/off got: '{s}'")

for entity in TEXTS:
    if entity in TEXT_MAX_EXEMPT:
        p(f"{entity} (max exempt)"); continue
    d = get_entity(entity)
    maxlen = attrs(d).get("max")
    if maxlen is None: w(f"{entity} -- no max attribute")
    elif int(maxlen) >= 255: p(f"{entity} (max:{int(maxlen)})")
    else: f(f"{entity} -- max={int(maxlen)} < 255")

for entity in NUMBERS:
    d = get_entity(entity); a = attrs(d)
    if "min" in a and "max" in a: p(f"{entity} ({a['min']}/{a['max']})")
    else: f(f"{entity} -- missing min/max")

for entity in DATETIMES:
    d = get_entity(entity); s = state(d)
    if re.match(r'^\d{4}-\d{2}-\d{2}', s): p(f"{entity} ({s})")
    elif s in ("unknown","unavailable"): w(f"{entity} ({s})")
    else: f(f"{entity} -- unexpected: '{s}'")

for entity in BUTTONS:
    code = http_code(entity)
    if code == "200": p(entity)
    else: f(f"{entity} (HTTP {code})")

for entity in SELECTS:
    d = get_entity(entity)
    if attrs(d).get("options"): p(entity)
    else: w(f"{entity} -- options empty")
group("GROUP 2")

# GROUP 3
print("\n[GROUP 3] AppDaemon health")
for entity, label in (("sensor.filament_iq_last_mapping_json", "last_mapping_json"),
                      ("input_text.filament_iq_slot_to_spool_binding_json", "slot_to_spool_binding_json")):
    d = get_entity(entity)
    s = state(d).strip().strip("'\"")
    if not s or s == "unknown": w(f"{label} empty")
    else:
        try: json.loads(s); p(f"{label} valid JSON ({s})")
        except Exception as e: f(f"{label} invalid JSON: {s} ({e})")

d = get_entity("sensor.filament_iq_operator_status")
s = state(d)
if s in VALID_OPERATOR: p(f"operator_status ({s})")
else: f(f"operator_status unexpected: '{s}'")
group("GROUP 3")

# GROUP 4
print("\n[GROUP 4] Spoolman connectivity")
if not SPOOLMAN:
    w("SPOOLMAN_URL not set")
else:
    for endpoint in ("api/v1/info", "api/v1/spool", "api/v1/filament"):
        r = subprocess.run(["curl","-s","-o","/dev/null","-w","%{http_code}",
                            f"{SPOOLMAN}/{endpoint}"], capture_output=True, text=True)
        code = r.stdout.strip()
        if code == "200":
            if endpoint == "api/v1/spool":
                raw = curl_get_raw(f"{SPOOLMAN}/{endpoint}")
                try:
                    data = json.loads(raw)
                    count = len(data) if isinstance(data, list) else len(data.get("items", data.get("spools", [])))
                    if count > 0: p(f"{endpoint} ({count} spools)")
                    else: w(f"{endpoint} -- 0 spools")
                except: w(f"{endpoint} -- parse error")
            else: p(endpoint)
        else: f(f"{endpoint} (HTTP {code})")
group("GROUP 4")

# GROUP 5
print(f"\n[GROUP 5] Printer sensors ({PRINTER})")
for suffix in ("print_status", "active_tray", "task_name"):
    entity = f"sensor.{PRINTER}_{suffix}"
    code = http_code(entity)
    if code == "200":
        d = get_entity(entity); p(f"{entity} ({state(d)})")
    else: f(f"{entity} (HTTP {code})")
group("GROUP 5")

# GROUP 6
print("\n[GROUP 6] AMS slot helpers")
for slot in range(1, 7):
    for suffix in ("spool_id", "status", "unbound_reason"):
        entity = f"input_text.ams_slot_{slot}_{suffix}"
        code = http_code(entity)
        if code == "200":
            d = get_entity(entity); p(f"{entity} ({state(d)})")
        else: f(f"{entity} (HTTP {code})")
group("GROUP 6")

print(f"\n========================================")
print(f"  PASSED : {PASS}")
print(f"  WARNED : {WARN}")
print(f"  FAILED : {FAIL}")
print(f"========================================")
sys.exit(1 if FAIL > 0 else 0)
