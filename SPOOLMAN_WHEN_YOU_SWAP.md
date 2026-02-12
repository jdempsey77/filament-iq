# Every time you change a spool (swap in a slot)

**Reserved IDs 1–6:** We reserve Spoolman spool IDs **1, 2, 3, 4, 5, 6** for the six AMS slots. Slot N always uses spool ID N. You never pick an ID in HA—you pick **name/color** by editing the spool in Spoolman (set which filament is in that slot). The dashboard shows **ID → name/color** from Spoolman; you only set remaining weight and tap Update.

**Short version:** In Spoolman, **edit** the spool for that slot (ID 1–6): change its filament to the new one, set remaining/empty weight. In HA, Spool management page: set weight, tap Update, tap Reload Spoolman. If the printer reports a new display name (e.g. after swapping), add that name → spool_id 1–6 in automations (see Step 2).

---

## The process (in order)

1. **In Spoolman — slot = spool ID**  
   Slot 1 = spool ID 1, slot 2 = spool ID 2, … slot 6 = spool ID 6. **Edit** the existing spool for that slot (don’t create a new spool with a new ID): change its **Filament** to the new type you put in, set **Remaining weight** and **Empty weight**, set **Location** to the AMS slot, save.

2. **In your repo / HA**  
   - **Dashboard:** No change needed for IDs—slot N is always spool N. Deploy stage if you changed anything else.  
   - **Automations:** If the printer (or you) reports a **new display name** for that slot (e.g. a new filament name), add that name → spool_id 1–6 in **both** automations in `automations.yaml` (see Step 2 below). Deploy automations, then reload (or wait for the Spoolman threshold automation to run `automation.reload`).

---

## Step 1 — In Spoolman (required)

1. **Old spool** — **Move to Shelf** (edit spool → Location = Shelf) or **delete** it if empty.
2. **New spool** — Click **Add spool** (or Create spool).
   - **Filament** = the type you put in (e.g. "Bambu Basic Blue"). Create the filament first if it doesn't exist (name = type only, no slot in the name).
   - **Location** = the slot you refilled (e.g. **AMS1_Slot2**).
   - **Remaining weight** and **Empty weight** = weigh or estimate, then save.
3. Use the **reserved spool ID** for that slot (1–6). If the slot didn’t have a spool yet, **create** a new spool in Spoolman and assign it to a **location** that maps to slot 1–6; Spoolman will assign an ID. To keep things simple, **reserve the first six IDs** in Spoolman for the six slots: create spools 1–6 (or move existing spools so IDs 1–6 are used for the six AMS slots). Then slot N = spool ID N everywhere.

Home Assistant will see the updated name/color and weight after a short time (or after reloading the Spoolman integration).

**Integration option:** In **Settings → Devices & Services → Spoolman → Configure**, enable **Enable polling for changes** so Home Assistant automatically polls Spoolman entities for state changes (remaining weight, etc.). With polling on, the dashboard will update on an interval; you can still use the Reload Spoolman button for an immediate refresh.

**Does the AMS report weight?** No. The AMS doesn’t have a weight sensor that reports remaining grams to HA. You only need to **weigh (or estimate) when you add or swap a spool** and set remaining + empty weight in Spoolman. After that, weight is updated automatically: the **subtract-after-print** automation uses the printer’s **print weight** (grams used for that job) and calls `spoolman.use_spool_filament` to subtract it from the active spool in Spoolman. So: set weight once per spool when it goes in; every finished print updates Spoolman for you.

---

## Manage Spools (3D Printer dashboard)

On the 3D Printer view, **Manage Spools** (under Power) links to the **Spool management** dashboard (`/lovelace-stage/spoolman`). There you can:

- **Spools in Spoolman (ID → name/color)** — Shows spool entities 1–6. Name and color come from Spoolman (filament name / color). Edit filament in the Spoolman UI; no ID dropdown in HA.
- **Set weight per slot** — For each slot (1–6), enter remaining (g) and tap **Update** to push to Spoolman. The card title shows the **name** from Spoolman (e.g. "Bambu Basic Blue"), not the ID.
- **Reload Spoolman** — Refreshes the integration so tray data in HA matches Spoolman.

**Reserved IDs 1–6 and ID → name translation**  
- Slot N always uses Spoolman spool ID N. In Spoolman, create or edit spools so that IDs 1–6 are used for your six AMS slots (e.g. spool 1 = AMS1 Slot 1, spool 2 = AMS1 Slot 2, … spool 6 = AMS2 HT Slot 2).
- Home Assistant does **not** create Spoolman entities—they appear when a spool with that ID exists in Spoolman. To have all six cards show data, ensure six spools with IDs 1–6 exist in Spoolman with locations set. Then reload the Spoolman integration; `sensor.spoolman_spool_1` … `sensor.spoolman_spool_6` will appear. The dashboard shows **name/color** from the entity attributes (`filament_name` or `name`); you never pick an ID in the UI.

