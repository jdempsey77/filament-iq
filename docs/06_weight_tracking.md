# Weight Tracking

There is no physical weight sensor in AMS.

Remaining weight is derived from:

- Spoolman total weight
- Recorded usage (written by `ams_print_usage_sync` on P1S_PRINT_USAGE_READY; dedup via `seen_job_keys.json`)
- Sync scripts

Initial onboarding requires manual weighing.

Remaining truth sync script aligns HA -> Spoolman.
