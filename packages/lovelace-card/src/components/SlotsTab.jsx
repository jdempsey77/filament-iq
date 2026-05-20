import { h } from 'preact'
import { useState } from 'preact/hooks'

// ── MDI SVG paths ────────────────────────────────────────────
const ICONS = {
  printer:      'M6,2A2,2 0 0,0 4,4V10A2,2 0 0,0 6,12H10A2,2 0 0,0 12,10V4A2,2 0 0,0 10,2H6M6,4H10V10H6V4M4,14A2,2 0 0,0 2,16V22H22V16A2,2 0 0,0 20,14H4M6,16H18V20H6V16Z',
  nozzle:       'M7,2H17V8H19V13H16.5L13,17H11L7.5,13H5V8H7V2M10,22H2V20H10A1,1 0 0,0 11,19V18H13V19A3,3 0 0,1 10,22Z',
  fan:          'M12,11A1,1 0 0,0 11,12A1,1 0 0,0 12,13A1,1 0 0,0 13,12A1,1 0 0,0 12,11M12.5,2C17,2 17.11,5.57 14.75,6.75C13.76,7.24 13.32,8.29 13.13,9.22C13.61,9.42 14.03,9.73 14.35,10.13C18.05,8.13 22.03,8.92 22.03,12.5C22.03,17 18.46,17.1 17.28,14.73C16.78,13.74 15.72,13.3 14.79,13.11C14.59,13.59 14.28,14 13.88,14.34C15.87,18.03 15.08,22 11.5,22C7,22 6.91,18.42 9.27,17.24C10.25,16.75 10.69,15.71 10.89,14.79C10.4,14.59 9.97,14.27 9.65,13.87C5.96,15.85 2,15.07 2,11.5C2,7 5.56,6.89 6.74,9.26C7.24,10.25 8.29,10.68 9.22,10.87C9.41,10.39 9.73,9.97 10.14,9.65C8.15,5.96 8.94,2 12.5,2Z',
  link:         'M10.59,13.41C11,13.8 11,14.44 10.59,14.83C10.2,15.22 9.56,15.22 9.17,14.83C7.22,12.88 7.22,9.71 9.17,7.76V7.76L12.76,4.17C14.71,2.22 17.88,2.22 19.83,4.17C21.78,6.12 21.78,9.29 19.83,11.24L18.07,13C18.07,11.96 17.89,10.92 17.51,9.94L18.42,9C19.59,7.85 19.59,5.96 18.42,4.79C17.25,3.62 15.36,3.62 14.19,4.79L10.59,8.38C9.42,9.55 9.42,11.44 10.59,12.61L10.59,13.41M13.41,10.59C13.8,10.2 14.44,10.2 14.83,10.59C16.78,12.54 16.78,15.71 14.83,17.66V17.66L11.24,21.25C9.29,23.2 6.12,23.2 4.17,21.25C2.22,19.3 2.22,16.13 4.17,14.18L5.93,12.46C5.93,13.5 6.11,14.54 6.49,15.52L5.58,16.43C4.41,17.6 4.41,19.49 5.58,20.66C6.75,21.83 8.64,21.83 9.81,20.66L13.41,17.07C14.58,15.9 14.58,14.01 13.41,12.84C13,12.45 13,11.81 13.41,11.42L13.41,10.59Z',
  chevron:      'M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z',
  alert:        'M13,14H11V10H13M13,18H11V16H13M1,21H23L12,2L1,21Z',
  cancel:       'M12,2A10,10 0 0,1 22,12A10,10 0 0,1 12,22A10,10 0 0,1 2,12A10,10 0 0,1 12,2M12,4A8,8 0 0,0 4,12C4,13.85 4.57,15.55 5.54,16.95L16.95,5.54C15.55,4.57 13.85,4 12,4M12,20A8,8 0 0,0 20,12C20,10.15 19.43,8.45 18.46,7.05L7.05,18.46C8.45,19.43 10.15,20 12,20Z',
  externalLink: 'M14,3V5H17.59L7.76,14.83L9.17,16.24L19,6.41V10H21V3M19,19H5V5H12V3H5C3.89,3 3,3.9 3,5V19A2,2 0 0,0 5,21H19A2,2 0 0,0 21,19V12H19V19Z',
}

