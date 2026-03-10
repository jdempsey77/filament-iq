# Monitor Agent

## Purpose

Captures end-to-end telemetry for a single print job — pre/post Spoolman weights, tray states, AppDaemon logs, and print status transitions. Produces a structured artifact that the MONITOR REPORT trigger can analyze.

## Triggers

| Trigger | Action |
|---------|--------|
| `MONITOR` | Run `scripts/monitor_print.sh` to begin capture |
| `MONITOR REPORT` | Analyze the most recent (or specified) monitor artifact |

## Capture Script

**`scripts/monitor_print.sh`**

### What it captures

1. **Pre-print snapshot**: tray states (all 6 slots), Spoolman spool weights, print status
2. **Poll loop**: print status every 10s until terminal state or 180-minute timeout
3. **AppDaemon log tail**: SSH tail of live AppDaemon log throughout the print
4. **Post-print snapshot**: same as pre-print, captured 5s after terminal state

### Terminal states

`finish`, `failed`, `idle`, `offline`, `unknown`, `unavailable`

### Requirements

- `scripts/deploy.env` with: `HOME_ASSISTANT_URL`, `HOME_ASSISTANT_TOKEN`, `SPOOLMAN_URL`, `PRINTER_PREFIX`
- SSH access to HA host (key at `~/.ssh/id_ed25519_ha`, port 2222)

### Artifact output

```
.artifacts/monitor/YYYYMMDD_HHMMSS/
  monitor.json    # Structured capture data
  appd_log.txt    # Raw AppDaemon log lines
```

### monitor.json schema

```json
{
  "monitor_version": "1.0",
  "pre_snapshot": {
    "timestamp": "ISO8601",
    "print_status": "string",
    "tray_states": {"entity": "state", ...},
    "spoolman_weights": {"spool_id": {"remaining_weight": N, "location": "..."}, ...}
  },
  "post_snapshot": { "...same as pre_snapshot..." },
  "poll_log": [{"t": seconds, "status": "string"}, ...],
  "appd_log_lines": N,
  "appd_log_file": "path"
}
```

## MONITOR REPORT Format

When the orchestrator sends `MONITOR REPORT`, analyze the artifact and produce:

```
## Monitor Report — YYYYMMDD_HHMMSS

**Duration**: Xm Ys (N polls)
**Status transitions**: idle → running → finish

### Weight Deltas
| Spool | Pre (g) | Post (g) | Delta (g) | Location |
|-------|---------|----------|-----------|----------|
| 42    | 850.0   | 842.3    | -7.7      | AMS 1-2  |

### Tray State Changes
| Entity | Pre | Post |
|--------|-----|------|
| ...    | PLA | PLA  |

### AppDaemon Log Highlights
- Key log lines (RFID, 3MF match, consumption writes, errors)
- Filtered to filament_iq entries only

### Assessment
- PASS/WARN/FAIL with explanation
- Any anomalies (missing writes, unexpected state changes, weight mismatches)
```
