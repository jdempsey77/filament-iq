# Code Workflow — Serious Mode

Lightweight workflow for code management: who edits, how changes flow, and when to commit and deploy.

---

## 1. Cursor is the only writer

- All edits to this repo are made through **Cursor** (prompts → agent-generated diffs).
- **Every Cursor prompt ends with a commit.** Finish each change cycle by committing (or stashing) so the tree is clean and history is traceable.
- Avoid hand-editing config or scripts outside Cursor so that intent, validation, and history stay in one place.
- If you must edit by hand, keep changes small and run the checks below before deploy.

---

## 2. Standard change flow

1. **Prompt** — Describe the change (e.g. new automation, fix, or doc update).
2. **Diff** — Review the proposed changes; keep diffs minimal and scoped.
3. **Tests / gates** — Run `./scripts/serious_mode_check.sh` (clean tree + optional pytest). Run any phase gates or validation scripts that apply (e.g. RFID gates, HA API checks).
4. **Commit** — Commit when the change is logically complete and checks pass. **Each prompt cycle ends with a commit** (or an explicit stash).
5. **Deploy** — Use `./scripts/manage_ha.sh` with the appropriate flag (e.g. `--config`, `--automations`, `--stage`, `--stage-no-restart`). Deploy only runs when the working tree is **clean** (see deploy guard below). For dashboard-only changes, use `--stage-no-restart` or LIGHT_DEPLOY to avoid HA restart.

---

## 3. Deploy guard

`scripts/manage_ha.sh` **refuses** deploy-affecting actions if the working tree has staged or unstaged changes:

- **Guarded flags:** `--config`, `--automations`, `--scripts`, `--go2rtc`, `--all`, `--appdaemon`, `--stage`, `--stage-no-restart`, `--promote`, `--restart`, `--restart-all`, `--appdaemon-restart`
- **Always allowed (even when dirty):** `--help`, `--check`, `--validate`, `--spoolman-export`, `--spoolman-import`, `--spoolman-update`

If you hit the guard, the script prints `git status --porcelain` and tells you to commit or stash, then run again.

---

## 4. Serious-mode check

Run before committing or deploying:

```bash
./scripts/serious_mode_check.sh
```

It:

- Ensures the working tree is clean (same notion of “dirty” as the deploy guard).
- If a `tests/` directory exists and `pytest` is available, runs `pytest -q`; otherwise prints a warning and continues.
- Prints a short success summary.

Keep it fast and safe; missing pytest does not fail the script.

---

## 5. Recommended commit boundaries

Commit in small, logical chunks. Suggested split:

| Boundary   | What to include |
|-----------|------------------|
| **Tooling** | Scripts, Makefile, CI/gates, `manage_ha.sh`, `serious_mode_check.sh` — no behavior change to HA or addons. |
| **Behavior** | Config that affects HA or addons: `configuration.yaml`, `automations.yaml`, `scripts.yaml`, `appdaemon/apps/*`, dashboards, `go2rtc.yaml`. One logical change per commit when possible. |
| **Docs** | `docs/*` only — runbooks, architecture, workflow. Can be committed alone or with the change they describe. |

Separating tooling, behavior, and docs makes history easier to review and roll back.
