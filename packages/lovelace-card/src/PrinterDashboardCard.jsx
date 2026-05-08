import { h, Component } from 'preact'
import { useState } from 'preact/hooks'

function CameraView({ getHass }) {
  const hass = getHass()
  const printerOn = hass?.states?.['switch.officeoutlet01_3dprinter']?.state === 'on'
  const token = hass?.auth?.data?.access_token

  const camStyle = {
    width: '100%',
    aspectRatio: '16/9',
    objectFit: 'cover',
    borderRadius: 8,
    display: 'block',
    background: '#0a0a0a',
  }

  const labelStyle = {
    fontSize: 8,
    color: '#2c2c2e',
    marginTop: 3,
    textAlign: 'center',
  }

  const tapoCam = `/api/camera_proxy/camera.tapo_c111_live_view?token=${token}&t=${Date.now()}`
  const bambuCam = `/api/camera_proxy/camera.p1s_01p00c5a3101668_camera?token=${token}&t=${Date.now()}`

  return h('div', { style: { display: 'flex', flexDirection: 'column', gap: 5, marginBottom: 8 } },
    h('div', null,
      h('img', {
        src: tapoCam,
        style: camStyle,
        alt: 'Tapo office',
        onError: e => { e.target.style.display = 'none' },
      }),
      h('div', { style: labelStyle }, 'Tapo · office')
    ),
    printerOn && h('div', null,
      h('img', {
        src: bambuCam,
        style: camStyle,
        alt: 'Bambu chamber',
        onError: e => { e.target.style.display = 'none' },
      }),
      h('div', { style: labelStyle }, 'Bambu · chamber')
    )
  )
}

