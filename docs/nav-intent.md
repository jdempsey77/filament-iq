# Nav Intent — External Navigation API

The filament-iq-manager card supports external navigation via a Home Assistant
`input_text` helper entity. This lets other dashboard elements (slot button-cards,
automations, scripts) deep-link into a specific spool's edit panel without the
user having to find it manually.

---

## Entity API

| Entity | Type | Purpose |
|--------|------|---------|
| `input_text.filament_iq_nav_intent` | `input_text` (max 64) | Write a nav payload here before the card mounts |

---

## Payload Format

```
type:value
```

| Payload | Behavior |
|---------|----------|
| `spool:N` | Pre-opens the edit panel for Spoolman spool #N |
| `spool:` | No-op (empty spool ID — card opens normally) |
| `spool:0` | No-op (ID 0 is not a valid Spoolman spool) |
| Anything else | No-op (card opens normally, no error) |

Reserved for future use: `slot:N`, `action:add`.

---

## Setup

### 1. Add helper to `configuration.yaml`

```yaml
input_text:
  filament_iq_nav_intent:
    name: Filament IQ Nav Intent
    max: 64
    icon: mdi:navigation
```

### 2. Create the slot-tap script in `scripts.yaml`

```yaml
slot_tap_to_filament_iq:
  alias: "Slot Tap → Filament IQ"
  fields:
    spool_id:
      description: Spoolman spool ID of the spool currently bound to the slot
      example: "42"
  sequence:
    - action: input_text.set_value
      target:
        entity_id: input_text.filament_iq_nav_intent
      data:
        value: "spool:{{ spool_id }}"
    - action: input_select.select_option
      target:
        entity_id: input_select.printer_view_tab
      data:
        option: Filament IQ
  mode: single
```

### 3. Update slot button-card `tap_action`

```yaml
tap_action:
  action: call-service
  service: script.slot_tap_to_filament_iq
  service_data:
    spool_id: >-
      [[[ return states['input_text.ams_slot_1_spool_id']?.state || ''; ]]]
```

---

## How It Works

1. User taps a slot card
2. Script writes `"spool:N"` to `input_text.filament_iq_nav_intent`
3. Script switches dashboard tab to Filament IQ (triggering the card to mount/remount)
4. On mount, `main.jsx` reads the intent from `hass.states` and passes it as `navIntent` prop
5. `SpoolsTab` parses the intent, clears the entity, and calls `setEditId(N)`
6. The matching spool row expands its edit panel

---

## Limitations

- **Fires at mount only.** The intent is read once when the card first renders.
  If the card stays mounted (the user is already on the Filament IQ tab when
  another slot is tapped), the new intent will not be processed until a full
  remount. This is by design — it avoids complex subscription state.

- **Last intent wins** on rapid consecutive taps (the script is `mode: single`,
  so the second tap will be queued or dropped depending on HA behavior).

- **Unknown spool ID** — if the spool ID does not exist in Spoolman, the card
  opens normally with no row expanded and no error.

---

## Future Architecture

The target architecture for reliable in-session navigation is a WebSocket event
subscription (`hass.connection.subscribeEvents`) rather than reading from an
entity state. This would allow the card to respond to nav intents even when
already mounted. Tracked in BACKLOG.md.
