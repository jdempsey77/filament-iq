# Orchestrator Agent

## Purpose

The Orchestrator is the top-level router for all Filament IQ triggers. It receives structured prompts, enforces gate rules, routes to sub-agents, and handles inline triggers (CHECKIN, GUARDRAILS, PHASE, ROLLBACK).

The full orchestrator spec lives in `CLAUDE.md` (root of repo). This document covers the CHECKIN flow in detail, including the mandatory code review and security gates.

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
[4] Run SECURITY on staged diff (Security Agent)
    Four lenses: Secrets, Input Validation, Shell Scripts, Data Files
        |
    FAIL (any HIGH)? --> Output SECURITY REPORT
                     --> "CHECKIN BLOCKED — resolve HIGH findings"
                     --> STOP (wait for user direction)
        |
        v
[5] git commit -m "[message]"
        |
        v
[6] Output audit summary:
    - FILES CHANGED: N
    - COMMIT HASH: [hash]
    - REVIEW: PASS (R1+R2+R3) — N findings (M medium, L low)
    - SECURITY: PASS (4 lenses) — N findings (M medium, L low)
    - GATES PASSED: clean tree, REVIEW PASS, SECURITY PASS
    - NEXT ACTION: [typically DEPLOY or TEST]
```

## Gate Dependencies

| Gate | Required by | Enforced how |
|------|-------------|--------------|
| Clean tree | CHECKIN, DEPLOY | `serious_mode_check.sh` |
| REVIEW PASS | CHECKIN | Code Review Agent (3 reviewers) |
| SECURITY PASS | CHECKIN | Security Agent (4 lenses) |
| TEST PASS | DEPLOY | `skill_test.sh` must have passed on current HEAD |

## Routing Table

See `CLAUDE.md` for the full routing table. Key triggers:

| Trigger | Route | Inline? |
|---------|-------|---------|
| CHECKIN | Orchestrator | Yes |
| REVIEW | Code Review Agent | No (but auto-invoked by CHECKIN) |
| SECURITY AUDIT | Security Agent | No (diff mode auto-invoked by CHECKIN) |
| TEST | Test Agent | No |
| DEPLOY | Deploy Agent | No |
| ANALYZE | Analyze Agent | No |
| DASHBOARD | Dashboard Agent | No |

## HA Config Routing

Non-AppDaemon HA configuration tasks (Lovelace YAML, custom cards, template sensors, new automations) route to the **Dashboard Agent** (`docs/agents/09_dashboard_agent.md`). The Dashboard Agent can directly edit dashboard YAML files but only *suggests* configuration.yaml changes for human review.

## Related Docs

- `CLAUDE.md` — Full orchestrator spec, routing table, gate rules
- `docs/agents/07_code_review_agent.md` — Three-reviewer spec, synthesis rules, domain invariants
- `docs/agents/08_security_agent.md` — Four-lens security spec, severity levels, CHECKIN integration
- `docs/agents/09_dashboard_agent.md` — Dashboard Agent spec, entity map, write access rules
