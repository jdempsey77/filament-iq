import { h } from 'preact'
import { useState, useEffect } from 'preact/hooks'
import { useProvider } from '../provider/context'
import { useSnapshot } from '../hooks/useSnapshot'
import { useSpoolPrintActions } from '../hooks/useSpoolPrintActions'
import { SpoolEditPanel, MatBadge } from './SpoolsTab'

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

// Shared across SlotRow (mobile) and SlotCard (desktop) -- exactly one of
// those two ever mounts for a given slot at a time (SlotsSegment picks one
// or the other based on isDesktop), so calling this from either is safe:
// the profileLookup RPC never fires twice concurrently for the same slot.
function useSlotProfileStatus(data, spools) {
  const provider = useProvider()
  const [profileStatus, setProfileStatus] = useState('idle')
  const isEmpty = data.status === 'empty' || data.spoolId === 0 || data.spoolId === '0' || !data.spoolId || parseInt(data.spoolId, 10) === 0

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
      .catch((e) => {
        // Known-unavailable (no AppDaemon listener) on every slot right
        // now -- a persistent pip on all 8 rows for a feature nobody can
        // use yet is noise, not signal. Stay at the default 'idle' (which
        // already renders nothing); log so it's still debuggable. Self-
        // healing: once the listener exists, this starts rendering real
        // verified/candidate pips with no further change needed here.
        console.warn('[useSlotProfileStatus] profileLookup unavailable:', e?.message || e)
      })
    return () => { cancelled = true }
  }, [])

  return profileStatus
}

