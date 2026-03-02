# Test dashboard (storage mode)

Use a **storage-mode** dashboard named "Test" so you can view and edit the Spool management config directly in Home Assistant and debug Configuration errors.

## Create the Test dashboard in HA

1. **Settings** → **Dashboards** → **Add dashboard**.
2. Set **Title** to `Test` (or any name). Create it.
3. Open the new **Test** dashboard (it will be empty or have one default view).
4. **⋮** (top right) → **Edit dashboard**.
5. **⋮** again → **Raw configuration**.
6. **Replace the entire contents** with the contents of `dashboards/dashboard.test.storage.yaml` from this repo (copy the YAML; you can skip the comment lines at the top if you prefer).
7. **Save** (✓). Reload the dashboard or refresh the page.

You can now:

- See the exact config HA is using (edit again via **Edit dashboard** → **Raw configuration**).
- Change cards/entities and save to test fixes; any errors will show which card or entity is wrong.
- Use **Developer Tools** → **States** alongside the Test dashboard to confirm entity IDs for slots 2, 4, 6 and fix them in the raw config.

## File roles

- **dashboard.test.storage.yaml** — Copy/paste source for the Test storage dashboard (Spool management view only). Uses `/dashboard-test/` nav paths. Not loaded from the repo; HA does not read this file.
- **dashboard.stage.yaml** — Stage YAML dashboard (deployed to `/lovelace-stage`). Uses `/lovelace-stage/` nav paths. Deploy via `./scripts/manage_ha.sh --stage`.
- **dashboard.prod.yaml** — Generated from stage with `/lovelace/` nav paths. For manual copy to the main (prod) dashboard. Regenerated automatically when you run `--stage`; or run `./scripts/manage_ha.sh --prod-prep` to regenerate without deploying.

## Push to prod

1. Deploy stage: `./scripts/manage_ha.sh --stage` (also regenerates `dashboard.prod.yaml`).
2. Test at `/lovelace-stage`.
3. Copy to prod: **Settings** → **Dashboards** → [Main dashboard] → **⋮** → **Edit dashboard** → **⋮** → **Raw configuration** → paste contents of `dashboards/dashboard.prod.yaml` → **Save**.
