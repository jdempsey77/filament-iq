# Home Assistant Config

This repo holds Home Assistant configuration: dashboards, automations, and related YAML.

**If this folder is not yet a git repo**, you can turn it into one with: `git init` (from this directory). Ensure `scripts/deploy.env` is in `.gitignore` so secrets are not committed.

## Contents

- **configuration.yaml** – Main HA config (template sensors, Google Assistant, etc.)
- **automations.yaml** – Automations (lights, dishwasher, printer, dryer, washer, etc.)
- **dashboards/** – Lovelace dashboards
  - `dashboard.stage.yaml` – Stage dashboard; edit and deploy to `/lovelace-stage`
- **dashboard.yaml** – Symlink to `dashboards/dashboard.stage.yaml` (for deployment compatibility)

## Deploy via SSH

1. Copy `scripts/deploy.env.example` to `scripts/deploy.env` and fill in your SSH host, user, and config path.
2. **Dashboard workflow:** Deploy stage (`--stage`), test at `/lovelace-stage`, then when happy copy the repo contents (e.g. `dashboards/dashboard.stage.yaml`) into production from the repo. No `--promote`; prod is updated by copying from the repo.

### Deploying changes to Home Assistant

From the **repo root**, the most common deploy command is:

```bash
./scripts/manage_ha.sh --all
```

This deploys `configuration.yaml`, `automations.yaml`, and `go2rtc.yaml`, then restarts Home Assistant.

**Other useful commands:**
   - `./scripts/manage_ha.sh` — show usage
   - `./scripts/manage_ha.sh --stage` — deploy stage dashboard
   - `./scripts/manage_ha.sh --check` — compare local stage vs HA
   - `./scripts/manage_ha.sh --config` — deploy configuration.yaml and included files (scripts.yaml, scenes.yaml)
   - `./scripts/manage_ha.sh --scripts` — deploy scripts.yaml only
   - `./scripts/manage_ha.sh --all` — deploy all config (config + automations + go2rtc) and restart HA
   - `./scripts/manage_ha.sh --config --restart` — deploy config and restart HA
   - `./scripts/manage_ha.sh --config --validate` — deploy config, validate via HA API
   - `./scripts/manage_ha.sh --validate` — validate config currently on HA
   - `./scripts/manage_ha.sh --restart` — restart HA only

The script copies the YAML to HA. Refresh the browser at `/lovelace-stage` to see changes (HA detects updates and prompts to refresh).

### Stage changes not showing after `--stage`?

1. **View the Stage dashboard**  
   Open `https://YOUR_HA_URL/lovelace-stage` (or use the "Stage" entry in the HA sidebar). The default Lovelace dashboard is storage-based and does **not** use this YAML file.

2. **Hard refresh**  
   Use **Ctrl+Shift+R** (Windows/Linux) or **Cmd+Shift+R** (Mac), or open `/lovelace-stage` in a private/incognito window so the browser doesn’t use a cached dashboard.

3. **Confirm deploy**  
   From the repo root run:
   - `./scripts/manage_ha.sh --stage`
   - `./scripts/manage_ha.sh --check`
   If you see **DIFFERENT**, the file on HA doesn’t match your local file (deploy path, SSH, or unsaved edits). If you see **SAME**, the deploy worked; the issue is cache or which dashboard you’re viewing.

4. **Save before deploy**  
   Ensure `dashboards/dashboard.stage.yaml` is saved in your editor before running `--stage`.

### Using the visual editor, then pushing to Stage

The **Stage** dashboard is YAML-based, so HA does not show the visual editor on `/lovelace-stage`. You can design on a **storage-mode** dashboard (e.g. Test or Production) and then push that config into Stage.

1. **Play in a dashboard that has the visual editor**  
   Use **Test** or **Production** (or create a dashboard in **Settings → Dashboards → Add dashboard** and name it e.g. "Stage Play"). Edit it with the **visual editor** (pencil icon → add/edit cards and views).

2. **Copy the config from that dashboard**  
   When you’re happy with the layout:
   - Open that dashboard (Test, Stage Play, etc.).
   - Click **Edit** (pencil icon).
   - Open the **⋮** menu (top right) → **Raw configuration** (or **Edit in YAML** / **Code editor**, depending on your HA version).
   - **Select all** and **copy** the YAML (or JSON; see note below).

3. **Put it into the repo and deploy Stage**  
   - Open `dashboards/dashboard.stage.yaml` in your repo.
   - Replace the entire file content with what you copied. The file must be **YAML** and start with `views:` at the top level (if you pasted JSON, convert it to YAML or keep only the `views` part as YAML).
   - Save the file.
   - From the repo root run:  
     `./scripts/manage_ha.sh --stage`
   - Open **`/lovelace-stage`** and hard-refresh to see the update.

**Note:** If the raw config is JSON, use a converter (e.g. paste into an online YAML/JSON converter) so `dashboard.stage.yaml` is valid YAML starting with `views:`.

---

Copy or symlink these into your Home Assistant `config/` (or use your usual deployment) and reload the relevant integrations.
