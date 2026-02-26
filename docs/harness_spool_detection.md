# Spool Detection Harness

Repeatable, timestamped evidence capture and evaluation for **RFID** and **non-RFID** spool detection per AMS slot. Used for debugging and regression checks only (not deployment).

## Environment

Source before running (scripts do this automatically when present):

- `scripts/deploy.env.local` (override)
- `scripts/deploy.env`

Required variables:

| Variable | Purpose |
|----------|--------|
| `HOME_ASSISTANT_URL` | Base URL for HA (e.g. `http://192.168.1.10:8123`) |
| `HOME_ASSISTANT_TOKEN` | Long-lived access token for HA API |
| `SPOOLMAN_URL` or `SPOOLMAN_BASE_URL` | Spoolman base URL (e.g. `http://192.168.1.10:7912`) |

Optional:

- `SPOOLMAN_TOKEN` – used only if the harness is extended to send auth headers (not required for read-only capture).
- `HARNESS_WAIT_SECONDS` or `--wait-seconds` – stabilization wait between baseline and after capture (default 20).

## Scripts

| Script | Purpose |
|--------|--------|
| `scripts/harness_spool_detection_capture.sh` | Single snapshot for one slot → JSON file |
| `scripts/harness_spool_detection_run.sh` | Full run: baseline → prompt → wait → after → eval |
| `scripts/harness_spool_detection_eval.py` | Compare baseline vs after and pass/fail by mode |

## Running

### RFID baseline

1. Ensure slot is empty (or known state).
2. Run:
   ```bash
   ./scripts/harness_spool_detection_run.sh --slot 1 --mode rfid --label my_rfid_test
   ```
3. When prompted, insert the RFID spool into the given slot and leave it for the stabilization window (default 20s).
4. Script captures baseline, waits, captures after, then runs eval.

### Non-RFID baseline

Same as above with `--mode nonrfid`:

```bash
./scripts/harness_spool_detection_run.sh --slot 2 --mode nonrfid --label my_nonrfid_test
```

### Single capture only

To capture a snapshot without the full run:

```bash
./scripts/harness_spool_detection_capture.sh --slot 3 --out /tmp/slot3.json
```

### Custom wait

```bash
./scripts/harness_spool_detection_run.sh --slot 1 --mode rfid --label quick --wait-seconds 10
```

## Artifacts

Output directory:

```
artifacts/harness_spool_detection/<YYYYMMDD_HHMMSS>_<label>/
├── baseline.json   # Snapshot before insert
└── after.json      # Snapshot after stabilization
```

Override root with:

```bash
ARTIFACT_ROOT=/path/to/artifacts ./scripts/harness_spool_detection_run.sh ...
```

## Interpreting output

- **PASS** – Exit code 0; all requirements for the chosen mode are satisfied.
- **FAIL** – Exit code 1; eval prints reasons (e.g. RFID not detected, tray_signature not set, Spoolman location mismatch).

Eval summary lines:

- **RFID detected** – `tag_uid` or `tray_uuid` present in after tray entity/derived.
- **tray_signature set** – Helper `input_text.ams_slot_N_tray_signature` non-empty in after.
- **bound** – `helper_spool_id_int` > 0 in after.
- **spoolman_reflects_location** – Spoolman spool (by helper ID) has `location` equal to expected (e.g. `AMS1_Slot1`).

### RFID mode rules

- Requires RFID detected and tray_signature set.
- If bound: requires Spoolman location to match expected.
- If not bound: requires either no Spoolman match by tag_uid, or matching spool in location `"New"` (blocked).

### Non-RFID mode rules

- Requires no tag_uid/tray_uuid in after.
- If bound: requires Spoolman location to match expected.

## JSON snapshot shape (capture)

Each snapshot JSON contains:

- `timestamp_utc` – ISO UTC time.
- `slot` – 1–6.
- `ha` – `tray_entity_state`, `helper_spool_id`, `helper_tray_signature` (full HA state or `{ status_code, body }` on error).
- `derived` – `tag_uid`, `tray_uuid`, `helper_spool_id_int`, `expected_spoolman_location`.
- `spoolman` – `by_helper_id` (spool object or status+body), `by_tag_uid` (`matching_spool`, `match_count`).

Tray entities and expected locations follow the same convention as `appdaemon/apps/ams_rfid_reconcile.py` (slots 1–4: AMS1_Slot1–4; 5–6: AMS128_Slot1, AMS129_Slot1).
