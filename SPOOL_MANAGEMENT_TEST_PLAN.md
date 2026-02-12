# Spool Management – Slot 1 Test Plan

Run this after HA has finished rebooting (and optionally wait 15–30 seconds for the REST sensor to complete its first fetch).

---

## 1. Dropdown has real options (no user action)

- [ ] Open the **Stage** dashboard → **Spool management** view.
- [ ] Open the **Assign from warehouse – select spool** dropdown.
- [ ] **Pass:** Dropdown shows real spools (e.g. `11 - Gray`, `27 - Blue`), not only "— Select spool —".
- [ ] **Pass:** Opening the dropdown is **instant** (no spinning cursor).

If the list is still only "— Select spool —", wait another 30 seconds and try again (REST sensors may still be loading). **Quick diagnostics (Developer Tools → States):**
- **sensor.spoolman_spool_list_api** – `state` should be a number (spool count), not `unknown`/`unavailable`. If unknown, check Spoolman base URL (input_text or Spoolman integration) and HA logs for REST errors.
- **sensor.spoolman_spool_list_options_raw** – `state` should be a JSON string like `["11 - Gray","27 - Blue",...]`. If unknown/empty, same connectivity check.
- **sensor.ams_spool_list_options** – **attributes.options** MUST be a non-empty list (e.g. `["11 - Gray", "27 - Blue"]`). This is what the dropdown and automation use. If options is `[]`, the template sensor is not getting valid data from the options_raw sensor.

---

## 2. Selecting a spool sets the numeric ID

- [ ] In the dropdown, select a spool (e.g. **27 - Blue**).
- [ ] Open **Developer Tools → States**.
- [ ] Find **input_number.ams_assign_source_spool_id**.
- [ ] **Pass:** Its value is the spool ID (e.g. `27`).

---

## 3. Assign selected spool to Slot 1

- [ ] With a spool still selected (e.g. 27 - Blue), scroll to **Slot 1**.
- [ ] Tap **Assign selected spool here** on Slot 1.
- [ ] In **Developer Tools → States**, find **input_text.ams_slot_1_spool_id**.
- [ ] **Pass:** Value is the same ID (e.g. `27`).

---

## 4. Slot 1 name and remaining from Spoolman

- [ ] Look at the Slot 1 card on the Spool management page.
- [ ] **Pass:** **Slot 1** shows the spool name (e.g. "Blue" or full filament name).
- [ ] **Pass:** **In Spoolman** shows remaining weight in grams (e.g. `950 g`).
- [ ] No reload, no spinner; data came from Spoolman via template sensors.

---

## 5. No Reload Spoolman needed for the list

- [ ] **Pass:** You did **not** tap "Reload Spoolman" to get the dropdown to fill; it filled after reboot when REST data arrived (automation).

---

## Slot 1 definition of done

All of the above pass. After that, the same pattern can be applied to slots 2–6.

---

## If something fails

| Failure | Check |
|--------|--------|
| Dropdown never gets options | States: `sensor.spoolman_spool_list_api` (state string like `11 - Gray,27 - Blue`?), `sensor.ams_spool_list_options.options` (list?). Automation `ams_populate_spool_dropdown_on_rest_data` enabled and triggered? |
| Select spool but ID stays 0 | Automation `ams_assign_source_spool_from_select`: trigger/condition/action. Option format must be `"id - name"` (number, space, hyphen, space, name). |
| Assign to Slot 1 does nothing | Script `ams_assign_to_slot_1`: runs only when `ams_assign_source_spool_id` > 0. Check script ran (log or trace). |
| Slot 1 name/remaining wrong or empty | `input_text.ams_slot_1_spool_id` matches a real Spoolman spool ID. Spoolman integration has `sensor.spoolman_spool_<id>` for that ID. |
