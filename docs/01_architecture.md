# System Architecture

## Layered Model

+----------------------+
|   Bambu P1S Printer  |
+----------------------+
           |
           v
+----------------------+
| AMS Units            |
| - ams_1 (slots 1-4)  |
| - ams_128 (slot 5)   |
| - ams_129 (slot 6)   |
+----------------------+
           |
           v
+----------------------+
| Home Assistant       |
| - Sensors            |
| - Helpers            |
| - Automations        |
+----------------------+
           |
           v
+----------------------+
| AppDaemon            |
| - RFID reconciliation|
| - Sticky tray logic  |
| - State machine      |
+----------------------+
           |
           v
+----------------------+
| Spoolman             |
| REST API             |
| /api/v1/openapi.json |
+----------------------+

---

## Separation of Responsibility

Home Assistant:
- Entity storage
- Helpers
- UI
- Service calls

AppDaemon:
- Deterministic reconciliation logic
- Identity decisions
- State transitions

Spoolman:
- Canonical spool inventory
- Filament metadata
- Weight tracking
- RFID UID association

Scripts:
- Deploy gates
- Preflights
- Evidence capture
