# Dashboard

The FilamentIQ dashboard is provided as YAML in `dashboard/filament_iq.yaml` (~1850 lines).

## Installation

HA dashboards in storage mode cannot be deployed via file copy. Use one of:

1. **Import via UI:** Settings → Dashboards → Add Dashboard → set to YAML mode → paste contents
2. **Raw editor:** Settings → Dashboards → (existing dashboard) → three-dot menu → Raw configuration editor → paste as a view

After importing, replace all occurrences of `YOUR_PRINTER_SERIAL` with your Bambu printer's device serial (e.g. `01p00c5a3101668`).

## Required Custom Cards

Install these via HACS (Frontend):

| Card | HACS Name | Used For |
|------|-----------|----------|
| [Mushroom](https://github.com/piitaya/lovelace-mushroom) | Mushroom | Entity cards, template cards throughout |
| [config-template-card](https://github.com/iantrich/config-template-card) | Config Template Card | Dynamic slot cards with Spoolman data |
| [timer-bar-card](https://github.com/rianadon/timer-bar-card) | Timer Bar Card | Print time remaining bar |
| [card-mod](https://github.com/thomasloven/lovelace-card-mod) | card-mod | Custom styling |
| [WebRTC Camera](https://github.com/AlexxIT/WebRTC) | WebRTC Camera | Printer camera feed |

## Dashboard Sections

### Printer Status & Controls

- Power toggle (requires a smart outlet entity — adjust `switch.officeoutlet01_3dprinter` to match yours)
- Operator status (`sensor.filament_iq_operator_status`)
- Print speed selector (conditional — only during prints on local MQTT)
- Pause / Resume / Cancel buttons (conditional)
- Task name, progress percentage, layer count
- Timer bar showing time remaining (uses `input_datetime.filament_iq_print_start_time` / `_end_time`)

### AMS Filament Slots

- Header card showing unbound slot count (green when all bound, red when slots need binding)
- Per-slot cards (1–6) showing:
  - Bound spool name, material, vendor
  - Color swatch from tray hex
  - Remaining weight
  - Status indicator (OK, UNBOUND, CONFLICT, etc.)
  - Tap actions for spool management

### Camera

- WebRTC camera card for live printer feed

### Spool Management

- Add Filament form (name, material, color, diameter, density → `rest_command` to Spoolman)
- Add Spool form (select filament from dropdown populated by `spoolman_dropdown_sync`, set weight/location → `rest_command` to Spoolman)
- Edit Spool popup (change location, weight)

## Customization

### Changing the smart plug entity

Find `switch.officeoutlet01_3dprinter` in the YAML and replace with your printer's power switch entity, or remove the power card entirely.

### Adding/removing AMS slots

The dashboard has cards for slots 1–6. If your setup uses fewer slots, remove the corresponding slot card sections. Slot cards use `config-template-card` to dynamically render Spoolman spool data.

### Operator status sensor

The dashboard relies on `sensor.filament_iq_operator_status` for conditional card display. This template sensor is defined in the HA package (`filament_iq.yaml`) and derives states like `printing_normally`, `paused_user`, `idle`, etc. from the printer's print status and AMS state.
