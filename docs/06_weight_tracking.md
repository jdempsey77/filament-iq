# Weight Tracking (v1.0)

There is no physical weight sensor in AMS. All consumption is computed from
software signals and written to Spoolman via the five-phase pipeline.

## Pipeline Overview

```
Print completes → status transition detected
  → _do_finish() orchestrates:
    1. COLLECT   Read HA sensors + Spoolman state → List[SlotInput]
    2. DECIDE    Pure function (consumption_engine.py) → List[SlotDecision]
    3. EXECUTE   Spoolman /use writes, fill post_write_remaining
    4. NOTIFY    Build HA notification from post-write data
    5. FINALIZE  Write print_history/{job_key}.json, persist dedup, schedule reconciler
```

## Decision Methods

The decision engine (`consumption_engine.py`) is a **pure function** — zero I/O.
It receives `List[SlotInput]` and returns `List[SlotDecision]`.

| Method | When | Consumption formula | Confidence |
|--------|------|---------------------|------------|
| `rfid_delta` | RFID spool, start_g and end_g available | `start_g - end_g` | high |
| `rfid_delta_depleted` | RFID spool, tray empty or end_g == 0 | `start_g` (full remaining) | high |
| `3mf` | Non-RFID, 3MF data available, tray not empty | `threemf_used_g` | high or medium |
| `3mf_depleted` | Non-RFID, 3MF data available, tray empty | `max(threemf_used_g, spoolman_remaining)` | medium |
| `depleted_nonrfid` | Non-RFID, no 3MF, tray empty | `spoolman_remaining` | low |
| `no_evidence` | Insufficient data to compute | 0 (no write) | n/a |

## RFID Ground Truth Rule

For RFID spools, the AMS reports fuel gauge readings (`remain` percentage)
converted to grams via `spool_weight`. The RFID delta (`start_g - end_g`) is
the **highest-confidence** signal and always wins when available. 3MF data is
not used for RFID slots — the hardware signal is strictly preferred.

If `start_g` was not captured (missed print start / AppDaemon restart), the
method falls to `no_evidence` with reason `DATA_LOSS`.

## Depletion Handling

When a tray is detected as empty at print end:
- RFID: `rfid_delta_depleted` — consume full `start_g`
- Non-RFID with 3MF: `3mf_depleted` — consume `max(3mf, remaining)`
- Non-RFID without 3MF: `depleted_nonrfid` — consume `spoolman_remaining`

After EXECUTE, if `post_write_remaining <= 0`, the spool location is updated
to `Empty` (EOL). This is handled in the EXECUTE phase, not DECIDE.

## Smart Empty Guard

Prevents over-decrement. If computed consumption exceeds current
`spoolman_remaining`, the write is capped at `spoolman_remaining`. This
ensures remaining weight never goes negative.

## Job Dedup

`seen_job_keys.json` persisted under `appdaemon/apps/data/`. Write-ahead:
the job key is recorded **before** the Spoolman writes, so a crash mid-write
will not re-apply the same consumption on restart. The
`_last_processed_job_key` field prevents duplicate processing within the
same AppDaemon session.

## Print History

Each completed print writes a record to `appdaemon/apps/data/print_history/{job_key}.json`
containing: job_key, timestamp, per-slot decisions (method, consumption_g,
confidence, pre/post remaining), 3MF metadata, and final status. This provides
an auditable trail for every consumption event.

## dry_run

`dry_run` is **false** as of v1.0 — usage tracking is live. Set
`dry_run: true` in app config to log consumption without writing to Spoolman.
All five phases still execute; only the EXECUTE phase skips the actual
Spoolman PATCH calls.

## Initial Onboarding

Initial spool weight requires manual entry in Spoolman. The system only
tracks consumption from prints — it does not know initial weight
automatically.
