FILAMENT IQ — ORCHESTRATOR PROMPT
==================================
AGENT TARGET: Analyze Agent
TRIGGER: ANALYZE

INTENT:
Pre-implementation analysis for: [FEATURE NAME]
Read-only — no code changes.

BACKGROUND:
[1-3 sentences describing what the feature does and why]

QUESTIONS:

Q1: HOOK POINT
Where does this feature attach to the existing code?
- What is the exact call site (file + line)?
- What executes immediately before and after?
- Is all required data available at that point?
- Are there timing or ordering constraints?

Q2: REUSABLE HELPERS
What existing methods, helpers, or patterns can be reused?
Search all 5 modules for anything relevant:
- ams_print_usage_sync.py
- ams_rfid_reconcile.py
- ams_rfid_guard.py
- filament_weight_tracker.py
- spoolman_dropdown_sync.py

Q3: ARCHITECTURE OPTIONS
What are the 2-3 realistic options for where this lives?
For each option provide:
- Code locality (does everything needed already exist there?)
- Coupling risk (timing races, cross-app dependencies)
- Testability (harness ready or needs new mocks?)
- Risk (what could go wrong?)
Recommend one option with justification.

Q4: EDGE CASES
What inputs or states could cause incorrect behaviour?
Consider:
- None / missing values from HA entities
- Zero, negative, or out-of-range sensor values
- External call failures (Spoolman, HA, FTPS)
- Mid-print restarts or state rehydration
- dry_run=True behaviour
- Interactions with existing guards

Q5: TEST SURFACE
What test cases will be needed?
List minimum required cases:
- Happy path
- Each skip/guard condition
- Each error/failure path
- Config toggle (enabled/disabled)
- dry_run

Q6: CONFIG KEYS
What new config keys are needed, if any?
For each: name, type, default value, where used.
Check apps.yaml for naming conventions.

Q7: ESTIMATED SCOPE
Rough line count:
- Production code (new lines)
- Test code (new lines)
- Config changes
- Total

CONSTRAINTS:
- Read-only — no code changes
- Reference exact file names and line numbers
- If a question is not applicable, say so explicitly

EXPECTED OUTPUT:
Full ANALYZE REPORT covering all 7 questions.
End with recommended architecture and explicit list of
edge cases the TEST prompt must cover.
NEXT ACTION: present for approval, then TEST prompt.
