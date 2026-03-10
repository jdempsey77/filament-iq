# Orchestrator Agent

## Purpose

The Orchestrator is the top-level router for all Filament IQ triggers. It receives structured prompts, enforces gate rules, routes to sub-agents, and handles inline triggers (CHECKIN, GUARDRAILS, PHASE, ROLLBACK).

The full orchestrator spec lives in `CLAUDE.md` (root of repo). This document covers the CHECKIN flow in detail, including the mandatory code review gate.

## CHECKIN Flow

```
CHECKIN trigger received
        |
        v
[1] Run ./scripts/serious_mode_check.sh
        |
    FAIL? --> "CHECKIN BLOCKED — dirty tree" --> STOP
        |
        v
[2] Stage files: git add (relevant files, use -f for filament_iq/)
        |
        v
[3] Run REVIEW on staged diff (Code Review Agent)
    Three reviewers: Skeptic (R1), Tester (R2), AppDaemon Expert (R3)
        |
    FAIL (any HIGH)? --> Output REVIEW REPORT
                     --> "CHECKIN BLOCKED — resolve HIGH findings"
                     --> STOP (wait for user direction)
        |
        v
    PASS (zero HIGH)
        |
        v
[4] git commit -m "[message]"
        |
        v
[5] Output audit summary:
    - FILES CHANGED: N
    - COMMIT HASH: [hash]
    - REVIEW: PASS (R1+R2+R3) — N findings (M medium, L low)
    - GATES PASSED: clean tree, REVIEW PASS
    - NEXT ACTION: [typically DEPLOY or TEST]
```

## Gate Dependencies

| Gate | Required by | Enforced how |
|------|-------------|--------------|
| Clean tree | CHECKIN, DEPLOY | `serious_mode_check.sh` |
| REVIEW PASS | CHECKIN | Code Review Agent (3 reviewers) |
| TEST PASS | DEPLOY | `skill_test.sh` must have passed on current HEAD |

## Routing Table

See `CLAUDE.md` for the full routing table. Key triggers:

| Trigger | Route | Inline? |
|---------|-------|---------|
| CHECKIN | Orchestrator | Yes |
| REVIEW | Code Review Agent | No (but auto-invoked by CHECKIN) |
| TEST | Test Agent | No |
| DEPLOY | Deploy Agent | No |
| ANALYZE | Analyze Agent | No |

## Related Docs

- `CLAUDE.md` — Full orchestrator spec, routing table, gate rules
- `docs/agents/07_code_review_agent.md` — Three-reviewer spec, synthesis rules, domain invariants
