import { h } from 'preact'
import { useState, useEffect } from 'preact/hooks'
import { LocationSelect } from './LocationSelect'
import { useProvider } from '../provider/context'
import { useSnapshot } from '../hooks/useSnapshot'

// ── MDI SVG paths ────────────────────────────────────────────
const ICONS = {
  printer:      'M6,2A2,2 0 0,0 4,4V10A2,2 0 0,0 6,12H10A2,2 0 0,0 12,10V4A2,2 0 0,0 10,2H6M6,4H10V10H6V4M4,14A2,2 0 0,0 2,16V22H22V16A2,2 0 0,0 20,14H4M6,16H18V20H6V16Z',
  nozzle:       'M7,2H17V8H19V13H16.5L13,17H11L7.5,13H5V8H7V2M10,22H2V20H10A1,1 0 0,0 11,19V18H13V19A3,3 0 0,1 10,22Z',
  fan:          'M12,11A1,1 0 0,0 11,12A1,1 0 0,0 12,13A1,1 0 0,0 13,12A1,1 0 0,0 12,11M12.5,2C17,2 17.11,5.57 14.75,6.75C13.76,7.24 13.32,8.29 13.13,9.22C13.61,9.42 14.03,9.73 14.35,10.13C18.05,8.13 22.03,8.92 22.03,12.5C22.03,17 18.46,17.1 17.28,14.73C16.78,13.74 15.72,13.3 14.79,13.11C14.59,13.59 14.28,14 13.88,14.34C15.87,18.03 15.08,22 11.5,22C7,22 6.91,18.42 9.27,17.24C10.25,16.75 10.69,15.71 10.89,14.79C10.4,14.59 9.97,14.27 9.65,13.87C5.96,15.85 2,15.07 2,11.5C2,7 5.56,6.89 6.74,9.26C7.24,10.25 8.29,10.68 9.22,10.87C9.41,10.39 9.73,9.97 10.14,9.65C8.15,5.96 8.94,2 12.5,2Z',
  link:         'M10.59,13.41C11,13.8 11,14.44 10.59,14.83C10.2,15.22 9.56,15.22 9.17,14.83C7.22,12.88 7.22,9.71 9.17,7.76V7.76L12.76,4.17C14.71,2.22 17.88,2.22 19.83,4.17C21.78,6.12 21.78,9.29 19.83,11.24L18.07,13C18.07,11.96 17.89,10.92 17.51,9.94L18.42,9C19.59,7.85 19.59,5.96 18.42,4.79C17.25,3.62 15.36,3.62 14.19,4.79L10.59,8.38C9.42,9.55 9.42,11.44 10.59,12.61L10.59,13.41M13.41,10.59C13.8,10.2 14.44,10.2 14.83,10.59C16.78,12.54 16.78,15.71 14.83,17.66V17.66L11.24,21.25C9.29,23.2 6.12,23.2 4.17,21.25C2.22,19.3 2.22,16.13 4.17,14.18L5.93,12.46C5.93,13.5 6.11,14.54 6.49,15.52L5.58,16.43C4.41,17.6 4.41,19.49 5.58,20.66C6.75,21.83 8.64,21.83 9.81,20.66L13.41,17.07C14.58,15.9 14.58,14.01 13.41,12.84C13,12.45 13,11.81 13.41,11.42L13.41,10.59Z',
  chevron:      'M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z',
  chevronDown:  'M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z',
  chevronUp:    'M7.41,15.41L12,10.83L16.59,15.41L18,14L12,8L6,14L7.41,15.41Z',
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

// ── Slot -> unit membership (for section grouping only; the AMS-index/
// tray-index cross-reference itself now lives in HassProvider) ───────────
const UNIT_SLOTS = {
  'AMS 2 Pro': [1, 2, 3, 4],
  'HT1': [5],
  'HT2': [6],
  'HT3': [7],
}

const LOCATION_TO_SLOT = {
  'AMS1_Slot1':   1,
  'AMS1_Slot2':   2,
  'AMS1_Slot3':   3,
  'AMS1_Slot4':   4,
  'AMS128_Slot1': 5,
  'AMS129_Slot1': 6,
  'AMS130_Slot1': 7,
}

const primaryLabel = d => {
  if (d.status === 'empty') return 'Empty'
  const r = d.unboundReason
  // Printer hardware swap: binding preserved, RFID re-confirming. Show a neutral
  // swap badge (never the raw reason string); the preserved spool name still
  // renders on the secondary line below.
  if (r === 'PRINTER_SERIAL_CHANGED') return '⚠ Confirming after printer swap'
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
const weightPct = d => Math.min(100, Math.max(0, Math.round((d.remainingG || 0) / 1000 * 100)))

// ── SlotRow — horizontal layout used by all sections ─────────
function SlotRow({ n, data, onPopup, spools, borderBottom = true }) {
  const provider = useProvider()
  const spoolId = parseInt(data.spoolId, 10)
  // Treat spool_id 0 / '0' / falsy as an empty slot: short-circuit before any
  // Spoolman lookup or match attempt so an unbound slot renders a clean Empty
  // state instead of "No Match Found" / "Too Generic" / "UNKNOWN #0".
  const isEmpty = data.status === 'empty' || data.spoolId === 0 || data.spoolId === '0' || !data.spoolId || spoolId === 0
  const isActive = data.isActive
  const matchedSpool = (!isNaN(spoolId) && spoolId > 0) ? spools?.find(s => s.id === spoolId) : null
  const multiColorHexes = matchedSpool?.filament?.multi_color_hexes || null
  const slotSwatchBg = multiColorHexes && multiColorHexes.split(',').length >= 2
    ? (() => { const cols = multiColorHexes.split(',').map(h => `#${h.trim().replace('#','')}`); return `linear-gradient(135deg, ${cols[0]} 50%, ${cols[1]} 50%)` })()
    : (isEmpty ? 'transparent' : data.colorHex)
  const pct = weightPct(data)
  const barColor = pct < 20 ? '#ff453a' : data.colorHex

  const [profileStatus, setProfileStatus] = useState('idle')

  useEffect(() => {
    if (isEmpty || data.status !== 'ok') return
    const spoolId = parseInt(data.spoolId, 10)
    if (isNaN(spoolId) || spoolId <= 0) return
    const filamentId = spools?.find(s => s.id === spoolId)?.filament?.id
    if (!filamentId) return
    if (!provider) return

    let cancelled = false
    provider.rpc('filament.profileLookup', { filament_id: filamentId })
      .then((d) => {
        if (!cancelled) setProfileStatus(d.status || 'unverified')
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

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
        background: slotSwatchBg,
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
        h('span', { style: { fontSize: 9, color: '#636366' } }, `#${data.spoolId}`),
        isActive && h('span', { style: { fontSize: 7, background: 'rgba(10,132,255,0.2)', color: '#0a84ff', padding: '1px 5px', borderRadius: 3, fontWeight: 700, marginLeft: 'auto' } }, 'ACTIVE'),
      ),
      h('div', { style: { fontSize: 13, fontWeight: 700, color: isEmpty ? '#636366' : (data.unboundReason === 'PRINTER_SERIAL_CHANGED' ? '#ff9f0a' : '#e5e5e7'), lineHeight: 1.1 } },
        // External port (slot 8) is binary — when nothing is loaded show a
        // minimal "No spool loaded" rather than a generic "Empty".
        isEmpty ? (n === 8 ? 'No spool loaded' : 'Empty') : primaryLabel(data)),
      data.ranOut && h('div', { style: { fontSize: 10, color: '#ff9f0a', marginTop: 1 } }, '🪫 Ran out during print'),
      !isEmpty && h('div', { style: { display: 'flex', alignItems: 'center', gap: 4, overflow: 'hidden' } },
        h('span', { style: { fontSize: 11, color: '#8e8e93', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, data.filamentName),
        profileStatus === 'verified' && h('span', { class: 'fiq-slot-profile-pip fiq-slot-pip-verified', title: 'Profile verified' }, '✓ Profile'),
        profileStatus === 'candidate' && h('span', { class: 'fiq-slot-profile-pip fiq-slot-pip-candidate', title: 'Profile unverified — verify in Filaments tab' }, '? Unverified'),
      ),
      !isEmpty && h('div', { style: { width: '100%', height: 2, background: '#3a3a3c', borderRadius: 2, overflow: 'hidden', marginTop: 2 } },
        h('div', { style: { width: `${pct}%`, height: '100%', borderRadius: 2, background: barColor } })
      ),
    ),

    // Grams + chevron
    h('div', { style: { display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4, flexShrink: 0 } },
      !isEmpty && h('span', { style: { fontSize: 13, fontWeight: 600, color: pct < 20 ? '#ff453a' : '#8e8e93' } },
        `${Math.round(data.remainingG || 0)}g`),
      h('span', { style: { fontSize: 14, color: '#636366' } }, '›'),
    ),
  )
}

// ── SlotsSegment — row layout ─────────────────────────────────
function SlotsSegment({ onPopup, spools }) {
  const provider = useProvider()
  const [reconciling, setReconciling] = useState(false)
  const snapshot = useSnapshot() || { slots: [], amsUnits: [] }
  const slotByIndex = n => snapshot.slots.find(s => s.index === n) || {}
  const unitByName = name => snapshot.amsUnits.find(u => u.name === name) || {}

  const sectionStyle = { background: '#2c2c2e', borderRadius: 10, border: '1px solid #3a3a3c', overflow: 'hidden' }
  const sectionHeaderStyle = { padding: '8px 12px', borderBottom: '1px solid #3a3a3c', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }
  const sectionTitleStyle = { fontSize: 11, fontWeight: 600, color: '#e5e5e7' }
  const sectionSubStyle = { fontSize: 10, color: '#8e8e93' }

  const ams2pro = unitByName('AMS 2 Pro')
  const htUnitNames = ['HT1', 'HT2', 'HT3']

  const handleReconcile = () => {
    if (!provider) return
    provider.rpc('reconcile.now')
    setReconciling(true)
    setTimeout(() => setReconciling(false), 4000)
  }

  return h('div', { style: { display: 'flex', flexDirection: 'column', gap: 8 } },

    h('div', { style: { display: 'flex', justifyContent: 'flex-end', padding: '0 2px 4px' } },
      h('button', {
        class: 'fiq-btn-bind',
        onClick: handleReconcile,
        disabled: reconciling,
        style: reconciling ? { opacity: 0.6, cursor: 'default' } : undefined,
      }, reconciling ? '↻ Reconciling…' : '↺ Reconcile')
    ),

    // AMS 2 Pro — 4 slot rows
    h('div', { style: sectionStyle },
      h('div', { style: sectionHeaderStyle },
        h('div', { style: sectionTitleStyle }, 'AMS 2 Pro'),
        h('div', { style: sectionSubStyle },
          ams2pro.connected ? `💧 ${ams2pro.humidity}% · 🌡️ ${ams2pro.temperature}°C` : 'Disconnected'
        )
      ),
      UNIT_SLOTS['AMS 2 Pro'].map((n, i) =>
        h(SlotRow, { key: n, n, data: slotByIndex(n), onPopup, spools, borderBottom: i < 3 })
      )
    ),

    // HT Units — sub-header per unit, single card
    h('div', { style: sectionStyle },
      h('div', { style: { ...sectionHeaderStyle, justifyContent: 'flex-start' } },
        h('div', { style: sectionTitleStyle }, 'HT Units')
      ),
      htUnitNames.map((name, i) => {
        const unit = unitByName(name)
        const dh = Math.floor((unit.dryingRemainingMin || 0) / 60)
        const dm = (unit.dryingRemainingMin || 0) % 60
        const dryTimeStr = dh > 0 ? `${dh}h ${String(dm).padStart(2, '0')}m` : `${dm}m`
        const isLast = i === htUnitNames.length - 1
        const slotN = UNIT_SLOTS[name][0]
        return h('div', { key: name },
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
            unit.connected && !unit.drying && h('span', { style: { fontSize: 9, color: '#8e8e93', marginLeft: 8 } },
              `💧 ${unit.humidity}% · 🌡️ ${unit.temperature}°C`),
            unit.drying && h('span', {
              style: {
                fontSize: 9, color: '#ff9f0a',
                background: 'rgba(255,159,10,0.1)', border: '1px solid rgba(255,159,10,0.3)',
                borderRadius: 8, padding: '1px 7px', marginLeft: 'auto',
              }
            }, `🔥 ${unit.temperature}°C · ${dryTimeStr} · 💧 ${unit.humidity}%`),
          ),
          h(SlotRow, { n: slotN, data: slotByIndex(slotN), onPopup, spools, borderBottom: !isLast })
        )
      })
    ),

    // External — single row
    h('div', { style: sectionStyle },
      h('div', { style: { ...sectionHeaderStyle, justifyContent: 'flex-start' } },
        h('div', { style: sectionTitleStyle }, 'External')
      ),
      h(SlotRow, { n: 8, data: slotByIndex(8), onPopup, spools, borderBottom: false })
    )
  )
}

// ── SpoolModal — bottom-sheet spool editor ───────────────────
function SpoolModal({ spool, updateSpool, deleteSpool, onClose, onCloseAll }) {
  const provider = useProvider()
  const [remaining, setRemaining] = useState(Math.round(spool.remaining_weight || 0))
  const [location, setLocation] = useState(spool.location || '')
  const [firstUsed, setFirstUsed] = useState(
    spool.first_used ? spool.first_used.substring(0, 10) : ''
  )
  const [saving, setSaving] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [showMore, setShowMore] = useState(false)
  const [printingLabel, setPrintingLabel] = useState(false)
  const [printingNiimbotLabel, setPrintingNiimbotLabel] = useState(false)
  const [toast, setToast] = useState(null)
  const [profileStatus, setProfileStatus] = useState('idle')
  const [profileData, setProfileData] = useState(null)
  const [profileLookedUp, setProfileLookedUp] = useState(false)

  useEffect(() => {
    if (!showMore || profileLookedUp || !provider || !spool.filament?.id) return
    setProfileLookedUp(true)
    setProfileStatus('loading')
    let cancelled = false
    provider.rpc('filament.profileLookup', { filament_id: spool.filament.id })
      .then((d) => {
        if (cancelled) return
        setProfileData(d)
        setProfileStatus(d.status || 'unverified')
      })
      .catch(() => {
        if (!cancelled) setProfileStatus('error')
      })
    return () => { cancelled = true }
  }, [showMore])

  const f = spool.filament || {}
  const vendor = f.vendor?.name || ''
  const material = f.material || ''
  const name = f.name || '—'
  const colorHex = (f.color_hex || '555555').replace('#', '')
  const swatchColor = `#${colorHex}`
  const isBlack = colorHex.toLowerCase() === '000000'
  const multiHexes = f.multi_color_hexes ? f.multi_color_hexes.split(',').map(h => `#${h.trim().replace('#','')}`) : null
  const swatchBg = multiHexes && multiHexes.length >= 2
    ? `linear-gradient(135deg, ${multiHexes[0]} 50%, ${multiHexes[1]} 50%)`
    : swatchColor

  const lotNr = spool.lot_nr || '—'
  const lastUsed = spool.last_used ? spool.last_used.substring(0, 10) : '—'
  const locationDisplay = location || 'Unassigned'
  const subtitle = [vendor, material, locationDisplay].filter(Boolean).join(' · ')

  const handleSave = async () => {
    setSaving(true)
    try {
      await updateSpool(spool.id, {
        remaining_weight: Number(remaining),
        location,
        ...(firstUsed ? { first_used: firstUsed } : {}),
      })
      const slot = LOCATION_TO_SLOT[location]
      if (slot && provider) {
        provider.rpc('slot.assigned', { slot, spool_id: spool.id })
      }
      onClose()
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    await deleteSpool(spool.id)
    onCloseAll()
  }

  const handlePrintLabel = () => {
    if (!provider) return
    setPrintingLabel(true)
    try {
      provider.rpc('label.print', { spool_id: spool.id, awaitResponse: false })
    } catch (_) {}
    setTimeout(() => setPrintingLabel(false), 15000)
  }

  const handlePrintSwatchLabel = async () => {
    if (!provider) return
    setPrintingNiimbotLabel(true)
    try {
      const d = await provider.rpc('label.printNiimbot', { spool_id: spool.id })
      setPrintingNiimbotLabel(false)
      if (d.success) {
        setToast({ msg: 'Swatch label queued for printing', type: 'ok' })
      } else {
        setToast({ msg: `Swatch print failed: ${d.error || 'unknown error'}`, type: 'err' })
      }
      setTimeout(() => setToast(null), 5000)
    } catch (e) {
      setPrintingNiimbotLabel(false)
      const msg = e?.message?.endsWith('timed out') ? 'Swatch print timed out' : `Swatch print failed: ${e.message || e}`
      setToast({ msg, type: 'err' })
      setTimeout(() => setToast(null), 5000)
    }
  }

  const inpStyle = {
    background: '#2a2a2e',
    border: '1px solid #3a3a3c',
    borderRadius: 6,
    padding: '6px 8px',
    fontSize: 12,
    color: '#e5e5e7',
    width: '100%',
    boxSizing: 'border-box',
  }

  return h('div', {
    style: {
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      background: 'rgba(0,0,0,0.72)',
      display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
      zIndex: 10000,
    },
    onClick: e => { if (e.target === e.currentTarget) onClose() },
  },
    toast && h('div', { class: `fiq-toast ${toast.type === 'err' ? 'fiq-toast-err' : 'fiq-toast-ok'}` }, toast.msg),
    h('div', {
      style: {
        background: '#1c1c1f',
        borderRadius: '16px 16px 0 0',
        maxHeight: '90vh',
        overflowY: 'auto',
        position: 'relative',
      },
      onClick: e => e.stopPropagation(),
    },

      // Drag handle
      h('div', { style: { width: 36, height: 4, background: '#3a3a3e', borderRadius: 2, margin: '10px auto 4px' } }),

      // Header: "Spool #N" + × close
      h('div', {
        style: { padding: '8px 16px 10px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
      },
        h('span', { style: { fontSize: 15, fontWeight: 600, color: '#f5f5f5' } }, `Spool #${spool.id}`),
        h('button', {
          style: {
            width: 28, height: 28, borderRadius: '50%',
            background: '#2a2a2e', border: 'none',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            cursor: 'pointer', padding: 0, color: '#8e8e93', fontSize: 18, lineHeight: 1,
          },
          onClick: onClose,
        }, '×')
      ),

      // Identity block: swatch + name + subtitle
      h('div', {
        style: {
          padding: '10px 16px 14px',
          display: 'flex', alignItems: 'center', gap: 12,
          borderBottom: '1px solid rgba(255,255,255,0.06)',
          borderTop: '1px solid rgba(255,255,255,0.06)',
        },
      },
        h('div', {
          style: {
            width: 40, height: 40, borderRadius: 8, flexShrink: 0,
            background: swatchBg,
            border: isBlack && !multiHexes ? '1px solid #444' : 'none',
          },
        }),
        h('div', { style: { flex: 1, minWidth: 0 } },
          h('div', { style: { fontSize: 13, fontWeight: 600, color: '#e5e5e7' } }, name),
          h('div', { style: { fontSize: 11, color: '#8e8e93', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, subtitle),
        )
      ),

      // Fields row: Remaining · Location
      h('div', {
        style: {
          padding: '10px 16px',
          display: 'flex', gap: 8,
          borderBottom: '1px solid rgba(255,255,255,0.06)',
        },
      },
        h('div', { style: { flex: 1 } },
          h('div', { style: { fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#555', marginBottom: 4 } }, 'Remaining (g)'),
          h('input', {
            style: inpStyle,
            type: 'number',
            value: remaining,
            onInput: e => setRemaining(e.target.value),
          })
        ),
        h('div', { style: { flex: 1 } },
          h('div', { style: { fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#555', marginBottom: 4 } }, 'Location'),
          h(LocationSelect, { value: location, onChange: setLocation }),
        ),
      ),

      // More info toggle
      h('div', {
        style: {
          padding: '8px 16px 6px',
          display: 'flex', alignItems: 'center', gap: 6,
          cursor: 'pointer', color: '#8e8e93', fontSize: 12,
        },
        onClick: () => setShowMore(v => !v),
      },
        h('svg', { viewBox: '0 0 24 24', style: { width: 14, height: 14, fill: '#8e8e93', flexShrink: 0 } },
          h('path', { d: 'M13,9H11V7H13M13,17H11V11H13M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2Z' })
        ),
        h('span', null, 'More info'),
        h('svg', {
          viewBox: '0 0 24 24',
          style: { width: 14, height: 14, fill: '#8e8e93', flexShrink: 0, transform: showMore ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.2s' },
        },
          h('path', { d: 'M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z' })
        ),
      ),
      showMore && h('div', {
        style: {
          margin: '0 16px 8px',
          background: '#2c2c2e',
          borderRadius: 8,
          padding: '10px 12px',
        },
      },
        h('div', { style: { marginBottom: 8 } },
          h('div', { style: { fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#555', marginBottom: 3 } }, 'LOT #'),
          h('div', { style: { fontSize: 11, fontFamily: 'monospace', color: '#e5e5e7', wordBreak: 'break-all', whiteSpace: 'normal' } }, lotNr),
        ),
        h('div', { style: { marginBottom: 8 } },
          h('div', { style: { fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#555', marginBottom: 3 } }, 'Spool ID'),
          h('div', { style: { fontSize: 11, fontFamily: 'monospace', color: '#e5e5e7' } }, `#${spool.id}`),
        ),
        h('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 } },
          h('div', null,
            h('div', { style: { fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#555', marginBottom: 4 } }, 'First used'),
            h('input', { style: inpStyle, type: 'date', value: firstUsed, onInput: e => setFirstUsed(e.target.value) }),
          ),
          h('div', null,
            h('div', { style: { fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#555', marginBottom: 3 } }, 'Last used'),
            h('div', { style: { fontSize: 11, fontFamily: 'monospace', color: '#e5e5e7' } }, lastUsed),
          ),
        ),
        profileStatus !== 'idle' && h('div', { style: { marginTop: 10, paddingTop: 10, borderTop: '0.5px solid #3a3a3c' } },
          h('div', { style: { fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#636366', marginBottom: 6 } }, '3D Filament Profile'),
          profileStatus === 'loading' && h('div', { class: 'fiq-profile-loading' }, 'Looking up profile...'),
          profileStatus === 'verified' && profileData && h('div', null,
            h('div', { class: 'fiq-profile-match-row' },
              h('span', { class: 'fiq-profile-badge fiq-profile-verified' }, '✓ Verified'),
              h('span', { class: 'fiq-profile-name' }, profileData.profile_name),
            ),
            h('div', { class: 'fiq-profile-actions', style: { marginTop: 6 } },
              h('button', { class: 'fiq-profile-btn', onClick: () => window.open(profileData.profile_url, '_blank', 'noopener') }, 'View profile ↗'),
            ),
          ),
          profileStatus === 'candidate' && profileData && h('div', null,
            h('div', { class: 'fiq-profile-match-row' },
              h('span', { class: 'fiq-profile-badge fiq-profile-candidate' }, '? Candidate'),
              h('span', { class: 'fiq-profile-name' }, profileData.profile_name),
            ),
            h('div', { class: 'fiq-profile-actions', style: { marginTop: 6 } },
              h('button', { class: 'fiq-profile-btn', onClick: () => window.open(profileData.profile_url, '_blank', 'noopener') }, 'View candidate ↗'),
              h('span', { class: 'fiq-profile-loading', style: { marginLeft: 4 } }, 'Verify in Filaments tab'),
            ),
          ),
          (profileStatus === 'no_profile_exists' || profileStatus === 'unverified' || profileStatus === 'error') && h('div', { class: 'fiq-profile-match-row' },
            h('span', { class: 'fiq-profile-badge fiq-profile-none' }, '— No profile'),
          ),
        ),
      ),

      // Action row 1: Delete | Spool Label | Swatch
      h('div', { style: { padding: '10px 16px 4px', display: 'flex', gap: 8 } },
        h('button', {
          style: {
            flex: 1, background: '#3a1515', border: 'none', borderRadius: 8,
            padding: '9px 4px', fontSize: 12, fontWeight: 600, color: '#e05555',
            cursor: 'pointer',
          },
          onClick: () => setConfirming(true),
          disabled: saving,
        }, 'Delete spool'),
        h('button', {
          style: {
            flex: 1, background: '#1a2035', border: 'none', borderRadius: 8,
            padding: '9px 4px', fontSize: 12, fontWeight: 600, color: '#5B8AF0',
            cursor: 'pointer',
          },
          onClick: handlePrintLabel,
          disabled: saving || printingLabel,
        }, printingLabel ? '⏳ Printing…' : '🖨 Spool Label'),
        h('button', {
          style: {
            flex: 1, background: '#1a2035', border: 'none', borderRadius: 8,
            padding: '9px 4px', fontSize: 12, fontWeight: 600, color: '#5B8AF0',
            cursor: 'pointer',
          },
          onClick: handlePrintSwatchLabel,
          disabled: saving || printingNiimbotLabel || profileStatus !== 'verified',
          title: profileStatus !== 'verified' ? 'Verify filament profile in Filaments tab to enable swatch printing' : '',
        }, printingNiimbotLabel ? 'Queuing…' : '🖨 Swatch'),
      ),

      // Action row 2: Cancel | Save changes
      h('div', { style: { padding: '8px 16px 20px', display: 'flex', gap: 8 } },
        h('button', {
          style: {
            flex: 1, background: '#2a2a2e', border: 'none', borderRadius: 8,
            padding: '11px 8px', fontSize: 13, fontWeight: 600, color: '#8e8e93',
            cursor: 'pointer',
          },
          onClick: onClose,
          disabled: saving,
        }, 'Cancel'),
        h('button', {
          style: {
            flex: 2, background: '#1a2035', border: 'none', borderRadius: 8,
            padding: '11px 8px', fontSize: 13, fontWeight: 600, color: '#5B8AF0',
            cursor: 'pointer',
          },
          onClick: handleSave,
          disabled: saving,
        }, saving ? 'Saving…' : 'Save changes'),
      ),

      // Confirm delete overlay
      confirming && h('div', {
        style: {
          position: 'absolute', inset: 0,
          background: 'rgba(0,0,0,0.80)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          borderRadius: '16px 16px 0 0',
        },
      },
        h('div', {
          style: {
            background: '#2c2c2e', borderRadius: 12,
            padding: '22px 20px', margin: '0 28px', width: '100%',
          },
        },
          h('div', { style: { fontSize: 14, fontWeight: 600, color: '#f5f5f5', marginBottom: 6, textAlign: 'center' } },
            'Delete this spool?'),
          h('div', { style: { fontSize: 12, color: '#8e8e93', marginBottom: 18, textAlign: 'center' } },
            'This cannot be undone.'),
          h('div', { style: { display: 'flex', gap: 8 } },
            h('button', {
              style: {
                flex: 1, background: '#2a2a2e', border: 'none', borderRadius: 8,
                padding: '11px 8px', fontSize: 13, fontWeight: 600, color: '#8e8e93',
                cursor: 'pointer',
              },
              onClick: () => setConfirming(false),
            }, 'Cancel'),
            h('button', {
              style: {
                flex: 2, background: '#3a1515', border: 'none', borderRadius: 8,
                padding: '11px 8px', fontSize: 13, fontWeight: 600, color: '#e05555',
                cursor: 'pointer',
              },
              onClick: handleDelete,
            }, 'Delete'),
          )
        )
      ),
    )
  )
}

// ── SlotPopup ────────────────────────────────────────────────
function SlotPopup({ popup, onClose, spools, updateSpool, deleteSpool }) {
  const provider = useProvider()
  const [pendingOption, setPendingOption] = useState(null)
  const [spoolModal, setSpoolModal] = useState(false)

  const spoolId = parseInt(popup.spoolId, 10)
  const spool = spools?.find(s => s.id === spoolId)
  const canEditSpool = popup.status === 'ok' && popup.spoolId && popup.spoolId !== '—' && popup.spoolId !== 'unavailable' && popup.spoolId !== 'unknown' && spool

  const selectSpool = option => {
    setPendingOption(option)
    provider?.rpc('slot.selectSpool', { index: popup.index, option })
  }

  const assignAndBind = () => {
    provider?.rpc('slot.assignAndBind', { slot: popup.index })
    onClose()
  }

  const options = popup.spoolOptions || []
  const displaySelected = pendingOption ?? popup.selectedOption

  const pct = Math.min(100, Math.round((popup.remainingG || 0) / 1000 * 100))
  const popupMultiHexes = spool?.filament?.multi_color_hexes || null
  const popupSwatchBg = popupMultiHexes && popupMultiHexes.split(',').length >= 2
    ? (() => { const cols = popupMultiHexes.split(',').map(h => `#${h.trim().replace('#','')}`); return `linear-gradient(135deg, ${cols[0]} 50%, ${cols[1]} 50%)` })()
    : popup.colorHex

  return h('div', {
    style: S.popupOverlay,
    onClick: e => { if (e.target === e.currentTarget) onClose() },
  },
    h('div', { style: S.popupSheet },
      h('div', { style: S.popupDrag }),
      h('div', { style: S.popupHeader },
        h('div', { style: S.popupUnit }, `Slot ${popup.index}`),
        h('div', { style: S.popupTitle },
          popup.status === 'needs_bind' ? 'Binding Required' : `${popup.vendor} · ${popup.material}`
        ),
        h('div', { style: S.popupSub },
          popup.status === 'ok' ? `Currently assigned · spool #${popup.spoolId}` : 'Select a spool below'
        )
      ),

      // Spool identity block — tappable if canEditSpool
      popup.status === 'ok' && h('div', {
        style: {
          ...S.currentSpool,
          cursor: canEditSpool ? 'pointer' : 'default',
          transition: 'background 0.15s',
        },
        onClick: canEditSpool ? () => setSpoolModal(true) : undefined,
      },
        h('div', { style: { ...S.csDot, background: popupSwatchBg } }),
        h('div', { style: { flex: 1, minWidth: 0 } },
          h('div', { style: S.csName }, popup.filamentName),
          h('div', { style: S.csMeta }, `Spool #${popup.spoolId}`),
          h('div', { style: S.csWbar },
            h('div', { style: { ...S.csWfill, width: `${pct}%` } })
          )
        ),
        h('div', { style: { textAlign: 'right', flexShrink: 0 } },
          h('div', { style: S.csPct }, `${pct}%`),
          h('div', { style: S.csG }, `${Math.round(popup.remainingG || 0)}g left`)
        ),
        canEditSpool && h(Icon, { path: ICONS.chevron, size: 16, color: '#555', style: { marginLeft: 4 } }),
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
    ),

    // SpoolModal renders above the slot popup sheet
    spoolModal && canEditSpool && h(SpoolModal, {
      spool,
      updateSpool,
      deleteSpool,
      onClose: () => setSpoolModal(false),
      onCloseAll: () => { setSpoolModal(false); onClose() },
    }),
  )
}

// ── Default export: wires SlotsSegment + SlotPopup ──────────
export default function SlotsTab({ spools, updateSpool, deleteSpool }) {
  const [popup, setPopup] = useState(null)
  return h('div', { style: { position: 'relative' } },
    h(SlotsSegment, { onPopup: setPopup, spools }),
    popup && h(SlotPopup, { popup, onClose: () => setPopup(null), spools, updateSpool, deleteSpool })
  )
}
