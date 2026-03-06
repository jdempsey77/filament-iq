# Troubleshooting

## Helpers resetting on HA restart

**Cause:** `initial:` in configuration for a FilamentIQ-managed helper overwrites runtime state on restart.

**Fix:** Remove all `initial:` from helpers in the `filament_iq.yaml` package for reconciler-owned fields (e.g. `ams_slot_*_spool_id`, `ams_slot_*_status`, `ams_slot_*_expected_spool_id`). Keep `initial` only where a default is intended (e.g. `p1s_slot_to_spool_binding_json: "{}"`).

## RFID spool recognized inconsistently

**Cause:** Some Bambu spools have dual NFC chips reporting different UIDs depending on spool orientation.

**Fix:** FilamentIQ uses `tray_uuid` (spool factory serial) as primary identity, not `tag_uid`. Ensure the spool is seated consistently. If recognition flips, try rotating the spool 180° and re-seating.

## Non-RFID slots not tracked

**Cause:** Legacy HA automation still enabled (e.g. `p1s_record_trays_used_during_print`).

**Fix:** Disable any pre-existing tray tracking automations. AppDaemon handles tray tracking internally via `active_tray` sensor listeners, avoiding `mode:restart` race conditions.

## Dashboard not updating after file deploy

**Cause:** Dashboard is in HA storage mode and doesn't read YAML files from disk.

**Fix:** Import via HA UI: Settings → Dashboards → Add Dashboard → Import from YAML, or edit via Settings → Dashboards → Raw configuration editor.

## AppDaemon logs truncated

**Cause:** HA Supervisor keeps only ~100 lines of addon logs.

**Fix:** Enable file-based logging in AppDaemon configuration so logs persist to disk. AppDaemon's `appdaemon.yaml` supports `log` section with file targets.

## 3MF fetch fails (no Tier 1 allocation)

**Cause:** `curl` not found on the HA host, wrong printer IP, or invalid access code.

**Fix:**
1. Verify `curl` is available: `docker exec homeassistant curl --version`
2. Check `printer_ip` in `apps.yaml` matches your printer's LAN IP
3. Verify access code via `access_code_entity` or `printer_access_code` config
4. Check AppDaemon logs for lines starting with `3MF_FETCH:`

## Slot shows UNBOUND after spool change

**Cause:** The reconciler can't match the tray's identity to exactly one Spoolman spool.

**Diagnosis:** Check `input_text.ams_slot_{N}_unbound_reason` for the specific reason code:

| Reason | Meaning |
|--------|---------|
| `UNBOUND_TRAY_EMPTY` | Tray is empty — no spool to match |
| `UNBOUND_TRAY_UNAVAILABLE` | Tray sensor state is `unknown`/`unavailable` |
| `UNBOUND_NO_RFID_TAG_ALL_ZERO` | Non-RFID tray (tag_uid all zeros) |
| `UNBOUND_TAG_UID_NO_MATCH` | RFID tag not found in any Spoolman spool |
| `UNBOUND_TAG_UID_AMBIGUOUS` | Multiple spools match the same RFID tag |
| `UNBOUND_TAG_UID_INELIGIBLE_LOCATION_NEW` | Matching spool exists but has location `New` |

**Fix:** For `NO_MATCH`, load the spool into the tray so the reconciler can auto-enroll it. For `AMBIGUOUS`, check Spoolman for duplicate `lot_nr` values. For `INELIGIBLE_LOCATION_NEW`, change the spool's location from `New` to `Shelf` in Spoolman.

## Consumption logged as 0g or unexpectedly low

**Cause:** Tray activity tracking didn't detect the slot being active during the print, or 3MF data was unavailable.

**Diagnosis:**
1. Check logs for `TRAY_TRACKING_START` / `TRAY_TRACKING_END` — verify `trays_used` includes expected slots
2. Check for `3MF_PARSED` — verify filaments were extracted
3. Check for `3MF_MATCH` — verify filaments matched to slots
4. If `TRAY_TRACKING_FALLBACK` appears, AppDaemon may have restarted mid-print

**Fix:** Ensure `active_tray` entity (`sensor.p1s_{SERIAL}_active_tray`) is reporting correctly. The ha-bambulab integration must be on a version that provides `ams_index` and `tray_index` attributes.

## Guard quarantined a spool unexpectedly

**Cause:** RFID Guard detected a policy violation — spool in an AMS location has RFID tag or Bambu vendor filament but no identity (`lot_nr` or `ha_spool_uuid`).

**Fix:**
1. In Spoolman, set the spool's location back from `QUARANTINE` to its AMS slot
2. Press the manual reconcile button so the reconciler can re-enroll it with a proper `lot_nr`
3. If this keeps happening, set `missing_ha_spool_uuid_mode: warn_only` in Guard config to log warnings instead of quarantining

## Startup reconcile times out

**Cause:** HA helpers aren't ready within `startup_wait_helpers_seconds` (default 420s).

**Symptoms:** Log shows `STARTUP_WAIT_TIMEOUT` and reconcile never runs.

**Fix:** Press the manual reconcile button (`input_button.filament_iq_reconcile_now`) once HA is fully loaded. If this happens regularly, increase `startup_wait_helpers_seconds` or decrease `startup_delay_seconds`.

## "CONFLICT: MISMATCH" status on a slot

**Cause:** The tray's color or material doesn't match the bound Spoolman spool's metadata. The spool is still bound (consumption tracking works) but the mismatch is flagged.

**Fix:** This is informational — the spool identity is correct but metadata differs. Update the spool's color/material in Spoolman to match, or adjust `color_distance_threshold` if the colors are visually close but numerically different (default threshold: 90 Euclidean RGB distance).
