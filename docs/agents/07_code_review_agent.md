# Code Review Agent

## Purpose

Runs three sequential adversarial review passes on a diff, then synthesizes findings into a single verdict. Integrates as a mandatory gate in the CHECKIN workflow — any HIGH finding blocks the commit.

## Triggers

| Trigger | Action |
|---------|--------|
| `REVIEW` | Run three-reviewer analysis on staged diff or specified files |
| (auto) | Invoked automatically as step 2 of CHECKIN |

## Usage

```
REVIEW                    # review staged diff (git diff --staged)
REVIEW staged             # same as above
REVIEW path/to/file.py    # review specific file(s)
```

## The Three Reviewers

### Reviewer 1 — THE SKEPTIC

Assumes the code is wrong. Hunts for:

- Guard ordering failures (dedup before snapshot? snapshot before handler? failed-print check before anything else?)
- Falsy traps (empty set, 0, None — is the code explicit enough?)
- Race conditions (what if AppDaemon restarts mid-wait?)
- What happens when Spoolman is down or returns 500?
- What happens on a cancelled print vs a failed print?
- What happens if `_threemf_data` is never populated?
- Off-by-one in slot numbering (slots 1-6, AMS HT is 5-6)
- Write-ahead dedup: job key persisted BEFORE Spoolman writes?
- Rehydration: does new code assume fresh state that won't exist after a restart?

### Reviewer 2 — THE TESTER

Looks only at test coverage. For every code path in the diff:

- Is there a test for the happy path?
- Is there a test for the guarded/skipped case?
- Are mocks hiding real behavior (mock returns success always)?
- Do assertions actually validate the right thing?
- Are edge cases tested: empty input, None, zero, negative values?
- Are new public methods tested?
- Are new guard conditions tested for both branches?
- Tests must use unittest.TestCase — flags pytest-style tests
- No test should depend on real HA or Spoolman connectivity

### Reviewer 3 — THE APPDAEMON EXPERT

Knows the AppDaemon threading model. Looks only for:

- Any `time.sleep()` in a callback or state handler (always HIGH)
- Blocking I/O in event loop (urllib, open(), socket) not wrapped in `run_in` or `run_in_executor`
- Timer handle leaks (`run_in` handle stored but never cancelled)
- `cancel_timer` called without try/except
- State mutation from a non-AppDaemon thread
- Listeners registered outside `initialize()`
- `run_in` callbacks that don't handle None/missing kwargs
- `self.args` access outside `initialize()` without `.get()` fallback
- Any assumption that callbacks fire in order

## Synthesis Rules

| Condition | Action |
|-----------|--------|
| Finding in 1 reviewer | Keep at stated severity |
| Finding in 2 reviewers | Elevate one level (LOW->MEDIUM, MEDIUM->HIGH) |
| Finding in 3 reviewers | Always HIGH regardless of individual severity |
| Identical findings | Merge into one entry with "Flagged by: R1, R2, R3" |

## Filament IQ Domain Rules (v1.0)

Applied by all three reviewers as domain invariants.
Any violation is at minimum MEDIUM severity.

### Architecture invariants

- decide_consumption() must be a pure function with zero I/O.
  Any urllib, open(), socket, or hassapi call inside consumption_engine.py
  is HIGH.
- Phases must not be mixed:
    _collect_print_inputs() → only HA/Spoolman reads, no decisions
    decide_consumption()    → only logic, no I/O
    _execute_writes()       → only Spoolman writes, no HA reads
  Mixing phases is MEDIUM.
- RFID slots must never use threemf_used_g as their consumption value.
  3MF data is input only — RFID delta always wins for RFID spools.
  Any code path where 3MF overrides RFID delta is HIGH.
- Notification must be built from SlotDecision.post_write_remaining,
  never from spools_cache or any pre-write value. Violation is HIGH.

### Write path invariants

- _spoolman_use() must return dict or None (never bool).
- post_write_remaining must come from _spoolman_use() response, not any cache.
- Dedup (_persist_seen_job_keys) must only be called after all writes succeed.
  Write-ahead dedup is HIGH.
- active_print.json must be written atomically (temp file + os.replace).
  Non-atomic writes are MEDIUM.
- Depleted spool (post_write_remaining <= 0) must always receive a location
  PATCH to "Empty", regardless of auto_empty_spools setting.
  Missing depletion location update is MEDIUM.

### Lifecycle invariants

- _on_print_finish() must never call time.sleep(). Any sleep is HIGH.
- 3MF fetch must be scheduled at print START, not polled at print END.
  Any finish-line polling for 3MF data is MEDIUM.
- _finish_wait_tick() must not exist. If found in a diff it is HIGH
  (deleted mechanism reintroduced).
- _handle_usage_event() must not exist. If found in a diff it is HIGH
  (deleted entrypoint reintroduced).
- Job keys must include timestamp suffix.

### Test style invariants (R2 — Tester, in addition to coverage checks)

- Scenario matrices with 6+ similar scenarios must use @pytest.mark.parametrize
  with id= strings. A class with 8+ nearly-identical test methods is MEDIUM.
- Shared setup must use pytest fixtures or module-level helpers (_make_slot etc).
  Duplicated setup boilerplate across 5+ tests is LOW.
- Write assertions must use SpoolmanRecorder, not log string matching.
  Log string matching for write verification in new tests is MEDIUM.
- No test body should exceed ~25 lines including setup and assertions.
  Tests exceeding this are LOW (suggest fixture extraction).
- Each new test file must have a module docstring describing its scope.
  Missing module docstring is LOW.
- test_rfid_slot_uses_rfid_delta_not_3mf must exist in the test suite.
  This is the permanent Bug 13 regression guard. If missing it is HIGH.

## Review Report Format

```
REVIEW REPORT
=============
SCOPE: [files / staged diff]
REVIEWERS: Skeptic (R1), Tester (R2), AppDaemon Expert (R3)
VERDICT: PASS | FAIL

FINDINGS:
+----+----------+--------------+-------------------------+---------------+
| #  | Severity |   Location   |          What           |  Flagged by   |
+----+----------+--------------+-------------------------+---------------+
| 1  | HIGH     | file.py:123  | ...                     | R1, R2        |
+----+----------+--------------+-------------------------+---------------+

[For each finding: what, why it matters, suggested fix]

SUMMARY:
HIGH: N -- must resolve before commit
MEDIUM: N -- warnings
LOW: N -- advisory

VERDICT: PASS (zero HIGH) | FAIL (N HIGH findings)
```

## CHECKIN Integration

When CHECKIN trigger fires:

1. Run `./scripts/serious_mode_check.sh`
2. **Run REVIEW on staged diff** (all three reviewers)
3. If VERDICT is **FAIL** (any HIGH findings):
   - Do NOT commit
   - Output full REVIEW REPORT
   - State: "CHECKIN BLOCKED — resolve HIGH findings then re-run CHECKIN"
   - Wait for user direction
4. If VERDICT is **PASS**:
   - Log MEDIUM/LOW warnings in CHECKIN output table
   - Proceed to step 3: SECURITY scan (see `docs/agents/08_security_agent.md`)
   - Include in CHECKIN output: `REVIEW: PASS (R1+R2+R3) — N findings (M medium, L low)`
