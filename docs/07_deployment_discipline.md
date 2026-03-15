# Deployment Discipline

## Rules

- Dirty tree cannot restart HA
- TEST runs all gates
- ANALYZE never deploys
- DEPLOY enforces restart discipline

---

## Light Deploy

Reload domains when possible.
Restart only when required.

---

## Usage Tracking

v1.0 five-phase pipeline: collect → decide → execute → notify → finalize. Decision engine (consumption_engine.py) is a pure function — no I/O. RFID delta always wins for RFID spools. See docs/06_weight_tracking.md.

---

## AppDaemon restart
- Restart clears **in-memory state**. Job_key dedup for print usage sync is now **persisted** to `appdaemon/apps/data/seen_job_keys.json` so duplicate P1S_PRINT_USAGE_READY events are still deduplicated after restart. Write-ahead dedup ensures crash safety.
- **Startup swap suppression:** For 90 seconds after AppDaemon start, the reconciler sets `input_boolean.appdaemon_startup_suppress_swap` so HA spool-swap detection does not fire on bulk helper updates. Avoids false positives from initial reconcile.

---

## Secrets

All sensitive values stored in:

./scripts/deploy.env.local

This file is not committed.
