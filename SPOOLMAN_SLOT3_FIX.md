# Fix missing Slot 3 – walkthrough

Follow these steps in order. Each step says **where** you are (Spoolman or Home Assistant).

---

## Step 1 — In Spoolman (browser)

1. Open Spoolman in your browser (same URL as your integration, e.g. `http://192.168.4.124:7912` or whatever you use).
2. Go to the **Spools** list (main spool list).
3. Look for a spool that you use in **AMS1 Slot 3** (e.g. "Overture PLA Black (AMS1 Slot 3)" or similar).
4. **If you find it:**
   - Click that spool to open it.
   - Note the **spool ID** (often in the page title or URL, e.g. "Spool #28" or `.../spool/28`).
   - Check the **Location** field. If it's empty or not `AMS1_Slot3`, set it to **AMS1_Slot3** and save.
   - Remember this ID (e.g. 28) for later.
5. **If you don't find a spool for Slot 3:**
   - Click **Add spool** (or **Create spool**).
   - Choose or create the **filament** (name/brand/material/color) for the filament in Slot 3.
   - Set **Location** to **AMS1_Slot3**.
   - Enter **remaining weight** and **empty weight** if you know them (you can change later).
   - Save.
   - Note the **new spool ID** Spoolman shows (e.g. 30).

---

## Step 2 — In Home Assistant

1. Open **Home Assistant**.
2. Go to **Developer Tools** (left sidebar, under "Tools" or "Settings").
3. Open the **States** tab.
4. In the **Filter** box at the top, type: **spoolman_spool**
5. Look at the list. You'll see entities like:
   - `sensor.spoolman_spool_24`
   - `binary_sensor.spoolman_spool_24_low_filament`
   - and the same pattern for other numbers (25, 26, 27, 28, 29, etc.).
6. Write down **which numbers** you see (e.g. 24, 25, 26, 27, 29 — and maybe 28 or 30).
7. **If you see the same ID you wrote down in Step 1** (e.g. 28 or 30) — Spoolman and HA are in sync for that spool. Go to Step 3.
8. **If you don't see that ID** (e.g. you created spool 30 in Spoolman but there's no `spoolman_spool_30` in HA):
   - Wait 1–2 minutes.
   - In HA go to **Settings → Devices & services**, find **Spoolman**, and click **Reload** (or restart HA).
   - Check the States filter again; the new spool should appear.

---

## Step 3 — In your repo (automations)

Your automations assume Slot 3's spool has ID **28**. If in Step 1 you saw a **different** ID (e.g. 30):

1. Open the file **`automations.yaml`** in your repo.
2. Search for: **Overture PLA Black (AMS1 Slot 3)**
3. You'll see something like: `{% elif s == 'Overture PLA Black (AMS1 Slot 3)' %}28{% endif %}`.  
   Change **28** to the ID you wrote down in Step 1 (e.g. **30**).
4. Search again for the **same** text; there are two automations (low filament and subtract after print). Change **28** to the same ID in **both** places.
5. Save the file, then deploy/reload automations in HA (e.g. **Settings → Automations → Reload**, or use your usual deploy script).

---

## Step 4 — Check again in Home Assistant

1. In HA, go back to **Developer Tools → States**.
2. Filter again: **spoolman_spool**
3. You should now see entities for **all six** slots, including the Slot 3 spool (e.g. `sensor.spoolman_spool_28` and `binary_sensor.spoolman_spool_28_low_filament`, or 30 if that's the ID you used).

---

## Summary

| Where | What you do |
|-------|-------------|
| **Spoolman (browser)** | Make sure a spool exists for Slot 3, set its **Location** to `AMS1_Slot3`, and note its **spool ID**. |
| **Home Assistant** | Developer Tools → States, filter `spoolman_spool`, confirm that ID appears. Reload Spoolman if needed. |
| **automations.yaml (repo)** | If Slot 3's spool ID is not 28, replace **28** with that ID in both Spoolman automations. |
| **Home Assistant** | Reload automations and check States again. |
