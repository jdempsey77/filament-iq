# LABELS.md — Filament IQ Label Printing

Spec and implementation notes for the Brother QL label printing feature.

---

## Hardware

| Item | Detail |
|---|---|
| Printer | Brother QL-810W (WiFi only, no Ethernet/BT) |
| Label stock | Brother DK-1218, 24mm round die-cut, white, 1000/roll |
| Connectivity | WiFi — assign static DHCP lease in UniFi UDM Pro |
| Python library | `brother_ql` (PyPI: `brother_ql`) |
| Label ID in brother_ql | `d24` (236 × 236 px printable area) |

**Printer identifier format:**
```
tcp://192.168.x.x:9100
```
Assign a static DHCP lease in UniFi once the printer is on the network. Update this file with the final IP.

**VLAN note:** Printer must be on the same VLAN as HA (`192.168.4.x` IoT) or a firewall rule must allow TCP 9100 from HA to the printer IP.

---

## Label Design

Canvas: 236 × 236 px (d24 round die-cut)

Contents (all fitting in 1-inch circle):
- Background fill = filament hex color
- Line 1 (large): material type (e.g. `PETG`)
- Line 2 (medium): vendor name abbreviated (e.g. `SUNLU`)
- Line 3 (small): color name (e.g. `Matte Black`)

Text color: white on dark backgrounds, dark on light backgrounds (auto-contrast).

---

## Architecture

```
Lovelace card (Preact)
  → checkbox: "Print label & move to shelf" (Add Spool dialog)
  → button: "Print Label" (Edit Spool inline panel)
      |
      v
hass.connection.sendMessage({ type: 'fire_event' })
  (direct WebSocket — NOT via filament_iq_proxy)
      |
      v
HA event: filament_iq_print_label
  payload: { spool_id: int }
      |
      v
AppDaemon: label_printer.py
  → GET spool from Spoolman API
  → GET filament from Spoolman API (for color hex, material, vendor)
  → Generate 236x236 PNG with Pillow
  → Send to QL-810W via brother_ql (tcp://192.168.x.x:9100)
  → PATCH spool location: "New" → "Shelf"
  → Fire HA event: filament_iq_label_result
      payload: { spool_id: int, success: bool, error: str|null }
      |
      v
Lovelace card (subscribeEvents)
  → toast: "Label printed — spool moved to shelf" or "Print failed: <error>"
  → 15s timeout if no result event received
```

**Note:** The card fires the print event via `hass.connection.sendMessage`
directly — same pattern as `FILAMENT_IQ_SLOT_ASSIGNED`. The
`filament_iq_proxy` component is NOT involved in the print trigger path.
This works identically local and via Nabu Casa — the HA WebSocket is
tunneled by Nabu Casa transparently. AppDaemon handles the printer
connection server-side; the browser never touches the printer directly.

---

## AppDaemon App: `label_printer.py`

**Location:** `appdaemon/apps/filament_iq/label_printer.py`

**Trigger:** listens for HA event `filament_iq_print_label`

**Dependencies:**
```
brother_ql
Pillow
```

Install in AppDaemon addon:
```yaml
# appdaemon/apps/apps.yaml — label_printer entry
label_printer:
  module: filament_iq.label_printer
  class: LabelPrinter
  spoolman_url: "http://192.168.4.124:7912"
  printer_url: "tcp://192.168.x.x:9100"
  printer_model: "QL-810W"
  label_size: "d24"
```

**Key methods:**
- `on_print_label_event(event_name, data, kwargs)` — event handler
- `fetch_spool(spool_id)` → spool + filament data from Spoolman
- `generate_label_image(spool_data)` → PIL Image (236x236 PNG)
- `send_to_printer(image)` → brother_ql send, raises on failure
- `update_spool_location(spool_id)` → PATCH location New → Shelf
- `fire_result_event(spool_id, success, error)` → HA event back to card

---

## Lovelace Card Changes

**File:** `packages/lovelace-card/src/`

### Add Spool dialog
- Add checkbox: `☑ Print label & move to shelf after saving` (default: ON)
- On save: if checked, after POST spool succeeds → call proxy to fire
  `filament_iq_print_label` with new spool_id
- Show loading spinner on print button while waiting for result event
- Toast on `filament_iq_label_result`: "Label printed — spool moved to shelf"
  or "Label print failed: <error>"

### Edit Spool inline panel
- Add button: `[🖨 Print Label]` at bottom of edit panel
- Same proxy call + result event pattern as above
- Does NOT auto-move to shelf (location already set on existing spools)
  — location move is optional/separate action on edit

---

## Event Pattern (actual implementation)

The card fires print events directly via the HA WebSocket connection
(NOT via filament_iq_proxy — same pattern as FILAMENT_IQ_SLOT_ASSIGNED):

```javascript
// Fire print event
hass.connection.sendMessage({
  type: 'fire_event',
  event_type: 'filament_iq_print_label',
  event_data: { spool_id: spoolId },
})

// Subscribe to result event
hass.connection.subscribeEvents(
  (event) => handleResult(event.data),
  'filament_iq_label_result'
)
```

---

## Error Handling

| Failure | Behavior |
|---|---|
| Printer offline | `brother_ql` raises — caught, fires result event with error |
| Out of labels | Printer returns error state — caught, fires result event |
| Spoolman fetch fails | Log + fire result event, do not attempt print |
| Location PATCH fails | Log warning — label already printed, don't block |

Location PATCH failure is non-fatal: label printed is more important than
the location update. User can manually update location in the card.

---

## Phase Checklist

### P_LABELS — Label Printing Feature

- [x] `label_printer.py` implemented + unit tests (dry_run mode)
- [x] `apps.yaml` updated with label_printer entry (dry_run: true)
- [x] Card: Add Spool dialog checkbox added (v1.1.0)
- [x] Card: Edit Spool print button added (v1.1.0)
- [x] Card: result event subscription + toast notification (v1.1.0)
- [x] Card fires event via hass.connection.sendMessage (no proxy needed)
- [ ] `pip install brother_ql Pillow` verified in AppDaemon addon environment
- [ ] Printer on network, static DHCP lease assigned, IP confirmed
- [ ] `apps.yaml` printer_url updated with real IP, dry_run: false
- [ ] End-to-end test: add spool → label prints → location moves to Shelf
- [ ] End-to-end test: edit spool → print button → label prints
- [ ] Remote test via Nabu Casa
- [ ] Error test: printer offline → toast shows error message
- [ ] BACKLOG.md updated, LABELS.md printer IP filled in

---

## Open Questions / Decisions Deferred

- [ ] Confirm printer VLAN placement (IoT `192.168.4.x` recommended)
- [ ] Confirm static DHCP IP — update `printer_url` in apps.yaml and this file
- [ ] Auto-contrast text color threshold (suggest: luminance < 0.4 → white text)
- [ ] Whether Edit Spool print button should also offer location move
- [ ] Third-party DK-1218 compatible label rolls — verify with brother_ql before ordering in bulk
