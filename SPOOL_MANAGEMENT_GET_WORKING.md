# Spool management – get it working

Minimal steps to deploy and verify the Spool management flow.

## 1. Deploy (run these for the files you changed)

| If you changed | Run |
|----------------|-----|
| `dashboards/dashboard.stage.yaml` | `./scripts/manage_ha.sh --stage` |
| `automations.yaml` | `./scripts/manage_ha.sh --automations` then in HA: **Settings → Automations → ⋮ → Reload** |
| `scripts.yaml` | `./scripts/manage_ha.sh --scripts` |
| `configuration.yaml` (or first time) | `./scripts/manage_ha.sh --config` then restart HA if needed |

**To push everything at once:**

```bash
./scripts/manage_ha.sh --stage
./scripts/manage_ha.sh --automations
./scripts/manage_ha.sh --scripts
```

Then in HA: **Settings → Automations → ⋮ → Reload**.

## 2. Test in Home Assistant

1. Open the **Stage** dashboard and go to **Spool management** (or your Test dashboard with the same view).
2. Tap **Reload Spoolman** (or **Refresh list**). Wait a couple of seconds.
3. Open **Assign from warehouse – select spool** and pick a spool (e.g. one you use in Slot 1).
4. Scroll to **Slot 1** and tap **Assign selected spool here**. Slot 1’s name/remaining should update to that spool.
5. Optionally: set **gross weight**, leave **spool type** as-is, tap **Update (gross − tare → Spoolman)**. In Spoolman (or after **Reload Spoolman**) the remaining weight should reflect the update.

## 3. If something doesn’t work

- **Dropdown empty after Refresh list**  
  - Reload **Template** entities: **Developer Tools → YAML → Template entities → Reload** (template sensor builds the list from Spoolman entities).
  - Check **Developer Tools → States**: search `spoolman_spool` — you should see one sensor per spool (e.g. `sensor.spoolman_spool_11`). Search `ams_spool_list_options` — its **options** attribute should be a list of `"id - name"` strings.
  - If Spoolman sensors exist but options is empty, ensure configuration is deployed and HA has been restarted or Template entities reloaded after the fix.

- **Assign selected spool here does nothing**  
  Confirm you picked a spool (not “— Select spool —”). In **Developer Tools → States** check `input_number.ams_assign_source_spool_id` (should be > 0 when a spool is selected).

- **Slot doesn’t update after assign**  
  Check `input_text.ams_slot_1_spool_id` (or the slot you used) in States; it should match the spool ID. Template sensors refresh on the next HA update cycle; reload the dashboard or wait a few seconds.

- **Update (gross − tare) fails**  
  Check **Settings → Logs** for `spoolman.patch_spool` errors. Ensure the slot’s spool ID exists in Spoolman and the Spoolman integration is connected.
