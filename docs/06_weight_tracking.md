# Weight Tracking

There is no physical weight sensor in AMS.

## Two Write Paths (v0.9.0+)

Remaining weight is updated via exactly two paths. The `pool_g` estimation
path was removed in v0.9.0.

### Path 1 — RFID Delta
For RFID spools, the AMS reports `remain` (percentage). On print completion,
the delta between pre-print and post-print `remain` is converted to grams
and written to Spoolman as consumed weight.

### Path 2 — 3MF Match
For non-RFID spools (and as validation for RFID), the 3MF file from the
printer's SD card is fetched via native FTPS (`ftplib.FTP_TLS`), parsed for
per-plate filament usage, and matched to slots via Tier 2.75 slot position
matching. Matched consumption is written to Spoolman.

## Pipeline

```
Print completes → P1S_PRINT_USAGE_READY event
  → ams_print_usage_sync receives event
  → Dedup check: job_key vs seen_job_keys.json (write-ahead)
  → RFID delta: read remain% before/after, convert to grams
  → 3MF fetch: FTPS listing → download latest .3mf → parse plate data
  → 3MF Tier 2.75: match plate filament to slot by position + material
  → PATCH Spoolman used_weight for each matched slot
```

## Smart Empty Guard (v0.9.0+)

Prevents over-decrement on short or interrupted prints. If the computed
consumption would drive remaining weight negative, the guard caps the
write at the current remaining weight.

## Job Dedup

`seen_job_keys.json` persisted under `appdaemon/apps/data/`. Write-ahead:
the job key is recorded before the Spoolman PATCH, so a crash mid-write
will not re-apply the same consumption on restart. The
`_last_processed_job_key` field prevents duplicate processing within the
same AppDaemon session.

## dry_run

`dry_run` is **false** as of v0.9.0 — usage tracking is live. Set
`dry_run: true` in app config to log consumption without writing to Spoolman.

## Initial Onboarding

Initial spool weight requires manual entry in Spoolman. The system only
tracks consumption from prints — it does not know initial weight
automatically.