function Icon({ path, size = 16, color = 'currentColor', style: extraStyle = {} }) {
  return h('svg', {
    viewBox: '0 0 24 24',
    style: { width: size, height: size, fill: color, flexShrink: 0, ...extraStyle },
  }, h('path', { d: path }))
}

// ── AMS unit definitions ─────────────────────────────────────
const AMS_UNITS = [
  {
    name: 'AMS 2 Pro',
    humEntity:     'sensor.p1s_01p00c5a3101668_ams_1_humidity',
    tempEntity:    'sensor.p1s_01p00c5a3101668_ams_1_temperature',
    dryEntity:     'binary_sensor.p1s_01p00c5a3101668_ams_1_drying',
    dryTimeEntity: 'sensor.p1s_01p00c5a3101668_ams_1_remaining_drying_time',
    slots: [1, 2, 3, 4],
  },
  {
    name: 'AMS HT 1',
    humEntity:     'sensor.p1s_01p00c5a3101668_ams_128_humidity',
    tempEntity:    'sensor.p1s_01p00c5a3101668_ams_128_temperature',
    dryEntity:     'binary_sensor.p1s_01p00c5a3101668_ams_128_drying',
    dryTimeEntity: 'sensor.p1s_01p00c5a3101668_ams_128_remaining_drying_time',
    slots: [5],
  },
  {
    name: 'AMS HT 2',
    humEntity:     'sensor.p1s_01p00c5a3101668_ams_129_humidity',
    tempEntity:    'sensor.p1s_01p00c5a3101668_ams_129_temperature',
    dryEntity:     'binary_sensor.p1s_01p00c5a3101668_ams_129_drying',
    dryTimeEntity: 'sensor.p1s_01p00c5a3101668_ams_129_remaining_drying_time',
    slots: [6],
  },
  {
    name: 'AMS HT 3',
    humEntity:     'sensor.p1s_01p00c5a3101668_ams_130_humidity',
    tempEntity:    'sensor.p1s_01p00c5a3101668_ams_130_temperature',
    dryEntity:     'binary_sensor.p1s_01p00c5a3101668_ams_130_drying',
    dryTimeEntity: 'sensor.p1s_01p00c5a3101668_ams_130_remaining_drying_time',
    slots: [7],
    external: true,
  },
]

const SLOT_AMS = {
  1: { ams: 0,   tray: 0 },
  2: { ams: 0,   tray: 1 },
  3: { ams: 0,   tray: 2 },
  4: { ams: 0,   tray: 3 },
  5: { ams: 128, tray: 0 },
  6: { ams: 129, tray: 0 },
  7: { ams: 130, tray: 0 },
  8: { ams: 255, tray: 0 },
}

const primaryLabel = d => {
  if (d.status === 'empty') return 'Empty'
  const r = d.reason
  if (r.includes('RFID_NOT_REFRESHED')) return 'Reload Spool'
  if (d.status === 'needs_bind') {
    if (r.includes('NONRFID_NO_MATCH'))                return 'No Match Found'
    if (r.includes('AMBIGUOUS'))                       return 'Multiple Matches'
    if (r.includes('GENERIC') || r.includes('LOW_CONFIDENCE')) return 'Too Generic'
    if (r.includes('NOT_FOUND'))                       return 'Spool Missing'
    if (r.includes('UID_NO_MATCH'))                    return 'RFID Not Recognized'
    return 'Needs Binding'
  }
  return `${d.vendor} · ${d.material}`
}

