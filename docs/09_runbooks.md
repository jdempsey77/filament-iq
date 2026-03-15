# Operational Runbooks

## Add new RFID spool
Insert the spool into an AMS tray. The reconciler auto-enrolls on first read: it matches `tray_uuid` to Spoolman (or unenrolled fallback), writes `tray_uuid` to `lot_nr`, and binds the slot. Verify with AppDaemon logs: look for enrollment/PATCH of `lot_nr` and slot status transitioning to RFID_REGISTERED/OK.

## Add non-RFID spool
Create the spool in Spoolman and set **Spoolman color** to match the Bambu profile color (hex or name). Put the filament in an AMS tray. The reconciler builds a tray sig (type|filament_id|color_hex) and matches against Spoolman (lot_nr first, then material+color for unenrolled). It auto-matches on insert when there is a single candidate; otherwise NEEDS_MANUAL_BIND. Use dashboard “Bind Spool” or assign script if needed.

## Reset tray signature
To force re-identification of the tray (e.g. after a bad bind): clear the helper  
`input_text.ams_slot_X_tray_signature`  
(for the slot X in 1–6). Next reconcile will treat the tray as new and re-run matching.

## Repair missing helper
If a slot’s `spool_id` helper is missing or wrong and the slot should be unbound: set  
`input_text.ams_slot_X_spool_id`  
to `0`, then trigger a reconcile (e.g. fire `bambu_rfid_reconcile_now` or wait for the next poll). Reconciler will re-run matching for that slot.

## Handle 404 in Spoolman
If Spoolman returns 404 for a spool (e.g. spool was deleted): clear the slot’s spool_id helper (`input_text.ams_slot_X_spool_id` = 0). Reconciler will rematch on next run; no code change required.

## Clear pending window
To clear the sticky “pending” state for a slot so it can re-match: clear  
`input_text.ams_slot_X_tray_signature`  
Then run reconcile. The slot will be re-evaluated from tray state.

## Re-seed spool_id
To force the slot to re-resolve from tray identity: set  
`input_text.ams_slot_X_spool_id`  
to `0`, then trigger a reconcile event (e.g. `bambu_rfid_reconcile_now` or safety poll). Reconciler will repopulate from lot_nr/sig or material+color match.

## Correct corrupted Spoolman weight
If a spool’s remaining weight in Spoolman is wrong (e.g. double-counted or wrong initial): use Spoolman API to **PATCH** the spool’s **used_weight** (or equivalent) directly so that remaining = initial − used. Do not rely on the sync app to “fix” historical weight; it only adds new consumption.

## Recover from double-patch
If the same print was applied twice to Spoolman (e.g. duplicate P1S_PRINT_USAGE_READY or restart before dedup persisted): (1) Check **seen_job_keys** in `appdaemon/apps/data/seen_job_keys.json` to confirm the job_key was recorded. (2) Correct Spoolman: PATCH the spool’s **used_weight** (or remaining) to the correct value (subtract the duplicate consumption once). (3) Optionally remove that job_key from `seen_job_keys.json` only if you need to re-apply the same print again (rare).

## Review print history

Print history records are stored in `appdaemon/apps/data/print_history/{job_key}.json`. Each record contains: job_key, timestamp, per-slot decisions (method, consumption_g, confidence, pre/post remaining), 3MF metadata, and final status.

To review a specific print:
1. Find the job_key from AppDaemon logs or HA notification history
2. Read the file: `cat /addon_configs/a0d7b954_appdaemon/apps/data/print_history/{job_key}.json | python3 -m json.tool`
3. Check per-slot `method` and `confidence` fields to understand how consumption was calculated
4. Compare `pre_remaining` and `post_remaining` to verify the write was correct

To list all print history: `ls -lt /addon_configs/a0d7b954_appdaemon/apps/data/print_history/`

## Diagnose no_evidence slot

When a slot reports `no_evidence` in the print history or notification, consumption could not be determined. Common causes:

1. **RFID spool, missed print start** — AppDaemon restarted during a print, so `start_g` was never captured. Check logs for `ACTIVE_PRINT_PERSISTED` at print start. If missing, the start snapshot was lost.
   - Resolution: No automatic fix. Manually calculate consumption from Bambu slicer estimate or weigh the spool. PATCH Spoolman `used_weight` directly.

2. **Non-RFID spool, no 3MF data** — 3MF fetch failed all retries, and the tray was not empty at print end.
   - Resolution: Check logs for `3MF_FETCH_FAILED` or `threemf_unavailable`. Verify printer FTPS connectivity (`192.168.4.114:990`). If the print file is still on the SD card, trigger a manual 3MF fetch or calculate from slicer estimate.

3. **Non-RFID spool, tray not empty, no 3MF** — The decision engine cannot resolve consumption without either RFID delta or 3MF data for a non-depleted non-RFID spool.
   - Resolution: Same as (2). For future prints, ensure 3MF fetch succeeds by verifying FTPS access before printing.
