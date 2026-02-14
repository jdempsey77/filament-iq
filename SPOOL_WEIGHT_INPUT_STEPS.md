# Spool weight input – weigh and submit to Spoolman

Use this flow when you load a spool from the shelf (e.g. open vacuum bag, weigh, put in AMS) and want to update Spoolman from Home Assistant only.

## Prerequisites

- Spoolman integration configured and running; at least one spool exists.
- Dashboard Spool management view deployed.

## Steps

1. Weigh the spool (with spool + silica/guides). Note gross weight in grams.
2. Open Spool management view in HA.
3. Choose the slot (1–6) for the spool you are loading.
4. In that slot card: open **Select Spool**, pick the spool (options are `ID - Name` from Spoolman). If list is empty, tap **Reload Spoolman** and retry.
5. Set **Spool Profile**: Bambu Lab (plastic), Overture cardboard, or Custom (then set Extras and Custom Tare if needed).
6. Set **Scale Weight (g)** to the gross weight from the scale.
7. Tap **Assign & Update**. Script assigns spool to slot, computes remaining = gross − tare (or − extras for Custom), patches Spoolman, reloads integration.
8. Check: slot card shows spool name and updated Remaining (g).

## Troubleshooting

- Dropdown empty: Reload Spoolman; ensure integration loaded and Spoolman has spools.
- Options must be format `ID - Name` (from sensor.ams_spool_list_options).
- Wrong remaining: Check Spool Profile and tare helpers (tare_bambu_lab_plastic, tare_overture_cardboard, or slot tare override).
- Spoolman not updating: Check logs for patch_spool errors; confirm Spoolman URL and spool ID exists.
