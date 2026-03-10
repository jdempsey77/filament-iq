# Security Agent

## Purpose

Scans for credential exposure, input validation gaps, shell script vulnerabilities, and data file integrity issues. Runs in two modes: diff mode (auto in CHECKIN after REVIEW gate) and full audit mode (standalone trigger).

## Triggers

| Trigger | Mode | Action |
|---------|------|--------|
| `SECURITY AUDIT` | Full codebase | Audit all AppDaemon source, scripts, config, .gitignore |
| (auto in CHECKIN) | Diff only | Scan staged diff through all four lenses |

## The Four Security Lenses

### Lens 1 — SECRETS EXPOSURE

Hunts for credentials, tokens, and sensitive values leaking into unintended locations:

- Any string matching HA token patterns in log statements (`self.log()`, `print()`, f-strings containing token/password/key vars)
- `deploy.env` or `deploy.env.local` referenced in Python source (should only appear in shell scripts)
- Hardcoded IPs, hostnames, or ports (use config/args instead)
- `HA_TOKEN`, `SPOOLMAN_URL`, `PRINTER_PREFIX`, SSH keys appearing in:
  - Log output (even at DEBUG level)
  - Artifact files (`.artifacts/`)
  - Git-tracked files (anything not in `.gitignore`)
  - Exception messages or tracebacks
- `.gitignore` coverage: `deploy.env.local`, `*.env`, artifacts dir, `seen_job_keys.json` — verify all sensitive files are excluded
- Shell scripts: confirm secrets sourced from `deploy.env` only, never hardcoded, never echoed to stdout

### Lens 2 — INPUT VALIDATION

Every value entering the system from HA entities or Spoolman must be treated as untrusted:

- **Slot numbers** from HA entity names: must be validated 1-6
- **spool_id** from `input_text.ams_slot_N_spool_id`: must be int-castable, non-negative. What happens if set to "abc" or "-1"?
- **consumption_g** from 3MF parser: must be float, non-negative, within sanity bounds (`min_consumption_g` / `max_consumption_g`)
- **remaining_weight** from Spoolman response: must be float-castable, handle None/missing gracefully (default to 1, not crash)
- **task_name** from HA sensor: used in job_key and FTPS filename matching — must be sanitized (no path traversal, no shell injection)
- **Spoolman response parsing**: every `.get()` must have a safe default, no bare dict access that could KeyError on unexpected response shape
- **FTPS filename** from printer: used in file operations — must validate against path traversal (no `../`, no absolute paths)

### Lens 3 — SHELL SCRIPT SECURITY

Scripts run with SSH access to the HA host and source env files:

- `set -euo pipefail` present in all scripts (fail fast, no unbound vars)
- All variables quoted (`"$VAR"` not `$VAR`) to prevent word splitting
- No `eval`, no unquoted command substitution that could inject
- Input to curl/wget: URLs constructed from config only, never from user input or HA entity values
- SSH commands: no dynamic command construction from untrusted input
- Artifact directory creation: uses `mkdir -p` with fixed paths only
- `monitor_print.sh`: log tail via SSH — verify the remote command is fixed, not constructed from variable input
- `manage_ha.sh`: flag parsing — no injection via flag values
- SKIP behavior: scripts must exit 0 (not 1) when config missing, to avoid breaking gate suite on unconfigured machines

### Lens 4 — DATA FILE INTEGRITY

Persistent state files that could be corrupted or manipulated:

- **seen_job_keys.json**: what happens if file is corrupted/truncated? Is there a try/except around `json.load`? Does corruption cause all prints to be dedup-skipped or does it fail open?
- **.artifacts/ directory**: monitor output files — are paths constructed safely? No user input in artifact filenames.
- **Job keys** written to `seen_job_keys.json`: are they sanitized before write? A malicious `task_name` could inject into the JSON structure if not properly escaped (`json.dumps` handles this, but verify)
- **MAX_SEEN_JOBS cap**: verify the eviction logic doesn't create a window where a job key is evicted and then replayed

## Severity Levels

| Level | Meaning | CHECKIN impact |
|-------|---------|---------------|
| **HIGH** | Blocks CHECKIN | Token in log, hardcoded credential, no validation on spool_id before use, path traversal in FTPS filename, seen_job_keys fails open on corrupt |
| **MEDIUM** | Warning | Missing `.get()` default on Spoolman field, unquoted variable in non-critical script path, artifact path uses variable but bounded |
| **LOW** | Advisory | Debug log includes non-sensitive config values, minor style issue in validation logic |

## SECURITY AUDIT Report Format (full codebase)

```
SECURITY AUDIT REPORT
=====================
SCOPE: Full codebase
DATE: [timestamp]
VERDICT: PASS | FAIL

FINDINGS:
[numbered, severity, location, what, why, suggested fix]

COVERAGE SUMMARY:
Lens 1 (Secrets):   [checked N files, N clean, N findings]
Lens 2 (Input):     [checked N files, N clean, N findings]
Lens 3 (Shell):     [checked N scripts, N clean, N findings]
Lens 4 (Data):      [checked N files, N clean, N findings]

VERDICT: PASS | FAIL
```

## SECURITY Report Format (diff mode)

```
SECURITY REPORT (diff mode)
===========================
SCOPE: staged diff
VERDICT: PASS | FAIL

FINDINGS: [same numbered format as audit]

VERDICT RATIONALE: [one sentence]
```

## CHECKIN Integration

SECURITY runs as step 3 in the CHECKIN workflow, after REVIEW:

1. `serious_mode_check.sh` — clean tree gate
2. REVIEW (Code Review Agent) — code quality gate
3. **SECURITY (diff mode)** — security gate
4. If all pass: `git commit`

Any HIGH finding in SECURITY blocks the commit, same as REVIEW.
