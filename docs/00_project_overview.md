# Project Overview

## Purpose

This project implements a deterministic filament identity and lifecycle management system for:

- Bambu P1S
- AMS 2 Pro (4-slot)
- AMS HT expansion units
- Home Assistant
- AppDaemon
- Spoolman

The goal is to create a stable, observable, and deployment-safe system where:

- Filament identity is deterministic
- Spool identity does not drift
- Tray swaps are accurately detected
- RFID and non-RFID spools coexist cleanly
- Deployment changes are controlled and gated

This is not a collection of automations.

This is a stateful system with defined guarantees.

---

## Design Philosophy

- Deterministic > Heuristic
- Identity must be explicit, never inferred
- Spool identity mutates only on real physical change
- State transitions must be explainable
- Deployments must be guarded and reproducible
- Secrets stay local (`./scripts/deploy.env.local`)

---

## v1.0 Consumption Pipeline

The consumption pipeline has five explicit phases:

1. COLLECT   All HA sensor reads and Spoolman queries. Produces List[SlotInput].
2. DECIDE    Pure function, zero I/O. Applies decision tree per slot.
             Produces List[SlotDecision]. Lives in consumption_engine.py.
3. EXECUTE   Spoolman /use writes. Fills post_write_remaining.
             Handles depletion location update.
4. NOTIFY    Builds notification from post-write SlotDecision data.
5. FINALIZE  Writes print_history record, persists dedup,
             schedules RFID reconciler.
