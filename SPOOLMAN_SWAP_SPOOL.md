# When you run out and put a new spool in a slot

You're not missing a "slot" — you're swapping one **spool record** for another. Slots are just the **Location** on a spool. Here's the flow.

---

## What actually exists

- **Spoolman** = a list of **spools** (each has name, weight, **location** like AMS1_Slot2).
- **Home Assistant** = one set of entities **per spool** (e.g. `sensor.spoolman_spool_27` for spool ID 27). When you delete a spool in Spoolman, that spool's entities go away. When you **add** a new spool, Spoolman gives it a **new ID** and HA shows new entities for it.
- **Slots** (AMS1_Slot1, AMS1_Slot2, …) = only a **label** you put on a spool. There is no separate "slot object." So when you run out and put a new spool in the same physical slot, you're really: **remove the old spool record → add a new spool record and set its location to that slot.**

---

## Step-by-step: run out → new spool in same slot

### 1. In Spoolman (browser)

- You already **deleted** the old spool. Good.
- **Add a new spool** for the new filament you put in:
  - Open Spoolman → **Spools** → **Add spool** (or Create spool).
  - Choose or create the **filament** (name, brand, material, color).
  - Set **Location** to the slot you used (e.g. **AMS1_Slot2** if that's the slot you refilled).
  - Set **remaining weight** and **empty weight** (weigh or estimate), then save.
- Write down the **new spool ID** (e.g. 31). Spoolman shows it after you save.

### 2. In Home Assistant

- After a short while (or after **Settings → Devices & services → Spoolman → Reload**), you should see new entities for that ID, e.g.:
  - `sensor.spoolman_spool_31`
  - `binary_sensor.spoolman_spool_31_low_filament`
- So you're not "missing" the slot — the **slot** is just the location on the **new** spool (e.g. 31). The old spool (e.g. 27) is gone; the new one (31) is what represents that slot now.

### 3. So the automations use the right spool

- The automations map **"display name" → spool ID** (e.g. "Overture PLA Red (AMS1 Slot 2)" → 27). Now that slot has a **different** spool (new name, new ID).
- You have two options:

**Option A — Use the dropdown (input_select.active_filament_spool)**  
- In HA: **Settings → Devices & services → Helpers** (or the place where you edit `input_select.active_filament_spool`).
- Add an **option** for the new spool (e.g. "Bambu Lab PLA Blue (AMS1 Slot 2)") and pick it when you use that slot.  
- Then in **automations.yaml** add a mapping for that name → **new spool ID** (e.g. 31). So the automations know: when the dropdown or printer says that name, use spool 31.

**Option B — Only use the printer's active tray**  
- If the printer reports the current filament name and that name is in the automation mapping, add a line for the **new** name → **new** ID. Example: in the `spool_id` template add `{% elif s == 'Bambu Lab PLA Blue (AMS1 Slot 2)' %}31{% endif %}` (and keep or remove the old "Overture PLA Red (AMS1 Slot 2)" → 27 if that spool is deleted).

So: **you're not missing "the slot."** You deleted one spool and added another. The new spool has a new ID; point the automations (and dropdown, if you use it) at that new ID for that slot's name.

---

## Quick checklist after a swap

- [ ] **Spoolman:** New spool created, **Location** = the slot you refilled (e.g. AMS1_Slot2).
- [ ] **Spoolman:** I know the new spool's **ID** (e.g. 31).
- [ ] **HA:** I see `spoolman_spool_<that ID>` in Developer Tools → States (reload Spoolman if not).
- [ ] **HA / automations:** Dropdown has an option for the new spool, and in `automations.yaml` the mapping includes that option name → new spool ID (in both automations).

Once that's done, low-filament and subtract-after-print will use the new spool for that slot.