// ── Styles ───────────────────────────────────────────────────
const S = {
  fiqCard:      { background: '#2c2c2e', border: '1px solid #3a3a3c', borderRadius: 11, overflow: 'hidden', marginBottom: 8 },
  unitHeader:   { padding: '7px 11px', borderBottom: '1px solid rgba(255,255,255,0.06)', display: 'flex', alignItems: 'center', gap: 7 },
  unitName:     { fontSize: 11, fontWeight: 500, color: '#e8e8ea' },
  unitSub:      { fontSize: 9, color: '#555' },
  slotRow:      { padding: '8px 11px', borderBottom: '1px solid rgba(255,255,255,0.04)', display: 'flex', alignItems: 'center', gap: 8, position: 'relative', cursor: 'pointer' },
  slotDot:      { width: 28, height: 28, borderRadius: '50%', flexShrink: 0 },
  slotPrimary:  { fontSize: 11, fontWeight: 500, color: '#e8e8ea' },
  slotSecondary:{ fontSize: 10, color: '#555', marginTop: 1 },
  slotBar:      { position: 'absolute', bottom: 0, left: 0, right: 0, height: 2, background: 'rgba(255,255,255,0.05)' },
  slotBarFill:  { height: 2, background: 'rgba(255,255,255,0.4)' },
  dryingRow:    { padding: '8px 11px', borderTop: '1px solid rgba(100,180,220,0.20)', display: 'flex', alignItems: 'center', gap: 8, background: 'rgba(80,160,210,0.08)', borderRadius: '0 0 11px 11px' },
  dryingPrimary:{ fontSize: 11, fontWeight: 500, color: '#64b4dc' },
  dryingSub:    { fontSize: 10, color: '#4a8aaa', marginTop: 2 },
  popupOverlay: { position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', zIndex: 9999 },
  popupSheet:   { background: '#1c1c1e', borderRadius: '16px 16px 0 0', borderTop: '1px solid #3a3a3c', maxHeight: '85vh', overflowY: 'auto' },
  popupDrag:    { width: 36, height: 4, background: '#3a3a3c', borderRadius: 2, margin: '10px auto 0' },
  popupHeader:  { padding: '14px 16px 10px', borderBottom: '1px solid rgba(255,255,255,0.06)' },
  popupUnit:    { fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', color: '#555', marginBottom: 3 },
  popupTitle:   { fontSize: 16, fontWeight: 500, color: '#f5f5f5' },
  popupSub:     { fontSize: 11, color: '#555', marginTop: 2 },
  currentSpool: { padding: '12px 16px', borderBottom: '1px solid rgba(255,255,255,0.06)', display: 'flex', alignItems: 'center', gap: 12 },
  csDot:        { width: 44, height: 44, borderRadius: '50%', flexShrink: 0, border: '2px solid rgba(255,255,255,0.2)' },
  csName:       { fontSize: 13, fontWeight: 500, color: '#e8e8ea' },
  csMeta:       { fontSize: 11, color: '#555', marginTop: 3 },
  csWbar:       { height: 3, background: '#2c2c2e', borderRadius: 2, marginTop: 6, width: 52 },
  csWfill:      { height: 3, borderRadius: 2, background: '#aaa' },
  csPct:        { fontSize: 15, fontWeight: 500, color: '#aaa' },
  csG:          { fontSize: 10, color: '#555', marginTop: 2 },
  popupSec:     { fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#444', padding: '10px 16px 6px' },
  pickerList:   { maxHeight: 360, overflowY: 'auto', overflowX: 'hidden', WebkitOverflowScrolling: 'touch', margin: '0 0 8px 0', borderTop: '1px solid rgba(255,255,255,0.06)' },
  pickerRow:    { padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 8, borderBottom: '1px solid rgba(255,255,255,0.04)', cursor: 'pointer' },
  pickerRowSelected: { background: 'rgba(106,171,218,0.08)' },
  pickerLabel:  { fontSize: 12, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 },
  assignBtn:    { margin: '4px 16px 16px', background: 'rgba(106,171,218,0.12)', border: '1px solid rgba(106,171,218,0.3)', borderRadius: 10, padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 9, cursor: 'pointer' },
  assignLabel:  { fontSize: 13, color: '#6aabda', fontWeight: 500 },
}

// ── Shared helpers ────────────────────────────────────────────
const weightPct = d => {
  const g = parseFloat(d.g) || 0
  return Math.min(100, Math.max(0, Math.round(g / 1000 * 100)))
}

// ── SlotRow — horizontal layout used by all sections ─────────
function SlotRow({ n, data, onPopup, borderBottom = true }) {
  const isEmpty = data.status === 'empty'
  const isActive = data.isActive
  const pct = weightPct(data)
  const barColor = pct < 20 ? '#ff453a' : data.color

  return h('div', {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      padding: '10px 12px',
      background: isActive ? 'rgba(10,132,255,0.06)' : 'transparent',
      cursor: 'pointer',
      position: 'relative',
      borderBottom: borderBottom ? '1px solid #3a3a3c' : 'none',
    },
    onClick: () => onPopup(data),
  },
    // Color swatch — 44×52px rounded rect
    h('div', {
      style: {
        width: 44,
        height: 52,
        borderRadius: 6,
        flexShrink: 0,
        background: isEmpty ? 'transparent' : data.color,
        border: isEmpty ? '2px dashed #3a3a3c' : 'none',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }
    }, isEmpty && h('span', { style: { fontSize: 16, opacity: 0.3, color: '#636366' } }, '—')),

    // Text stack
    h('div', { style: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 } },
      !isEmpty && h('div', { style: { display: 'flex', alignItems: 'center', gap: 6 } },
        h('span', { style: { fontSize: 9, color: '#636366', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' } },
          data.vendor),
        h('span', { style: { fontSize: 9, color: '#636366' } }, `#${data.id}`),
        isActive && h('span', { style: { fontSize: 7, background: 'rgba(10,132,255,0.2)', color: '#0a84ff', padding: '1px 5px', borderRadius: 3, fontWeight: 700, marginLeft: 'auto' } }, 'ACTIVE'),
      ),
      h('div', { style: { fontSize: 13, fontWeight: 700, color: isEmpty ? '#636366' : '#e5e5e7', lineHeight: 1.1 } },
        isEmpty ? 'Empty' : primaryLabel(data)),
      !isEmpty && h('div', { style: { fontSize: 11, color: '#8e8e93', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } },
        data.name),
      !isEmpty && h('div', { style: { width: '100%', height: 2, background: '#3a3a3c', borderRadius: 2, overflow: 'hidden', marginTop: 2 } },
        h('div', { style: { width: `${pct}%`, height: '100%', borderRadius: 2, background: barColor } })
      ),
    ),

    // Grams + chevron
    h('div', { style: { display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4, flexShrink: 0 } },
      !isEmpty && h('span', { style: { fontSize: 13, fontWeight: 600, color: pct < 20 ? '#ff453a' : '#8e8e93' } },
        `${Math.round(parseFloat(data.g) || 0)}g`),
      h('span', { style: { fontSize: 14, color: '#636366' } }, '›'),
    ),
  )
}

// ── SlotsSegment — row layout ─────────────────────────────────
function SlotsSegment({ getHass, onPopup }) {
  const hass = getHass()
  const sv = id => hass?.states?.[id]?.state ?? '—'
  const sa = (id, attr) => hass?.states?.[id]?.attributes?.[attr]

  const activeAms  = sa('sensor.p1s_01p00c5a3101668_active_tray', 'ams_index')
  const activeTray = sa('sensor.p1s_01p00c5a3101668_active_tray', 'tray_index')
  const isPrinting = sv('sensor.p1s_01p00c5a3101668_current_stage') === 'printing'

  const slotData = n => {
    const { ams, tray } = SLOT_AMS[n]
    const isActive = isPrinting && activeAms === ams && activeTray === tray
    const status   = sv(`sensor.ams_slot_${n}_status`)
    const reason   = sv(`input_text.ams_slot_${n}_unbound_reason`)
    const hex      = sv(`sensor.ams_slot_${n}_color_hex`)
    const color    = hex && !['unknown', 'unavailable', '—'].includes(hex) ? `#${hex}` : '#555'
    return {
      n, status, reason, color, isActive,
      vendor:        sv(`sensor.ams_slot_${n}_vendor`),
      material:      sv(`sensor.ams_slot_${n}_material`),
      name:          sv(`sensor.ams_slot_${n}_name`),
      id:            sv(`input_text.ams_slot_${n}_spool_id`),
      g:             sv(`sensor.ams_slot_${n}_remaining_g`),
      selectEntity:  `input_select.ams_slot_${n}_select_spool`,
      selectCurrent: sv(`input_select.ams_slot_${n}_select_spool`),
    }
  }

  const sectionStyle = { background: '#2c2c2e', borderRadius: 10, border: '1px solid #3a3a3c', overflow: 'hidden' }
  const sectionHeaderStyle = { padding: '8px 12px', borderBottom: '1px solid #3a3a3c', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }
  const sectionTitleStyle = { fontSize: 11, fontWeight: 600, color: '#e5e5e7' }
  const sectionSubStyle = { fontSize: 10, color: '#8e8e93' }

  const ams2pro = AMS_UNITS[0]
  const ams2proHum = sv(ams2pro.humEntity)
  const ams2proTemp = sv(ams2pro.tempEntity)
  const ams2proConnected = !['unavailable', 'unknown', '—'].includes(ams2proHum)

  const htUnits = AMS_UNITS.slice(1)

  return h('div', { style: { display: 'flex', flexDirection: 'column', gap: 8 } },

    h('div', { style: { display: 'flex', justifyContent: 'flex-end', padding: '0 2px 4px' } },
      h('button', {
        class: 'fiq-btn-bind',
        onClick: () => getHass()?.callService('input_button', 'press', { entity_id: 'input_button.filament_iq_reconcile_now' }),
      }, '↺ Reconcile')
    ),

    // AMS 2 Pro — 4 slot rows
    h('div', { style: sectionStyle },
      h('div', { style: sectionHeaderStyle },
        h('div', { style: sectionTitleStyle }, 'AMS 2 Pro'),
        h('div', { style: sectionSubStyle },
          ams2proConnected ? `${ams2proHum}% · ${ams2proTemp}°C` : 'Disconnected'
        )
      ),
      [1, 2, 3, 4].map((n, i) =>
        h(SlotRow, { key: n, n, data: slotData(n), onPopup, borderBottom: i < 3 })
      )
    ),

    // HT Units — sub-header per unit, single card
    h('div', { style: sectionStyle },
      h('div', { style: { ...sectionHeaderStyle, justifyContent: 'flex-start' } },
        h('div', { style: sectionTitleStyle }, 'HT Units')
      ),
      htUnits.map((unit, i) => {
        const hum      = sv(unit.humEntity)
        const temp     = sv(unit.tempEntity)
        const isDrying = sv(unit.dryEntity) === 'on'
        const dryTimeRaw = parseFloat(sv(unit.dryTimeEntity)) || 0
        const dh = Math.floor(dryTimeRaw)
        const dm = Math.round((dryTimeRaw - dh) * 60)
        const dryTimeStr = dh > 0 ? `${dh}h ${String(dm).padStart(2, '0')}m` : `${dm}m`
        const connected = !['unavailable', 'unknown', '—'].includes(hum)
        const isLast = i === htUnits.length - 1
        return h('div', { key: unit.name },
          h('div', {
            style: {
              display: 'flex',
              alignItems: 'center',
              padding: '6px 12px 4px',
              background: 'rgba(255,255,255,0.02)',
              borderBottom: '1px solid #3a3a3c',
              borderTop: i > 0 ? '1px solid #3a3a3c' : 'none',
            }
          },
            h('span', { style: { fontSize: 9, color: '#636366', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em' } },
              `HT${i + 1}`),
            !isDrying && connected && h('span', { style: { fontSize: 9, color: '#8e8e93', marginLeft: 8 } },
              `${hum}% · ${temp}°C`),
            isDrying && h('span', {
              style: {
                fontSize: 9, color: '#ff9f0a',
                background: 'rgba(255,159,10,0.1)', border: '1px solid rgba(255,159,10,0.3)',
                borderRadius: 8, padding: '1px 7px', marginLeft: 'auto',
              }
            }, `♨️ ${temp}°C · ${dryTimeStr}`),
          ),
          h(SlotRow, { n: unit.slots[0], data: slotData(unit.slots[0]), onPopup, borderBottom: !isLast })
        )
      })
    ),

    // External — single row
    h('div', { style: sectionStyle },
      h('div', { style: { ...sectionHeaderStyle, justifyContent: 'flex-start' } },
        h('div', { style: sectionTitleStyle }, 'External')
      ),
      h(SlotRow, { n: 8, data: slotData(8), onPopup, borderBottom: false })
    )
  )
}

// ── SlotPopup (ported verbatim from PrinterDashboardCard) ────
function SlotPopup({ popup, getHass, onClose, onViewSpool }) {
  const hass = getHass()
  const [pendingOption, setPendingOption] = useState(null)

  const selectSpool = option => {
    setPendingOption(option)
    getHass()?.callService('input_select', 'select_option', {
      entity_id: popup.selectEntity,
      option,
    })
  }

  const assignAndBind = () => {
    getHass()?.callService('script', 'turn_on', {
      entity_id: 'script.ams_slot_assign_and_update',
      variables: { slot: String(popup.n) },
    })
    onClose()
  }

  const selectState = hass?.states?.[popup.selectEntity]
  const allOptions = selectState?.attributes?.options || []
  const PLACEHOLDER = allOptions.find(o => o.startsWith('—') || o.startsWith('-')) || '— Select spool —'
  const options = allOptions.filter(o => o !== PLACEHOLDER)
  const currentOption = selectState?.state || popup.selectCurrent
  const displaySelected = pendingOption ?? currentOption

  return h('div', {
    style: S.popupOverlay,
    onClick: e => { if (e.target === e.currentTarget) onClose() },
  },
    h('div', { style: S.popupSheet },
      h('div', { style: S.popupDrag }),
      h('div', { style: S.popupHeader },
        h('div', { style: S.popupUnit }, `Slot ${popup.n}`),
        h('div', { style: S.popupTitle },
          popup.status === 'needs_bind' ? 'Binding Required' : `${popup.vendor} · ${popup.material}`
        ),
        h('div', { style: S.popupSub },
          popup.status === 'ok' ? `Currently assigned · spool #${popup.id}` : 'Select a spool below'
        )
      ),
      popup.status === 'ok' && h('div', { style: S.currentSpool },
        h('div', { style: { ...S.csDot, background: popup.color } }),
        h('div', { style: { flex: 1, minWidth: 0 } },
          h('div', { style: S.csName }, popup.name),
          h('div', { style: S.csMeta }, `Spool #${popup.id}`),
          h('div', { style: S.csWbar },
            h('div', { style: { ...S.csWfill, width: `${Math.min(100, Math.round((parseFloat(popup.g) || 0) / 1000 * 100))}%` } })
          )
        ),
        h('div', { style: { textAlign: 'right', flexShrink: 0 } },
          h('div', { style: S.csPct }, `${Math.min(100, Math.round((parseFloat(popup.g) || 0) / 1000 * 100))}%`),
          h('div', { style: S.csG }, `${Math.round(parseFloat(popup.g) || 0)}g left`)
        )
      ),
      popup.status === 'ok' && onViewSpool && popup.id && popup.id !== '—' && popup.id !== 'unavailable' && popup.id !== 'unknown' && h('div', {
        style: { padding: '4px 16px 8px' },
      },
        h('button', {
          style: { background: 'none', border: 'none', padding: 0, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: '#6aabda' },
          onClick: () => { onViewSpool(parseInt(popup.id, 10)); onClose() },
        },
          h(Icon, { path: ICONS.externalLink, size: 12, color: '#6aabda' }),
          'View in Spools'
        )
      ),
      h('div', { style: S.popupSec }, 'Select spool'),
      h('div', { style: S.pickerList, onTouchMove: e => e.stopPropagation() },
        options.length === 0
          ? h('div', { style: { padding: '16px', fontSize: 12, color: '#555', textAlign: 'center' } },
              'No spools available — run Reconcile'
            )
          : options.map(option =>
              h('div', {
                key: option,
                style: { ...S.pickerRow, ...(option === displaySelected ? S.pickerRowSelected : {}) },
                onClick: () => selectSpool(option),
              },
                h('div', { style: { ...S.pickerLabel, color: option === displaySelected ? '#6aabda' : '#e8e8ea' } }, option),
                option === displaySelected && h(Icon, { path: ICONS.chevron, size: 14, color: '#6aabda' })
              )
            )
      ),
      h('div', {
        style: S.assignBtn,
        onClick: assignAndBind,
      },
        h(Icon, { path: ICONS.link, size: 16, color: '#6aabda' }),
        h('span', { style: S.assignLabel }, 'Assign & bind')
      )
    )
  )
}

// ── Default export: wires SlotsSegment + SlotPopup ──────────
export default function SlotsTab({ getHass, onViewSpool }) {
  const [popup, setPopup] = useState(null)
  return h('div', { style: { position: 'relative' } },
    h(SlotsSegment, { getHass, onPopup: setPopup }),
    popup && h(SlotPopup, { popup, getHass, onClose: () => setPopup(null), onViewSpool })
  )
}