// ── SlotRow — horizontal layout used by all sections ─────────
function SlotRow({ n, data, onPopup, spools, borderBottom = true }) {
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

  const profileStatus = useSlotProfileStatus(data, spools)

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

function slotShortLabel(n) {
  if (n >= 1 && n <= 4) return `Slot ${n}`
  if (n === 5) return 'HT1'
  if (n === 6) return 'HT2'
  if (n === 7) return 'HT3'
  return 'External'
}

function dryTimeLabel(minutes) {
  const dh = Math.floor((minutes || 0) / 60)
  const dm = (minutes || 0) % 60
  return dh > 0 ? `${dh}h ${String(dm).padStart(2, '0')}m` : `${dm}m`
}

// ── SlotCard — desktop hybrid card: color-flooded header (status pill only)
// + neutral body (vendor/material/remaining/fill bar). Status color and
// filament color never compete: the header is flooded with the filament's
// own color, and the status pill's background is fixed per status (never
// derived from the filament color), so a low red spool still reads "LOW".
// `ambient` (HT cards only -- each HT unit maps to exactly one slot) renders
// as translucent pills bottom-left of the color field so they read on any
// filament color; AMS 2 Pro's ambient reading covers 4 slots and lives once
// in its section header instead (see SlotsSegment).
function SlotCard({ n, data, onPopup, spools, ambient }) {
  const spoolId = parseInt(data.spoolId, 10)
  const isEmpty = data.status === 'empty' || data.spoolId === 0 || data.spoolId === '0' || !data.spoolId || spoolId === 0
  const isActive = data.isActive
  const matchedSpool = (!isNaN(spoolId) && spoolId > 0) ? spools?.find(s => s.id === spoolId) : null
  const multiColorHexes = matchedSpool?.filament?.multi_color_hexes || null
  const headerBg = isEmpty
    ? null
    : (multiColorHexes && multiColorHexes.split(',').length >= 2
        ? (() => { const cols = multiColorHexes.split(',').map(h => `#${h.trim().replace('#','')}`); return `linear-gradient(135deg, ${cols[0]} 50%, ${cols[1]} 50%)` })()
        // data.colorHex is already a resolved CSS color (e.g. "#3388ff"),
        // per the domain snapshot contract -- SlotRow/SlotPopup use it bare
        // the same way. Prefixing it again here produced invalid CSS
        // ("##3388ff"), which silently fell back to .fiq-slot-card's own
        // flat gray background.
        : data.colorHex)
  const pct = weightPct(data)
  const profileStatus = useSlotProfileStatus(data, spools)

  let statusCls = null
  let statusText = null
  if (isEmpty) { statusCls = 'status-empty'; statusText = 'EMPTY' }
  else if (data.status === 'needs_bind') { statusCls = 'status-unbound'; statusText = 'UNBOUND' }
  else if (pct < 20) { statusCls = 'status-low'; statusText = 'LOW' }
  else if (isActive) { statusCls = 'status-active'; statusText = 'ACTIVE' }

  return (
    <div class="fiq-slot-card" onClick={() => onPopup(data)}>
      <div
        class={`fiq-slot-card-color${isEmpty ? ' fiq-slot-card-color-empty' : ''}`}
        style={isEmpty ? undefined : { background: headerBg }}
      >
        <div class="fiq-slot-card-color-top">
          <span class="fiq-slot-card-label">{slotShortLabel(n)}</span>
          {statusText && <span class={`fiq-slot-status-pill ${statusCls}`}>{statusText}</span>}
        </div>
        {ambient && (
          <div class="fiq-slot-card-ambient-row">
            {ambient.drying ? (
              <span class="fiq-slot-card-ambient-pill drying">🔥 {ambient.temperature}°C · {dryTimeLabel(ambient.dryingRemainingMin)}</span>
            ) : ambient.connected ? (
              <>
                <span class="fiq-slot-card-ambient-pill">💧 {ambient.humidity}%</span>
                <span class="fiq-slot-card-ambient-pill">🌡️ {ambient.temperature}°C</span>
              </>
            ) : (
              <span class="fiq-slot-card-ambient-pill">Disconnected</span>
            )}
          </div>
        )}
      </div>
      <div class="fiq-slot-card-body">
        {isEmpty ? (
          <span class="fiq-slot-card-empty-label">{n === 8 ? 'No spool loaded' : 'Empty'}</span>
        ) : (
          <>
            <div class="fiq-slot-card-vendor">{data.vendor} · {data.filamentName}</div>
            <div class="fiq-slot-card-meta">
              <MatBadge material={data.material} />
              <span class="fiq-slot-card-remaining">{Math.round(data.remainingG || 0)}g</span>
            </div>
            <div class="fiq-slot-card-pbar"><div class="fiq-slot-card-pbar-fill" style={{ width: `${pct}%`, background: data.colorHex }} /></div>
            {profileStatus === 'verified' && <span class="fiq-slot-profile-pip fiq-slot-pip-verified" title="Profile verified">✓ Profile</span>}
            {profileStatus === 'candidate' && <span class="fiq-slot-profile-pip fiq-slot-pip-candidate" title="Profile unverified — verify in Filaments tab">? Unverified</span>}
          </>
        )}
      </div>
    </div>
  )
}

// ── SlotsSegment — row layout ─────────────────────────────────
function SlotsSegment({ onPopup, spools, isDesktop }) {
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

  const reconcileBtn = h('button', {
    class: 'fiq-btn-bind',
    onClick: handleReconcile,
    disabled: reconciling,
    style: reconciling ? { opacity: 0.6, cursor: 'default' } : undefined,
  }, reconciling ? '↻ Reconciling…' : '↺ Reconcile')

  // Desktop: grouped 2-up card sections, mirroring mobile's AMS/HT/External
  // grouping -- AMS 2 Pro's ambient reading covers 4 slots so it appears
  // once in the section header; each HT card carries its own ambient
  // reading since each HT unit maps to exactly one slot. Reconcile lives in
  // the topbar on desktop (FilamentIQCard.jsx), not here -- a full-width
  // button is a mobile pattern.
  if (isDesktop) {
    return h('div', { style: { display: 'flex', flexDirection: 'column', gap: 16 } },
      // Boxed panel: the border + header bar make it visually explicit that
      // the ambient pills belong to THIS unit's 4 slots, not the page at
      // large. HT/External below stays unwrapped -- each of those cards
      // already carries its own reading, so there's no shared ownership to
      // scope with a container.
      h('div', { class: 'fiq-slot-section-panel' },
        h('div', { class: 'fiq-slot-section-header' },
          h('span', { class: 'fiq-slot-section-title' }, 'AMS 2 Pro'),
          ams2pro.connected
            ? h('div', { class: 'fiq-ambient-readings' },
                h('span', { class: 'fiq-ambient-pill' }, `💧 ${ams2pro.humidity}%`),
                h('span', { class: 'fiq-ambient-pill' }, `🌡️ ${ams2pro.temperature}°C`),
              )
            : h('span', { class: 'fiq-ambient-disconnected' }, 'Disconnected')
        ),
        h('div', { class: 'fiq-slot-grid' },
          UNIT_SLOTS['AMS 2 Pro'].map(n => h(SlotCard, { key: n, n, data: slotByIndex(n), onPopup, spools }))
        )
      ),

      // HT Units + External share one unlabeled 2-up section: no header,
      // since each card is already self-labeled (HT1/HT2/HT3/External) and
      // -- unlike AMS 2 Pro -- neither carries a shared ambient reading that
      // would need a header to hold it. Folding External in here (rather
      // than its own section below) fills the gap HT3 would otherwise leave
      // alone in a 3-card 2-up grid.
      h('div', { class: 'fiq-slot-section' },
        h('div', { class: 'fiq-slot-grid' },
          [...htUnitNames.map(name => {
            const slotN = UNIT_SLOTS[name][0]
            return h(SlotCard, { key: slotN, n: slotN, data: slotByIndex(slotN), onPopup, spools, ambient: unitByName(name) })
          }), h(SlotCard, { key: 8, n: 8, data: slotByIndex(8), onPopup, spools })]
        )
      )
    )
  }

  return h('div', { style: { display: 'flex', flexDirection: 'column', gap: 8 } },

    h('div', { style: { display: 'flex', justifyContent: 'flex-end', padding: '0 2px 4px' } },
      reconcileBtn
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

// ── SpoolSheet — bottom-sheet chrome around the shared SpoolEditPanel ──
// Replaces the old standalone SpoolModal. All field editing, profile lookup,
// delete-confirm, and print-label logic now live in one place (SpoolEditPanel,
// exported from SpoolsTab.jsx) -- this wrapper only supplies the mobile
// bottom-sheet frame (overlay, drag handle, "Spool #N" header) and the
// identity block via SpoolEditPanel's `identity` prop, since there's no
// adjacent row here to show swatch/name the way SpoolsTab's row does.
//
// Note: the old SpoolModal fired label.print fire-and-forget with no
// success/failure feedback; this wrapper uses useSpoolPrintActions, the same
// awaited+toast pattern SpoolsTab's row-level print button already uses --
// bringing this path in line with that one rather than the other way round.
function SpoolSheet({ spool, updateSpool, deleteSpool, onClose, onCloseAll }) {
  const { printingLabel, printingNiimbotLabel, toast, handlePrintLabel, handlePrintSwatchLabel } = useSpoolPrintActions(spool)

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

      h(SpoolEditPanel, {
        spool,
        identity: true,
        onSave: (id, patch) => updateSpool(id, patch).then(() => onClose()),
        onCancel: onClose,
        onDelete: (id) => deleteSpool(id).then(() => onCloseAll()),
        onPrintLabel: handlePrintLabel,
        onPrintSwatchLabel: handlePrintSwatchLabel,
        printingLabel,
        printingNiimbotLabel,
      }),
    )
  )
}

// ── SlotPopupContent — shared slot detail body: identity block (tappable
// to open the bound spool), spool-select picker, "Assign & bind". Used by
// both the mobile bottom-sheet overlay (SlotPopup) and the desktop right
// DetailPanel (SlotDetailPanel) -- same content, different chrome, per the
// desktop layout spec ("same panel component for both slot and spool
// contexts").
function SlotPopupContent({ popup, spools, onOpenSpool, onAssigned }) {
  const provider = useProvider()
  const [pendingOption, setPendingOption] = useState(null)

  const spoolId = parseInt(popup.spoolId, 10)
  const spool = spools?.find(s => s.id === spoolId)
  const canEditSpool = popup.status === 'ok' && popup.spoolId && popup.spoolId !== '—' && popup.spoolId !== 'unavailable' && popup.spoolId !== 'unknown' && spool

  const selectSpool = option => {
    setPendingOption(option)
    provider?.rpc('slot.selectSpool', { index: popup.index, option })
  }

  const assignAndBind = () => {
    provider?.rpc('slot.assignAndBind', { slot: popup.index })
    onAssigned?.()
  }

  const options = popup.spoolOptions || []
  const displaySelected = pendingOption ?? popup.selectedOption

  const pct = Math.min(100, Math.round((popup.remainingG || 0) / 1000 * 100))
  const popupMultiHexes = spool?.filament?.multi_color_hexes || null
  const popupSwatchBg = popupMultiHexes && popupMultiHexes.split(',').length >= 2
    ? (() => { const cols = popupMultiHexes.split(',').map(h => `#${h.trim().replace('#','')}`); return `linear-gradient(135deg, ${cols[0]} 50%, ${cols[1]} 50%)` })()
    : popup.colorHex

  return h('div', null,
    h('div', { style: S.popupHeader },
      h('div', { style: S.popupUnit }, `Slot ${popup.index}`),
      h('div', { style: S.popupTitle },
        popup.status === 'needs_bind' ? 'Binding Required'
          : popup.status === 'empty' ? (popup.index === 8 ? 'No spool loaded' : 'Empty')
          : `${popup.vendor} · ${popup.material}`
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
      onClick: canEditSpool ? () => onOpenSpool(spool) : undefined,
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
  )
}

// ── SlotPopup — mobile bottom-sheet overlay wrapper ─────────
function SlotPopup({ popup, onClose, spools, updateSpool, deleteSpool }) {
  const [spoolModal, setSpoolModal] = useState(false)
  const spoolId = parseInt(popup.spoolId, 10)
  const spool = spools?.find(s => s.id === spoolId)
  const canEditSpool = popup.status === 'ok' && popup.spoolId && popup.spoolId !== '—' && popup.spoolId !== 'unavailable' && popup.spoolId !== 'unknown' && spool

  return h('div', {
    style: S.popupOverlay,
    onClick: e => { if (e.target === e.currentTarget) onClose() },
  },
    h('div', { style: S.popupSheet },
      h('div', { style: S.popupDrag }),
      h(SlotPopupContent, { popup, spools, onOpenSpool: () => setSpoolModal(true), onAssigned: onClose }),
    ),

    // SpoolSheet renders above the slot popup sheet
    spoolModal && canEditSpool && h(SpoolSheet, {
      spool,
      updateSpool,
      deleteSpool,
      onClose: () => setSpoolModal(false),
      onCloseAll: () => { setSpoolModal(false); onClose() },
    }),
  )
}

// ── SlotDetailPanel — desktop right-panel content for a selected slot.
// Same SlotPopupContent body as mobile, no overlay chrome; tapping the bound
// spool routes to the spool context (onOpenSpool) instead of opening another
// sheet, since there's only one persistent detail panel at desktop widths.
export function SlotDetailPanel({ popup, spools, onOpenSpool }) {
  return h(SlotPopupContent, { popup, spools, onOpenSpool })
}

// ── Default export: wires SlotsSegment + SlotPopup ──────────
// At desktop widths (isDesktop + onSelectSlot supplied by FilamentIQCard),
// a slot click routes to the parent's selected-entity state -> right
// DetailPanel instead of opening the local mobile overlay. Falls back to
// the mobile popup whenever onSelectSlot isn't wired up (e.g. the HA card /
// printer-dashboard surfaces, which stay mobile-only and never pass it).
export default function SlotsTab({ spools, updateSpool, deleteSpool, isDesktop, onSelectSlot }) {
  const [popup, setPopup] = useState(null)
  const handlePopup = (data) => {
    if (isDesktop && onSelectSlot) {
      onSelectSlot(data)
    } else {
      setPopup(data)
    }
  }
  return h('div', { style: { position: 'relative' } },
    h(SlotsSegment, { onPopup: handlePopup, spools, isDesktop }),
    popup && h(SlotPopup, { popup, onClose: () => setPopup(null), spools, updateSpool, deleteSpool })
  )
}