// ── MDI SVG paths — inline, no external dependency ──────────
const ICONS = {
  printer:      'M6,2A2,2 0 0,0 4,4V10A2,2 0 0,0 6,12H10A2,2 0 0,0 12,10V4A2,2 0 0,0 10,2H6M6,4H10V10H6V4M4,14A2,2 0 0,0 2,16V22H22V16A2,2 0 0,0 20,14H4M6,16H18V20H6V16Z',
  slots:        'M2,2H8V8H2V2M10,2H16V8H10V2M18,2H22V8H18V2M2,10H8V16H2V10M10,10H16V16H10V10M18,10H22V16H18V10M2,18H8V22H2V18M10,18H16V22H10V18M18,18H22V22H18V18Z',
  brain:        'M13,3A9,9 0 0,0 4,12H2A11,11 0 0,1 13,1V3M13,21V23A11,11 0 0,0 24,12H22A9,9 0 0,1 13,21M4,12A9,9 0 0,0 13,21V23A11,11 0 0,1 2,12H4M22,12A9,9 0 0,0 13,3V1A11,11 0 0,1 24,12H22Z',
  clock:        'M12,20A8,8 0 0,0 20,12A8,8 0 0,0 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20M12,2A10,10 0 0,1 22,12A10,10 0 0,1 12,22C6.47,22 2,17.5 2,12A10,10 0 0,1 12,2M12.5,7V12.25L17,14.92L16.25,16.15L11,13V7H12.5Z',
  layers:       'M17,17H7V7H17M21,11V9H19V7C19,5.89 18.1,5 17,5H15V3H13V5H11V3H9V5H7C5.89,5 5,5.89 5,7V9H3V11H5V13H3V15H5V17A2,2 0 0,0 7,19H9V21H11V19H13V21H15V19H17A2,2 0 0,0 19,17V15H21V13H19V11M17,17H7V7H17V17Z',
  nozzle:       'M7,2H17V8H19V13H16.5L13,17H11L7.5,13H5V8H7V2M10,22H2V20H10A1,1 0 0,0 11,19V18H13V19A3,3 0 0,1 10,22Z',
  fan:          'M12,11A1,1 0 0,0 11,12A1,1 0 0,0 12,13A1,1 0 0,0 13,12A1,1 0 0,0 12,11M12.5,2C17,2 17.11,5.57 14.75,6.75C13.76,7.24 13.32,8.29 13.13,9.22C13.61,9.42 14.03,9.73 14.35,10.13C18.05,8.13 22.03,8.92 22.03,12.5C22.03,17 18.46,17.1 17.28,14.73C16.78,13.74 15.72,13.3 14.79,13.11C14.59,13.59 14.28,14 13.88,14.34C15.87,18.03 15.08,22 11.5,22C7,22 6.91,18.42 9.27,17.24C10.25,16.75 10.69,15.71 10.89,14.79C10.4,14.59 9.97,14.27 9.65,13.87C5.96,15.85 2,15.07 2,11.5C2,7 5.56,6.89 6.74,9.26C7.24,10.25 8.29,10.68 9.22,10.87C9.41,10.39 9.73,9.97 10.14,9.65C8.15,5.96 8.94,2 12.5,2Z',
  wifi:         'M12,21L15.6,16.2C14.6,15.45 13.35,15 12,15C10.65,15 9.4,15.45 8.4,16.2L12,21M12,3C7.95,3 4.21,4.34 1.2,6.6L3,9C5.5,7.12 8.62,6 12,6C15.38,6 18.5,7.12 21,9L22.8,6.6C19.79,4.34 16.05,3 12,3M12,9C9.3,9 6.81,9.89 4.8,11.4L6.6,13.8C8.1,12.67 9.97,12 12,12C14.03,12 15.9,12.67 17.4,13.8L19.2,11.4C17.19,9.89 14.7,9 12,9Z',
  refresh:      'M17.65,6.35C16.2,4.9 14.21,4 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20C15.73,20 18.84,17.45 19.73,14H17.65C16.83,16.33 14.61,18 12,18A6,6 0 0,1 6,12A6,6 0 0,1 12,6C13.66,6 15.14,6.69 16.22,7.78L13,11H20V4L17.65,6.35Z',
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

function hexToRgb(hex) {
  const h = (hex || '').replace('#', '')
  if (h.length < 6) return '128,128,128'
  const r = parseInt(h.substr(0, 2), 16)
  const g = parseInt(h.substr(2, 2), 16)
  const b = parseInt(h.substr(4, 2), 16)
  return `${r},${g},${b}`
}

// ── Segment bar ──────────────────────────────────────────────
function SegBar({ active, onSwitch }) {
  const segs = [
    { id: 'printer', label: 'Printer',     icon: ICONS.printer },
    { id: 'slots',   label: 'Slots',       icon: ICONS.slots },
    { id: 'fiq',     label: 'Filament IQ', icon: ICONS.brain },
  ]
  return h('div', { style: S.segBar },
    segs.map(s =>
      h('div', {
        key: s.id,
        style: { ...S.seg, ...(active === s.id ? S.segActive : {}) },
        onClick: () => onSwitch(s.id),
      },
        h(Icon, { path: s.icon, size: 14, color: active === s.id ? '#6aabda' : '#444' }),
        h('span', { style: { ...S.segLabel, color: active === s.id ? '#6aabda' : '#444' } }, s.label)
      )
    )
  )
}

// ── Printer segment ──────────────────────────────────────────
function PrinterSegment({ getHass }) {
  const hass = getHass()
  const sv = id => hass?.states?.[id]?.state ?? '—'
  const sa = (id, attr) => hass?.states?.[id]?.attributes?.[attr]

  const status    = sv('sensor.p1s_01p00c5a3101668_print_status')
  const progress  = parseFloat(sv('sensor.p1s_01p00c5a3101668_print_progress')) || 0
  const remaining = parseFloat(sv('sensor.p1s_01p00c5a3101668_remaining_time')) || 0
  const curLayer  = sv('sensor.p1s_01p00c5a3101668_current_layer')
  const totalLayer = sv('sensor.p1s_01p00c5a3101668_total_layer_count')
  const taskName  = sv('sensor.p1s_01p00c5a3101668_task_name')
  const stage     = sv('sensor.p1s_01p00c5a3101668_current_stage')
  const imgUrl    = sa('image.p1s_01p00c5a3101668_cover_image', 'entity_picture')

  const isPrinting = status === 'running'
  const isPaused   = status === 'pause'
  const isActive   = isPrinting || isPaused

  const statusColor = isPrinting ? '#6aabda' : isPaused ? '#ff9800' : '#555'
  const statusLabel = isPrinting ? 'PRINTING'
    : isPaused   ? 'PAUSED'
    : status === 'finish' ? 'FINISHED'
    : status === 'failed' ? 'FAILED'
    : (status || 'IDLE').toUpperCase()

  const remH   = Math.floor(remaining)
  const remM   = Math.round((remaining - remH) * 60)
  const remStr = remH > 0 ? `${remH}h ${remM}m` : `${remM}m`

  const tray        = hass?.states?.['sensor.p1s_01p00c5a3101668_ams_1_tray_2']
  const activeColor = tray?.attributes?.color
    ? '#' + tray.attributes.color.replace('#', '').substring(0, 6) : '#888'
  const activeType  = tray?.attributes?.type ?? ''
  const remainG     = sv('sensor.ams_slot_2_remaining_g')
  const vendor      = sv('sensor.ams_slot_2_vendor')
  const spoolId     = sv('input_text.ams_slot_2_spool_id')
  const activeName  = tray?.attributes?.name ?? '—'

  const nozzle    = Math.round(parseFloat(sv('sensor.p1s_01p00c5a3101668_nozzle_temperature')) || 0)
  const nozzleTgt = Math.round(parseFloat(sv('sensor.p1s_01p00c5a3101668_nozzle_target_temperature')) || 0)
  const bed       = Math.round(parseFloat(sv('sensor.p1s_01p00c5a3101668_bed_temperature')) || 0)
  const bedTgt    = Math.round(parseFloat(sv('sensor.p1s_01p00c5a3101668_bed_target_temperature')) || 0)
  const chamberPct = sa('fan.p1s_01p00c5a3101668_chamber_fan', 'percentage') ?? 0
  const chamberOn  = sv('fan.p1s_01p00c5a3101668_chamber_fan') === 'on'
  const coolingPct = sa('fan.p1s_01p00c5a3101668_cooling_fan', 'percentage') ?? 0
  const coolingOn  = sv('fan.p1s_01p00c5a3101668_cooling_fan') === 'on'
  const rssi       = parseInt(sv('sensor.p1s_01p00c5a3101668_wi_fi_signal')) || 0
  const wifiLabel  = rssi >= -50 ? 'Excellent' : rssi >= -60 ? 'Good' : rssi >= -70 ? 'Fair' : rssi >= -80 ? 'Weak' : 'Poor'
  const wifiColor  = rssi >= -60 ? '#4caf50' : rssi >= -70 ? '#ff9800' : '#f44336'
  const hours      = Math.round(parseFloat(sv('sensor.p1s_01p00c5a3101668_total_usage')) || 0)

  const printerOn  = sv('switch.officeoutlet01_3dprinter') === 'on'
  const lightOn    = sv('light.p1s_01p00c5a3101668_chamber_light') === 'on'
  const purifierOn = sv('fan.office_air_purifier') !== 'off'
  const bentoOn    = sv('switch.officeoutlet_02') === 'on'

  const call = (domain, service, data) => hass?.callService(domain, service, data)

  const togglePrinter = () => {
    if (window.confirm('Toggle printer power?'))
      call('switch', 'toggle', { entity_id: 'switch.officeoutlet01_3dprinter' })
  }

  return h('div', null,

    // Print hero card
    h('div', { style: S.card },
      h('div', { style: { display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 12 } },
        imgUrl && isActive && h('img', {
          src: imgUrl,
          style: S.coverImg,
          onError: e => { e.target.style.display = 'none' },
        }),
        h('div', { style: { flex: 1, minWidth: 0 } },
          h('div', { style: S.statusBadge(statusColor) },
            h('div', { style: { ...S.statusDot, background: statusColor } }),
            h('span', { style: { ...S.statusText, color: statusColor } }, statusLabel)
          ),
          h('div', { style: S.taskName }, taskName),
          h('div', { style: S.stageName }, stage?.replace(/_/g, ' ') || '—'),
          isActive && h('div', null,
            h('div', { style: S.progBar },
              h('div', { style: { ...S.progFill, width: `${Math.min(progress, 100)}%` } })
            ),
            h('div', { style: S.progRow },
              h('span', { style: S.progPct }, `${Math.round(progress)}%`),
              h('div', { style: S.chips },
                h('div', { style: S.chip },
                  h(Icon, { path: ICONS.clock, size: 9, color: '#6aabda' }),
                  remStr
                ),
                h('div', { style: S.chip },
                  h(Icon, { path: ICONS.layers, size: 9, color: '#6aabda' }),
                  `${curLayer} / ${totalLayer}`
                )
              )
            )
          )
        )
      ),
      h('div', { style: S.swatchRow },
        h('div', { style: { ...S.swatch, background: activeColor } }),
        h('div', { style: { flex: 1, minWidth: 0 } },
          h('div', { style: S.swatchName }, `${vendor} · ${activeName}`),
          h('div', { style: S.swatchSub },
            [activeType, 'Slot 2', spoolId && spoolId !== '—' ? `#${spoolId}` : ''].filter(Boolean).join(' · ')
          )
        ),
        h('div', { style: { textAlign: 'right' } },
          h('div', { style: S.swatchRemain },
            `${Math.round(sa('sensor.p1s_01p00c5a3101668_ams_1_tray_2', 'remain') || 0)}%`
          ),
          remainG && remainG !== '—' &&
            h('div', { style: S.swatchG }, `${Math.round(parseFloat(remainG))}g`)
        )
      )
    ),

    // Controls row
    h('div', { style: S.ctrlGrid },
      [
        { label: 'Power',    on: printerOn,  onClick: togglePrinter,                                                                      icon: '⏻' },
        { label: 'Light',    on: lightOn,    onClick: () => call('light',  'toggle', { entity_id: 'light.p1s_01p00c5a3101668_chamber_light' }), icon: '💡' },
        { label: 'Purifier', on: purifierOn, onClick: () => call('fan',    'toggle', { entity_id: 'fan.office_air_purifier' }),              icon: '💨' },
        { label: 'Bento',    on: bentoOn,    onClick: () => call('switch', 'toggle', { entity_id: 'switch.officeoutlet_02' }),               icon: '📦' },
      ].map(b =>
        h('div', {
          key: b.label,
          style: { ...S.ctrlBtn, ...(b.on ? S.ctrlBtnOn : {}) },
          onClick: b.onClick,
        },
          h('span', { style: { fontSize: 16 } }, b.icon),
          h('span', { style: { ...S.ctrlName, color: b.on ? '#1c1c1e' : '#555' } }, b.label)
        )
      )
    ),

    // Cameras
    h(CameraView, { getHass }),

    // Vitals card
    h('div', { style: S.card },
      h('div', { style: S.sectionLabel }, 'Printer status'),
      h('div', { style: S.vitalsGrid },
        [
          { label: 'Nozzle',  value: `${nozzle}° / ${nozzleTgt}°`, color: nozzleTgt > 0 ? '#e8784a' : '#555', icon: ICONS.nozzle },
          { label: 'Bed',     value: `${bed}° / ${bedTgt}°`,       color: bedTgt > 0    ? '#e8784a' : '#555', icon: ICONS.nozzle },
          { label: 'Chamber', value: chamberOn ? `${chamberPct}%` : 'Off', color: chamberOn ? '#6aabda' : '#444', icon: ICONS.fan },
          { label: 'Cooling', value: coolingOn ? `${coolingPct}%` : 'Off', color: coolingOn ? '#6aabda' : '#444', icon: ICONS.fan },
          { label: 'WiFi',    value: wifiLabel, color: wifiColor, icon: ICONS.wifi },
          { label: 'Hours',   value: `${hours}h`, color: '#aaa', icon: ICONS.clock },
        ].map(v =>
          h('div', { key: v.label, style: S.vitalRow },
            h('div', { style: S.vitalLeft },
              h(Icon, { path: v.icon, size: 11, color: v.color }),
              h('span', { style: S.vitalLabel }, v.label)
            ),
            h('span', { style: { ...S.vitalValue, color: v.color } }, v.value)
          )
        )
      )
    ),

    // Reconcile action
    h('div', {
      style: S.actionBtn,
      onClick: () => call('script', 'turn_on', { entity_id: 'script.reconcile_all_ams_slots' }),
    },
      h(Icon, { path: ICONS.refresh, size: 14, color: '#3b9fd8', extraStyle: { opacity: 0.6 } }),
      h('span', { style: S.actionLabel }, 'Reconcile')
    )
  )
}

// ── Slots segment ────────────────────────────────────────────
const AMS_UNITS = [
  {
    name: 'AMS 2 Pro',
    humEntity:  'sensor.p1s_01p00c5a3101668_ams_1_humidity',
    tempEntity: 'sensor.p1s_01p00c5a3101668_ams_1_temperature',
    dryEntity:  'binary_sensor.p1s_01p00c5a3101668_ams_1_drying',
    slots: [1, 2, 3, 4],
  },
  {
    name: 'AMS HT 1',
    humEntity:  'sensor.p1s_01p00c5a3101668_ams_128_humidity',
    tempEntity: 'sensor.p1s_01p00c5a3101668_ams_128_temperature',
    dryEntity:  'binary_sensor.p1s_01p00c5a3101668_ams_128_drying',
    slots: [5],
  },
  {
    name: 'AMS HT 2',
    humEntity:  'sensor.p1s_01p00c5a3101668_ams_129_humidity',
    tempEntity: 'sensor.p1s_01p00c5a3101668_ams_129_temperature',
    dryEntity:  'binary_sensor.p1s_01p00c5a3101668_ams_129_drying',
    slots: [6],
  },
  {
    name: 'AMS HT 3',
    humEntity:  'sensor.p1s_01p00c5a3101668_ams_130_humidity',
    tempEntity: 'sensor.p1s_01p00c5a3101668_ams_130_temperature',
    dryEntity:  'binary_sensor.p1s_01p00c5a3101668_ams_130_drying',
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

  const secondaryLabel = d => {
    if (d.status === 'empty') return 'No spool loaded'
    const r = d.reason
    if (r.includes('RFID_NOT_REFRESHED')) return `${d.name} · RFID not refreshed`
    if (d.status === 'needs_bind')        return 'Tap to select spool'
    const g = parseFloat(d.g) || 0
    return `${d.name} · #${d.id} · ${Math.round(g)}g`
  }

  const slotStateStyle = d => {
    if (d.isActive && d.status === 'ok')      return { background: 'rgba(255,255,255,0.06)' }
    if (d.status === 'empty')                 return { opacity: 0.35 }
    if (d.reason?.includes('RFID_NOT_REFRESHED')) return { background: 'rgba(255,152,0,0.05)' }
    if (d.status === 'needs_bind')            return { background: 'rgba(239,83,80,0.05)' }
    return {}
  }

  const weightPct = d => {
    const g = parseFloat(d.g) || 0
    return Math.min(100, Math.max(0, Math.round(g / 1000 * 100)))
  }

  const SlotRow = ({ n, last = false }) => {
    const d = slotData(n)
    const pct = weightPct(d)
    const isNeedsAction = d.status === 'needs_bind' || d.reason?.includes('RFID_NOT_REFRESHED')
    return h('div', {
      style: { ...S.slotRow, ...(last ? { borderBottom: 'none' } : {}), ...slotStateStyle(d) },
      onClick: () => d.status !== 'empty' && onPopup(slotData(n)),
    },
      d.status === 'empty'
        ? h('div', { style: { ...S.slotDot, background: '#1c1c1e', border: '1px solid #3a3a3c', display: 'flex', alignItems: 'center', justifyContent: 'center' } },
            h(Icon, { path: ICONS.cancel, size: 12, color: '#444' })
          )
        : h('div', { style: { ...S.slotDot, background: d.color } }),
      h('div', { style: { flex: 1, minWidth: 0 } },
        h('div', { style: { ...S.slotPrimary, ...(isNeedsAction ? { color: '#ef5350' } : d.isActive && d.status === 'ok' ? { color: d.color } : {}) } },
          primaryLabel(d)
        ),
        h('div', { style: { ...S.slotSecondary, ...(isNeedsAction ? { color: '#a04040' } : {}) } },
          secondaryLabel(d)
        )
      ),
      d.isActive && d.status === 'ok' && h(Icon, { path: ICONS.nozzle, size: 12, color: '#63cab7' }),
      isNeedsAction                   && h(Icon, { path: ICONS.alert,   size: 12, color: '#ef5350' }),
      d.status !== 'empty'            && h(Icon, { path: ICONS.chevron, size: 11, color: '#555' }),
      d.status !== 'empty' && h('div', { style: S.slotBar },
        h('div', { style: { ...S.slotBarFill, width: `${pct}%` } })
      )
    )
  }

  return h('div', null,

    AMS_UNITS.map(unit => {
      const hum       = sv(unit.humEntity)
      const temp      = sv(unit.tempEntity)
      const connected = !['unavailable', 'unknown', '—'].includes(hum)

      return h('div', { key: unit.name, style: S.fiqCard },
        h('div', { style: S.unitHeader },
          h(Icon, { path: ICONS.printer, size: 14, color: connected ? '#4caf50' : '#ef5350' }),
          h('div', null,
            h('div', { style: S.unitName }, unit.name),
            h('div', { style: S.unitSub }, connected ? `${hum}% humidity · ${temp}°C` : 'Disconnected')
          )
        ),
        unit.slots.map((n, i) =>
          h(SlotRow, { key: n, n, last: i === unit.slots.length - 1 && !unit.external })
        ),
        unit.external && h('div', null,
          h('div', { style: { ...S.unitHeader, borderTop: '1px solid rgba(255,255,255,0.06)', borderBottom: 'none' } },
            h(Icon, { path: ICONS.externalLink, size: 14, color: '#4caf50' }),
            h('div', { style: S.unitName }, 'External')
          ),
          h(SlotRow, { n: 8, last: true })
        )
      )
    })
  )
}

// ── Slot popup (rendered outside scrollArea at root level) ───
function SlotPopup({ popup, getHass, onClose }) {
  const hass = getHass()

  const selectSpool = option => {
    hass?.callService('input_select', 'select_option', {
      entity_id: popup.selectEntity,
      option,
    })
  }

  const assignAndBind = () => {
    hass?.callService('script', 'turn_on', {
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
      h('div', { style: S.popupSec }, 'Select spool'),
      h('div', { style: S.pickerList, onTouchMove: e => e.stopPropagation() },
        options.length === 0
          ? h('div', { style: { padding: '16px', fontSize: 12, color: '#555', textAlign: 'center' } },
              'No spools available — run Reconcile'
            )
          : options.map(option =>
              h('div', {
                key: option,
                style: { ...S.pickerRow, ...(option === currentOption ? S.pickerRowSelected : {}) },
                onClick: () => selectSpool(option),
              },
                h('div', { style: { ...S.pickerLabel, color: option === currentOption ? '#6aabda' : '#e8e8ea' } }, option),
                option === currentOption && h(Icon, { path: ICONS.chevron, size: 14, color: '#6aabda' })
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

// ── Filament IQ segment ──────────────────────────────────────
class FiqSegment extends Component {
  constructor(props) {
    super(props)
    this.containerRef = null
    this._card = null
  }

  componentDidMount() {
    if (!this.containerRef) return
    try {
      const card = document.createElement('filament-iq-manager')
      card.setConfig({})
      card.hass = this.props.getHass()
      this.containerRef.appendChild(card)
      this._card = card
    } catch (e) {
      console.warn('[printer-dashboard] FiqSegment mount failed:', e)
    }
  }

  componentDidUpdate() {
    if (this._card) this._card.hass = this.props.getHass()
  }

  componentWillUnmount() {
    if (this._card) {
      this._card.remove()
      this._card = null
    }
  }

  render() {
    return h('div', {
      ref: el => { this.containerRef = el },
      style: { width: '100%', minHeight: 200 },
    })
  }
}

// ── Root component ───────────────────────────────────────────
export function PrinterDashboardCard({ config, getHass }) {
  const [seg, setSeg] = useState('printer')
  const [popup, setPopup] = useState(null)

  return h('div', { style: S.root },
    h(SegBar, { active: seg, onSwitch: setSeg }),
    h('div', { style: S.scrollArea },
      seg === 'printer' && h(PrinterSegment, { getHass }),
      seg === 'slots'   && h(SlotsSegment,   { getHass, onPopup: setPopup }),
      seg === 'fiq'     && h(FiqSegment, { getHass }),
    ),
    popup && h(SlotPopup, { popup, getHass, onClose: () => setPopup(null) })
  )
}

// ── Style object ─────────────────────────────────────────────
const S = {
  root:         { display: 'flex', flexDirection: 'column', height: '100%', background: '#111', fontFamily: '-apple-system,sans-serif', overflow: 'hidden', position: 'relative' },
  segBar:       { display: 'flex', gap: 5, padding: '6px 8px', background: '#1c1c1e', borderBottom: '1px solid #2c2c2e', flexShrink: 0 },
  seg:          { flex: 1, borderRadius: 8, padding: '7px 4px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, cursor: 'pointer', transition: 'background 0.15s' },
  segActive:    { background: '#2c2c2e' },
  segLabel:     { fontSize: 10, fontWeight: 500, whiteSpace: 'nowrap' },
  scrollArea:   { flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: 10 },
  card:         { background: '#1c1c1e', border: '1px solid #2c2c2e', borderRadius: 12, padding: 12, marginBottom: 8, overflow: 'hidden' },
  coverImg:     { width: 72, height: 72, borderRadius: 8, objectFit: 'cover', flexShrink: 0, background: '#2c2c2e' },
  statusBadge:  c => ({ display: 'inline-flex', alignItems: 'center', gap: 5, background: `rgba(${hexToRgb(c)},0.12)`, border: `1px solid rgba(${hexToRgb(c)},0.2)`, borderRadius: 5, padding: '2px 8px', marginBottom: 7 }),
  statusDot:    { width: 5, height: 5, borderRadius: '50%' },
  statusText:   { fontSize: 10, letterSpacing: '0.06em' },
  taskName:     { fontSize: 13, fontWeight: 500, color: '#f5f5f5', marginBottom: 2 },
  stageName:    { fontSize: 10, color: '#555', marginBottom: 8, textTransform: 'capitalize' },
  progBar:      { height: 4, background: '#2c2c2e', borderRadius: 2, marginBottom: 6 },
  progFill:     { height: 4, background: '#6aabda', borderRadius: 2, transition: 'width 0.3s' },
  progRow:      { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  progPct:      { fontSize: 14, fontWeight: 500, color: '#6aabda' },
  chips:        { display: 'flex', gap: 4 },
  chip:         { background: 'rgba(106,171,218,0.12)', border: '1px solid rgba(106,171,218,0.25)', borderRadius: 5, padding: '3px 6px', display: 'flex', alignItems: 'center', gap: 3, fontSize: 10, color: '#6aabda', fontWeight: 500 },
  swatchRow:    { display: 'flex', alignItems: 'center', gap: 8, paddingTop: 8, borderTop: '1px solid #2c2c2e' },
  swatch:       { width: 26, height: 26, borderRadius: 4, flexShrink: 0, border: '2px solid rgba(255,255,255,0.25)' },
  swatchName:   { fontSize: 11, fontWeight: 500, color: '#e8e8ea' },
  swatchSub:    { fontSize: 9, color: '#555', marginTop: 1 },
  swatchRemain: { fontSize: 11, color: '#aaa' },
  swatchG:      { fontSize: 9, color: '#555' },
  ctrlGrid:     { display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 5, marginBottom: 8 },
  ctrlBtn:      { background: '#232323', border: '1px solid #2a2a2a', borderRadius: 8, padding: '8px 4px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, cursor: 'pointer', transition: 'all 0.15s' },
  ctrlBtnOn:    { background: '#f5f5f7', borderColor: '#d8d8da' },
  ctrlName:     { fontSize: 9, textAlign: 'center' },
  sectionLabel: { fontSize: 8, color: '#444', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 7 },
  vitalsGrid:   { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 },
  vitalRow:     { background: '#2c2c2e', borderRadius: 6, padding: '6px 8px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
  vitalLeft:    { display: 'flex', alignItems: 'center', gap: 5 },
  vitalLabel:   { fontSize: 9, color: '#888' },
  vitalValue:   { fontSize: 10, fontWeight: 500 },
  actionBtn:    { background: '#2c2c2e', border: '1px solid #3a3a3c', borderRadius: 9, padding: '9px 11px', display: 'flex', alignItems: 'center', gap: 7, marginBottom: 8, cursor: 'pointer' },
  actionLabel:  { fontSize: 11, color: '#e8e8ea' },
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
  popupOverlay: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', zIndex: 9999 },
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
  ddRow:        { margin: '0 16px 10px', background: '#2c2c2e', border: '1px solid #3a3a3c', borderRadius: 9, padding: '11px 13px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer' },
  ddVal:        { fontSize: 13, color: '#e8e8ea', fontWeight: 500 },
  ddSub:        { fontSize: 10, color: '#555', marginTop: 3 },
  assignBtn:    { margin: '4px 16px 16px', background: 'rgba(106,171,218,0.12)', border: '1px solid rgba(106,171,218,0.3)', borderRadius: 10, padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 9, cursor: 'pointer' },
  assignLabel:  { fontSize: 13, color: '#6aabda', fontWeight: 500 },
  pickerList:   { maxHeight: 360, overflowY: 'auto', overflowX: 'hidden', WebkitOverflowScrolling: 'touch', margin: '0 0 8px 0', borderTop: '1px solid rgba(255,255,255,0.06)' },
  pickerRow:    { padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 8, borderBottom: '1px solid rgba(255,255,255,0.04)', cursor: 'pointer' },
  pickerRowSelected: { background: 'rgba(106,171,218,0.08)' },
  pickerLabel:  { fontSize: 12, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 },
}
