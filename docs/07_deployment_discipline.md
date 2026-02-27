# Deployment Discipline

## Rules

- Dirty tree cannot restart HA
- TEST runs all gates
- ANALYZE never deploys
- DEPLOY enforces restart discipline

---

## Light Deploy

Reload domains when possible.
Restart only when required.

---

## Secrets

All sensitive values stored in:

./scripts/deploy.env.local

This file is not committed.
