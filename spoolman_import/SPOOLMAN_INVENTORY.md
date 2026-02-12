# Filament inventory before Spoolman

Get your physical spools labeled and weighed so you can fill `spools.csv` with **remaining_g** and **empty_spool_g**. Spoolman uses these for tracking and for the “subtract after print” flow.

**Inventory is tracked in this repo.** `spools.csv` and `weighing_sheet.csv` are version-controlled. When you clean up rows, add weights, or change names, commit and push so you have a history of what you had and when.

---

## Start here (don’t do everything at once)

If this feels like too much, do **only** the 6 spools currently in your AMS (the ones marked `in_use` in `spools.csv`). Everything else can wait.

1. **One empty spool weight** – Find one empty spool that’s the same type as your in-use spools (e.g. Overture 1 kg). Weigh it in grams. Write that number in the form-factor table below (and in `empty_spool_g` for those 6 rows in `spools.csv`).
2. **Weigh the 6 in-use spools** – Take each of the 6 spools out of the AMS, weigh it (spool + filament) in grams. In `spools.csv`, set **remaining_g = (that weight) − (empty spool weight from step 1)** for the matching row.
3. **Commit** – `git add spoolman_import/spools.csv && git commit -m "Spoolman: weights for 6 in-use spools"`. Done for now. You can add the rest of the inventory and other form factors later.

---

## 1. Form factors (spool types)

You have different spool sizes/types. For each **form factor** you need **one empty spool weight** that you’ll reuse for every spool of that type. **Empty** = spool + anything you leave on when the filament is gone (e.g. silica container, plastic AMS adapters on cardboard spools). That way `current_weight - empty_spool_g` = remaining filament only.

| Form factor (label it) | Empty spool weight (g) | Notes |
|------------------------|-------------------------|-------|
| Overture (cardboard)   | 130                     | Cardboard + silica (70g) + rubber/plastic guides (12g) |
| Bambu Lab              | 296                     | Plastic spool (226g) + silica (70g) |
| (add more)             | _____                   | |

Add-ons: silica container 70g; AMS guides (cardboard spools only) 12g.

- Weigh **one empty** of each form factor (with silica/adapters as you use them) on a kitchen or small scale (grams).
- Write that weight in this table and in `empty_spool_g` for every spool of that type in `spools.csv`.

---

## 2. Weighing each spool (current weight)

For each spool you want to track:

1. **Label the spool** (optional but helpful): name/brand/material/color or a short ID that you’ll use in the CSV (e.g. “Overture PLA Black”, “BL Basic Green”).
2. **Weigh the whole spool** (spool + filament + silica + adapters, as it sits in the AMS or on the shelf) in **grams**.
3. **Remaining filament** (what goes in `remaining_g`):
   - **remaining_g = current_weight_g − empty_spool_g**
   - Use the **empty_spool_g** for that form factor from the table above (same setup: silica, adapters, etc.).
   - If you don’t have an empty yet, you can leave `remaining_g` and `empty_spool_g` blank in the CSV and fill them later, or use a typical value (e.g. ~250 g for many 1 kg empty spools) as an estimate.

---

## 3. Weighing sheet (use while you weigh)

Use `weighing_sheet.csv` (or paper). Fill one row per spool. The **name** column must match the spool name in `spools.csv` exactly so the merge script can update weights.

| # | Form factor | Empty spool (g) | Name / ID | Current weight (g) | Remaining (g) |
|---|-------------|------------------|-----------|---------------------|---------------|
| 1 |             |                  |           |                     | current − empty |
| 2 |             |                  |           |                     | |
| … |             |                  |           |                     | |

- **Form factor** – e.g. “Bambu 1kg”, “Overture 1kg”.
- **Empty spool (g)** – from your form-factor table (same for all of that type).
- **Name / ID** – must match how you want it in Spoolman (e.g. “Overture PLA Black (AMS1 Slot 3)”).
- **Current weight (g)** – scale reading for spool + filament.
- **Remaining (g)** – current weight − empty spool (for that form factor).

---

## 4. Getting the data into spools.csv

Your CSV has (among others) these columns:

- **name** – Spool/filament name (must be unique in Spoolman).
- **brand**, **material**, **color**, **status**, **location**, **notes** – as you already use.
- **remaining_g** – from your “Remaining (g)” column.
- **empty_spool_g** – from your form-factor table for that spool type.

After you fill these in:

1. Save `spools.csv`.
2. Run the import (see main [README](README.md)):  
   `python3 import_spools.py --dry-run` then `python3 import_spools.py` (with `SPOOLMAN_URL` or `--url`).
3. New spools are created; rows that already exist in Spoolman (by name) are skipped. To **update** existing spools’ remaining or empty weight, use the Spoolman UI or API (the import script is idempotent and skips existing names).

---

## 5. If you don’t have an empty spool yet

- **Option A:** Use a typical empty weight for that form factor (e.g. ~250 g for many 1 kg plastic spools, ~180 g for some refills). Put that in **empty_spool_g** and set **remaining_g = current_weight − that value**. You can correct later when you weigh a real empty.
- **Option B:** Leave **remaining_g** and **empty_spool_g** blank in the CSV and import. Then update remaining weight in Spoolman’s UI as you weigh or use filament.
- **Option C:** For “new” or “refill” spools, use the manufacturer’s stated full weight (e.g. 1000 g) as “current” and a typical empty weight to get an initial remaining (e.g. 1000 − 250 = 750 g). Refine when you have real empty weights.

---

## 6. Quick checklist

- [ ] List your form factors (spool types).
- [ ] Weigh one empty of each form factor; record in the table and on labels.
- [ ] For each spool: put **name** (matching `spools.csv`) in weighing_sheet, weigh (current g), fill remaining_g or current_weight_g + empty_spool_g.
- [ ] Run `merge_weighing_into_spools.py` to write weights into `spools.csv`.
- [ ] Run `validate_spools.py`, then import; use Spoolman or HA automations as you move forward.

Once inventory and weights are in place, you can move on to driving Spoolman from the active tray and low-filament warnings.
