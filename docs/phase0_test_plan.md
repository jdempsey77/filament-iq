# Phase 0 Test Plan — Commands + What to Validate

Phase 0 only. No Phase 1+ logic. Documentation: commands and validations only.

---

## 1) Preconditions / Environment

**Required env for baseline and hard gate:**

- `HOME_ASSISTANT_URL`
- `HOME_ASSISTANT_TOKEN`
- `SPOOLMAN_URL`

**test_phase0_freeze** requires **NONE** of these. It runs offline with no HA/Spoolman.

**RFID writes** are frozen unless `AMS_ALLOW_RFID_WRITES=1` is set.

---

## 2) Test A — Freeze Gate Self-Test (OFFLINE)

**Command:**

```bash
make test_phase0_freeze
```

**Validate:**

- The output contains **EXACTLY**:
  ```
  SPOOLMAN_WRITE_REFUSED: AMS_ALLOW_RFID_WRITES must be 1 to write RFID identity.
  ```
- The make target exits success (exit code 0).

**Troubleshooting:**

- If it fails, the freeze gate is not guaranteed and Phase 0 is **NOT** complete.

---

## 3) Test B — Baseline Snapshot (ONLINE)

**Commands:**

```bash
export HOME_ASSISTANT_URL="http://<ha_host>:8123"
export HOME_ASSISTANT_TOKEN="..."
export SPOOLMAN_URL="http://<spoolman_host>:7912"
make gates_phase0_baseline
```

**Validate:**

- It prints a snapshot directory path under:
  ```
  snapshots/phase0_baseline/<UTC timestamp>/
  ```
- The directory contains:
  - `ams_trays.json`
  - `spoolman_rfid_spools.json`
  - `duplicate_rfid_report.json`
  - `new_location_with_rfid.json`
  - `summary.txt`
- **summary.txt** counts match the JSON lengths:
  - AMS trays captured == `jq 'length' ams_trays.json`
  - RFID spools count == `jq 'length' spoolman_rfid_spools.json`
  - Duplicate tag count == `jq 'length' duplicate_rfid_report.json`
  - "New" with RFID count == `jq 'length' new_location_with_rfid.json`

**Helper commands:**

```bash
ls -la <snapshot_dir>
jq 'length' <snapshot_dir>/ams_trays.json
jq 'length' <snapshot_dir>/spoolman_rfid_spools.json
jq 'length' <snapshot_dir>/duplicate_rfid_report.json
jq 'length' <snapshot_dir>/new_location_with_rfid.json
```

---

## 4) Test C — Hard Integrity Gate (ONLINE, MUST PASS FOR PHASE 0 COMPLETE)

**Command:**

```bash
make gates_phase0_hard
```

**Validate:**

- It **always** prints:
  - `snapshot path=<path>`
  - `dup_count=<n>`
  - `new_with_rfid_count=<n>`
- For Phase 0 completion it **must** print:
  - `PASS: PHASE 0 BASELINE CLEAN`
  - with `dup_count=0` and `new_with_rfid_count=0`
- The make target exits success (exit code 0).

---

## 5) Failure Interpretation

| Exit code | Meaning |
|-----------|--------|
| **10** | Duplicates exist (`duplicate_rfid_report.json` length > 0) |
| **11** | RFID spools exist in location "New" (`new_location_with_rfid.json` length > 0) |
| **12** | Snapshot failure (snapshot script exited non-zero) |

**What to inspect:**

- **Exit 10:** Open `duplicate_rfid_report.json` and identify the conflicting `spool_ids` and tag.
- **Exit 11:** Open `new_location_with_rfid.json` and identify spool ids to move out of "New".
- **Exit 12:** Rerun `make gates_phase0_baseline` and check HA/Spoolman connectivity and tray entity IDs.

---

## 6) Completion Criteria

Phase 0 is **DONE** only if:

- `make test_phase0_freeze` passes.
- `make gates_phase0_hard` passes with `dup_count=0` and `new_with_rfid_count=0`.