The **Reload Spoolman** button reloads the Spoolman config entry so HA fetches fresh data; the tray cards then show current remaining weights. (Calling `homeassistant.update_entity` on the spool sensors does **not** trigger a refetch—the integration only refetches when its config entry is reloaded.)

**One-time setup: create a script the button calls**

The script lives in **`scripts.yaml`** (included from `configuration.yaml`). In that file, the script `reload_spoolman_integration` is already defined; you only need to set your Spoolman config entry ID:

1. Get your Spoolman **config entry ID**: **Settings → Devices & Services → Spoolman** → click the three dots → **Configure**. The page URL will look like `.../config/integrations/integration/XXXXXXXX` — that hex string is your entry ID. Or in **File Editor** open `.storage/core.config_entries`, search for `"domain": "spoolman"` and copy the `"entry_id"` value.
2. Open **`scripts.yaml`** and replace `ENTRY_ID` in the `reload_spoolman_integration` script with your actual entry ID. Optionally, use `entry_id: !secret spoolman_entry_id` and add `spoolman_entry_id` to `secrets.yaml` instead.
3. Deploy config (and restart HA if needed) so the script is loaded.

The dashboard button calls `script.reload_spoolman_integration`. If the script is missing or `ENTRY_ID` is still placeholder, the button will error until you fix it.

**How to confirm it worked**

- In Spoolman, change the **remaining weight** for one spool (e.g. subtract 10 g), save. In HA, open the 3D Printer page and tap **Reload Spoolman**. Within a few seconds the tray card for that spool should show the new value.
- Or: Developer Tools → States → filter `spoolman_spool`, note **Last updated** for one sensor. Tap Reload Spoolman, then check again — **Last updated** should be just now.

---

## Step 2 — In Home Assistant / repo (only for automations)

The low-filament and subtract-after-print automations need **"display name" → spool ID**. When you add a new spool, it has a **new name** and **new ID**, so you have to add that mapping once.

**Option A — You use the dropdown (input_select.active_filament_spool)**  
1. In HA, add a new **option** to the dropdown for this spool (e.g. "Bambu Basic Blue (AMS1 Slot 2)" or just "Bambu Basic Blue" — whatever you'll select when that slot is in use).  
2. In your repo, open **automations.yaml**. Search for `spool_id: "{% set s = active_spool %}`.  
3. In **both** automations (low filament and subtract after print), add a new line so the **exact** option text maps to the **new spool ID**. Example, before the `{% else %}0{% endif %}`:  
   `{% elif s == 'Bambu Basic Blue (AMS1 Slot 2)' %}31{% endif %}`  
   (Use the real option text and the real ID from Spoolman.)  
4. Save, deploy, and reload automations in HA.

**Option B — Printer reports the filament name (active_tray)**  
- If the printer sends a name that matches what Spoolman shows (e.g. "Bambu Lab - Bambu Basic Blue"), add that exact string to the mapping in **automations.yaml** as in Option A, so that name → new spool ID.  
- If the printer sends something different, use that exact string in the `{% elif s == '...' %}ID{% endif %}` line.

So: **each time you swap**, you move/delete the old spool and add the new one in Spoolman, then update the **dashboard** (slot → new spool ID) and **automations** (name → new spool ID) so the new spool is used.

---

## Best way to test

**Stage dashboard (after `--stage` deploy)**  
1. Open the stage dashboard (e.g. `/lovelace-stage` or your stage tab).  
2. Click through the 3D Printer section: Power, Reload Spoolman, tray cards, timer bar.  
3. Confirm layout and that entities show (no “unavailable” where you expect data).

**Reload Spoolman button**  
1. In Spoolman (UI or API), change **remaining weight** for one spool (e.g. subtract 10 g), save.  
2. In HA, open the 3D Printer page and tap **Reload Spoolman**.  
3. Within a few seconds the tray card for that spool should show the new value.  
4. Optional: Developer Tools → States → filter `spoolman_spool` → check **Last updated** before/after the button tap.

**Polling (if enabled)**  
- Change a weight in Spoolman and wait the polling interval (e.g. a few minutes). The dashboard should update without tapping Reload.

**Automations (low filament / subtract after print)**  
- **Low filament:** Temporarily set a spool’s remaining weight in Spoolman below 100 g (or 50 g for critical), save. With polling or after Reload Spoolman, the automation should run and create a persistent notification.  
- **Subtract after print:** Run a short print (or one you were already doing); when it finishes, check for the “Spoolman – filament used” notification and confirm the spool’s remaining weight in Spoolman decreased.  
- **Logs:** Settings → Logs — look for errors mentioning `spoolman`, `use_spool_filament`, or the automation aliases if something doesn’t fire.
