# Consumption harness (IN-1494)

Proves what Filament IQ charges Spoolman for a whole print, end to end, so every consumption fix is shown
by a scenario and can't regress. Run it with the rest of the suite:

```bash
python3 -m pytest tests/test_consumption_harness.py -v
```

## What runs, what is fake

The REAL apps run: `filament_iq.ams_print_usage_sync` (from `initialize()` on, every listener, the 3MF cache
read, the decision engine, the Spoolman writes, the deferred RFID reconcile) and `filament_iq.runout_tracker`.
Only the edges are fake (`fiq_harness.py`):

| Fake | What it does |
|---|---|
| Home Assistant | States and attributes; `listen_state` callbacks fire in registration order (usage sync first, then the runout tracker); `run_in` timers on a controlled clock; `input_text` / `input_boolean` service calls land back in the state map; notifications are recorded. Template sensors from `ha-config/packages/filament_iq.yaml` are computed on read: `p1s_tray_N_fuel_gauge_remaining` (slots 1-6 only, as in the package) and `ams_slot_N_remaining_g`, plus `sensor.spoolman_spool_N`. |
| Spoolman | `urllib.request.urlopen` is patched. `GET /spool`, `GET /spool/N`, `PUT /spool/N/use`, `PATCH /spool/N`. `remaining = initial - used`, no clamp at 0 (like Spoolman). Every write is recorded with the print it happened in. |
| Clock | `time` and `datetime` inside the usage-sync module. No sleeps; a 3-hour print runs in milliseconds. |
| 3MF | Written as `<cache>/1-<task>.slice_info.config` (mtime = the fake clock) and read by the app's own ha-bambulab cache path (`_try_cache_3mf` → `parse_slice_info_file`). |

Slots: 1-4 AMS 2 Pro, 5-7 AMS HT, 8 external, as in the live `apps.yaml`. App config mirrors the live one:
lifecycle phases 1-3 on, `min_consumption_g` 2, `max_consumption_g` 1000, reconcile on, no auto-archive (a
spool that runs out is set to location `Empty`, which is what "marked for archive" means today).

Not driven: `ams_rfid_reconcile` (slot binding). Each scenario binds its spools directly
(`input_text.ams_slot_N_spool_id`).

## Scenario format (`scenarios/<id>.json`)

Plain JSON (no YAML dependency in CI). The `//` comments below are for explanation only.

```jsonc
{
 "id": "01_rfid_single_colour",           // = file name
 "title": "...", "notes": "what happens and why the expected numbers are right",
 "slots": [                                 // spools loaded before the first print
  {"slot": 1, "spool_id": 10, "rfid": true, "remain": 85,          // RFID: remain % (tray_weight 1000 default)
   "material": "PLA", "color": "FF0000", "filament_id": "GFA00", "filament_db_id": 101},
  {"slot": 3, "spool_id": 20, "rfid": false, "spoolman_remaining_g": 700, "material": "PLA", "color": "1A1A1A"}
 ],
 "timeline": [                              // t = seconds from the start; events run in t order
  {"t": 0, "event": "print_start", "task": "fake_task", "label": "57", "active_slot": 1,
   "threemf": [{"used_g": 41.0, "used_m": 13.7, "color": "FF0000", "material": "PLA", "filament_id": "GFA00"}]},
  {"t": 1200, "event": "rfid", "slot": 1, "remain": 83},
  {"t": 3600, "event": "end", "status": "finish"}
 ],
 "expect": {
  "charged": {"10": 40.0},                  // grams written via /use per spool, whole scenario ({} = nothing)
  "per_print": {"57": {"98": 111.4}},       // optional: grams per print label
  "remaining_g": {"10": 810.0},             // Spoolman remaining after everything (reconcile included)
  "emptied": [10], "not_emptied": [11],     // emptied = ~0 g and location Empty
  "methods": {"10": "rfid_delta"},          // a USAGE_PATCHED method the spool must have
  "alerts": ["spool depleted"],             // every alert sent must match one of these; each must be sent
  "tolerance_g": 1.0
 },
 "xfail": {"reason": "what today's code gets wrong", "in": "IN-1493"}   // optional: strict xfail
}
```

Events: `print_start` (task name, gcode file, print weight, status prepare → running, active tray; optional
`slots` to load spools between prints), `active_slot`, `rfid` (tray remain %), `progress` (%), `runout`
(`slot`, optional `to`: the tray reads Empty; by default it also drops its tag/uuid to zeros and its colour to
`#00000000`, `keep_tag` / `keep_filament_info` keep them; the active tray reads none, then `to`), `tray_empty`,
`offline` / `online` (print status and active tray unavailable, then back), `pause` / `resume`, `end`
(`status`: finish, failed, cancelled, ...), `idle`. After the last event the clock runs `settle_s` (300) more
seconds so the 60 s RFID reconcile fires.

"Alerts" are pushes and bell entries. The routine end-of-print summary push (Print Complete / Failed /
Cancelled) is not counted.

## Wrong answers

When today's code gets a scenario wrong, the scenario keeps its correct expectation and carries an `xfail`
block with the reason and an IN id. It runs as `xfail(strict=True)`: the fix that makes it pass fails the suite
until the block is removed, so the fix PR proves itself. `pytest --runxfail` shows what each one gets today.

Fake data only: placeholder serial, `192.0.2.x` URLs, made-up spool ids and task names.
