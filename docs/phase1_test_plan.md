# Phase 1 Match Resolution — Test Plan (Read-Only)

Phase 1 is read-only: no writes to Spoolman or HA.

## Prerequisites

- `HOME_ASSISTANT_URL`, `HOME_ASSISTANT_TOKEN`, `SPOOLMAN_URL` (e.g. from `source ./scripts/deploy.env`).

## Commands

```bash
source ./scripts/deploy.env
export HOME_ASSISTANT_URL HOME_ASSISTANT_TOKEN SPOOLMAN_URL
make phase1_snapshot    # prints SNAPSHOT_DIR=<path>
make gates_phase1_match # gate: fails if AMBIGUOUS_DUPLICATES or SPOOL_IN_NEW
```

## Expected output (healthy system)

**make gates_phase1_match:**

```
snapshot path=<path>
ambiguous_count=0
in_new_count=0
PASS: PHASE 1 MATCH CLEAN
```

Exit code: 0.

## Snapshot artifacts

Under `snapshots/phase1_match/<UTC timestamp>/`:

- `ams_trays.json` — tray state (same structure as Phase 0)
- `spoolman_rfid_spools.json` — normalized RFID spools (jq lib)
- `match_results.json` — per-slot: resolved_spool_id, resolution_status, evidence
- `summary.txt` — counts per status + per-slot one-liners

## Exit codes (gates_phase1_match)

| Code | Meaning |
|------|--------|
| 0 | Clean |
| 20 | Ambiguous duplicates present |
| 21 | Spool in location New present |
| 22 | Phase 1 snapshot failure or missing env |

## Resolution statuses

- `TAG_EMPTY` — tray tag normalizes to empty
- `NO_MATCH` — no spool with matching normalized tag
- `AMBIGUOUS_DUPLICATES` — multiple spools share the tag
- `SPOOL_IN_NEW` — single match but spool location is "New"
- `RESOLVED_UNIQUE` — exactly one match, not in New
