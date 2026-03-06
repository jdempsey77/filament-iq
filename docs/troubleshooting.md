# FilamentIQ Troubleshooting

Common issues and solutions.

---

## AppDaemon not starting

**Symptom:** AppDaemon add-on fails to start, or FilamentIQ apps do not load.

**Checks:**

1. **apps.yaml syntax** — Validate YAML (no tabs, correct indentation). Use a YAML linter or `python3 -c "import yaml; yaml.safe_load(open('apps.yaml'))"`.
2. **Module path** — Ensure `appdaemon/apps/filament_iq/` is in the AppDaemon apps directory. The folder must be named `filament_iq` and contain `__init__.py`, `ams_rfid_reconcile.py`, etc.
3. **Required keys** — Each app needs its required keys (e.g. `spoolman_url`, `printer_serial`). Missing keys cause `ValueError` on startup. Check AppDaemon logs: **Settings** → **Add-ons** → **AppDaemon** → **Log**.
4. **Python dependencies** — AppDaemon includes `hassapi`; no extra pip packages required for core apps. If using 3MF parsing, ensure `appdaemon` is up to date.

**Fix:** Correct `apps.yaml`, fix folder structure, restart AppDaemon add-on.

---

## Slots showing UNBOUND

**Symptom:** `input_text.ams_slot_N_unbound_reason` shows values like `NEEDS_MANUAL_BIND`, `UNBOUND_TAG_UID_NO_MATCH`, or `UNBOUND_TRAY_EMPTY`.

**Causes and fixes:**

| Reason | Cause | Fix |
|--------|-------|-----|
| `UNBOUND_TRAY_EMPTY` | Tray is empty | Normal; insert filament |
| `UNBOUND_NO_TAG_UID` | No RFID read | Ensure RFID chip is present and tray is seated; wait for printer to read |
| `UNBOUND_NO_RFID_TAG_ALL_ZERO` | Non-RFID tray | Enable non-RFID: `input_boolean.filament_iq_nonrfid_enabled` = on. Add spool to Spoolman with matching material/color |
| `UNBOUND_TAG_UID_NO_MATCH` | RFID not in Spoolman | Add spool to Spoolman, set `extra.rfid_tag_uid` (or use "Register from tray" flow if available) |
| `UNBOUND_TAG_UID_AMBIGUOUS` | Multiple spools match | Resolve in Spoolman (different locations, or remove duplicate). Or use manual bind on dashboard |
| `NEEDS_MANUAL_BIND` | No auto-match for non-RFID | Assign spool via dashboard: select spool from dropdown, assign to slot |
| `UNBOUND_SPOOLMAN_LOOKUP_FAILED` | Spoolman unreachable | Check `spoolman_url`, network, Spoolman container/process |

**Manual reconcile:** Press `input_button.filament_iq_reconcile_now` to force a reconciliation run.

---

## RFID not detected

**Symptom:** Tray has RFID chip but slot stays `PENDING_RFID_READ` or `UNBOUND_NO_TAG_UID`.

**Checks:**

1. **Printer integration** — Ensure `ha-bambulab` (or equivalent) exposes tray entities with `tag_uid` attribute. Check **Developer Tools** → **States** → tray entity → attributes.
2. **Tag format** — Spoolman stores `extra.rfid_tag_uid` as JSON-encoded string. Reconcile normalizes (uppercase, strip). Mismatched encoding can prevent match.
3. **Tray reseat** — Remove and reinsert tray; printer may need to re-read RFID.
4. **Pending window** — `PENDING_RFID_READ` can last ~20 seconds. Wait before assuming failure.

**Fix:** Verify tray entity has `tag_uid`; ensure Spoolman spool has matching `rfid_tag_uid`; reseat tray if needed.

---

## Spoolman connectivity

**Symptom:** Reconcile fails, Guard/Usage sync errors, "Spoolman lookup failed".

**Checks:**

1. **URL** — `spoolman_url` must be reachable from the host running AppDaemon (e.g. `http://192.168.4.124:7912`). No trailing slash.
2. **Network** — If AppDaemon runs in a container, ensure it can reach Spoolman (same Docker network, or host IP).
3. **Spoolman running** — Confirm Spoolman is up: `curl http://YOUR_SPOOLMAN_IP:7912/api/v1/health` or open `/api/v1/openapi.json` in browser.
4. **CORS / auth** — Spoolman typically has no auth; if you added auth, AppDaemon does not send credentials by default.

**Fix:** Correct `spoolman_url`, fix network/firewall, restart Spoolman if needed.

---

## Print usage not syncing

**Symptom:** Prints complete but Spoolman `remaining_weight` does not decrease.

**Checks:**

1. **Event** — `ams_print_usage_sync` is triggered by `P1S_PRINT_USAGE_READY`. Ensure your HA automation fires this event on print finish. Check **Developer Tools** → **Events** → listen for `P1S_PRINT_USAGE_READY`.
2. **Slot bindings** — `input_text.ams_slot_N_spool_id` must be set (non-zero) for slots used during print. Unbound slots are skipped.
3. **Snapshots** — HA automation must record start/end snapshots to `input_number.filament_iq_start_slot_N_g` and `input_number.filament_iq_end_slot_N_g`. Check these entities after a print.
4. **Dedup** — Same `job_key` is only applied once. If you re-run finish automation, it may be deduplicated. Check `appdaemon/apps/filament_iq/data/seen_job_keys.json`.
5. **Consumption bounds** — Values outside `min_consumption_g` (default 2) and `max_consumption_g` (default 300) are ignored. Check AppDaemon logs for "skipped" or "out of range".
6. **dry_run** — If `ams_print_usage_sync` has `dry_run: true`, it logs but does not write. Set to `false`.

**Fix:** Verify event, slot bindings, snapshots; disable dry_run; check logs.
