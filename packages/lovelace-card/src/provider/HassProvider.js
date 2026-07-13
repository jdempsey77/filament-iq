import { ProxyClient } from '../api/proxy'

// Confirmed via git history (home_assistant repo, appdaemon/apps/apps.yaml)
// on 2026-07-12: filament_profile_lookup.py was added 2026-05-23 (commit
// 98f1684) but only apps.yaml.example was updated, never the real
// apps.yaml -- no AppDaemon listener has ever existed for these event
// types. Fail fast instead of waiting out fireAndAwait's full 10-30s
// timeout for a response that structurally cannot arrive. Remove from
// this set (not the case blocks below, which stay correct and ready)
// once the app is actually registered.
const UNAVAILABLE_VERBS = new Set([
  'filament.profileLookup',
  'filament.profileVerify',
  'filament.profileBulkStatus',
])

function generateId() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return Math.random().toString(36).slice(2)
}

// Fires an HA event and waits for a correlated response event, mirroring the
// subscribe/correlate/timeout dance every call site used to hand-roll via
// hass.connection directly.
function fireAndAwait(getHass, { requestType, eventData, responseType, matchKey, matchValue, timeoutMs }) {
  return new Promise((resolve, reject) => {
    const hass = getHass()
    if (!hass?.connection) {
      reject(new Error(`${requestType}: hass not available`))
      return
    }
    let done = false
    let unsub = null
    let timer = null
    const cleanup = () => {
      if (timer) { clearTimeout(timer); timer = null }
      if (unsub) { unsub(); unsub = null }
    }

    hass.connection
      .subscribeEvents((event) => {
        const d = event.data || {}
        if (done || d[matchKey] !== matchValue) return
        done = true
        cleanup()
        resolve(d)
      }, responseType)
      .then((u) => {
        unsub = u
        hass.connection.sendMessage({ type: 'fire_event', event_type: requestType, event_data: eventData })
        if (timeoutMs) {
          timer = setTimeout(() => {
            if (done) return
            done = true
            cleanup()
            reject(new Error(`${requestType} timed out`))
          }, timeoutMs)
        }
      })
      .catch((e) => {
        cleanup()
        reject(e)
      })
  })
}

// Fires an HA event with no response event to wait for.
function fireEvent(getHass, eventType, eventData) {
  const hass = getHass()
  if (!hass?.connection) return
  hass.connection.sendMessage({ type: 'fire_event', event_type: eventType, event_data: eventData })
}

// Printer serial comes from card config, fed in by main.jsx via getSerial.
// This default is only a fallback when config omits it.
const DEFAULT_SERIAL = '01p00c5b2201397'

// Slot -> AMS unit identity, and the ams_index/tray_index pair HA reports on
// sensor.p1s_<serial>_active_tray when that slot is the one loaded in the
// printer. Canonical mapping documented in d5-automation/ecosystem.yaml.
// Duplicated here rather than sourced at runtime — this is a static browser
// bundle with no build-time YAML read — but centralized to this ONE copy
// (PrinterDashboardCard.jsx keeps its own, out of scope for this session).
// Slot 8 (external) is given a sentinel ams/tray pair that active_tray can
// never report, since the external port can't be "active" via AMS tracking.
const SLOT_UNIT = {
  1: { unit: 'AMS 2 Pro', ams: 0,   tray: 0 },
  2: { unit: 'AMS 2 Pro', ams: 0,   tray: 1 },
  3: { unit: 'AMS 2 Pro', ams: 0,   tray: 2 },
  4: { unit: 'AMS 2 Pro', ams: 0,   tray: 3 },
  5: { unit: 'HT1',       ams: 128, tray: 0 },
  6: { unit: 'HT2',       ams: 129, tray: 0 },
  7: { unit: 'HT3',       ams: 130, tray: 0 },
  8: { unit: 'external',  ams: 255, tray: 0 },
}

// AMS 2 Pro has no drying capability/entities — dryEntity/dryTimeEntity are
// intentionally omitted for it (nothing reads them today).
const amsUnitEntities = (serial) => ({
  'AMS 2 Pro': {
    humEntity:  `sensor.p1s_${serial}_ams_1_humidity`,
    tempEntity: `sensor.p1s_${serial}_ams_1_temperature`,
  },
  'HT1': {
    humEntity:     `sensor.p1s_${serial}_ams_128_humidity`,
    tempEntity:    `sensor.p1s_${serial}_ams_128_temperature`,
    dryEntity:     `binary_sensor.p1s_${serial}_ams_128_drying`,
    dryTimeEntity: `sensor.p1s_${serial}_ams_128_remaining_drying_time`,
  },
  'HT2': {
    humEntity:     `sensor.p1s_${serial}_ams_129_humidity`,
    tempEntity:    `sensor.p1s_${serial}_ams_129_temperature`,
    dryEntity:     `binary_sensor.p1s_${serial}_ams_129_drying`,
    dryTimeEntity: `sensor.p1s_${serial}_ams_129_remaining_drying_time`,
  },
  'HT3': {
    humEntity:     `sensor.p1s_${serial}_ams_130_humidity`,
    tempEntity:    `sensor.p1s_${serial}_ams_130_temperature`,
    dryEntity:     `binary_sensor.p1s_${serial}_ams_130_drying`,
    dryTimeEntity: `sensor.p1s_${serial}_ams_130_remaining_drying_time`,
  },
})

// Entities that feed the domain snapshot for a given serial. subscribe()
// filters state_changed events against this set BEFORE calling getState() —
// on a busy HA instance (thousands of unrelated entities), rebuilding the
// snapshot on every irrelevant event would be wasteful. Mirrors
// filament-iq-ops's ha_client.py _watched_entity_ids() exactly — the two
// providers must stay in lockstep here.
function watchedEntityIds(serial) {
  const ids = new Set([
    `sensor.p1s_${serial}_current_stage`,
    `sensor.p1s_${serial}_active_tray`,
  ])
  for (const index of Object.keys(SLOT_UNIT)) {
    ids.add(`sensor.ams_slot_${index}_status`)
    ids.add(`input_text.ams_slot_${index}_unbound_reason`)
    ids.add(`sensor.ams_slot_${index}_color_hex`)
    ids.add(`sensor.ams_slot_${index}_vendor`)
    ids.add(`sensor.ams_slot_${index}_material`)
    ids.add(`sensor.ams_slot_${index}_name`)
    ids.add(`input_text.ams_slot_${index}_spool_id`)
    ids.add(`sensor.ams_slot_${index}_remaining_g`)
    ids.add(`input_boolean.ams_slot_${index}_ran_out`)
    ids.add(`input_select.ams_slot_${index}_select_spool`)
  }
  for (const e of Object.values(amsUnitEntities(serial))) {
    ids.add(e.humEntity)
    ids.add(e.tempEntity)
    if (e.dryEntity) ids.add(e.dryEntity)
    if (e.dryTimeEntity) ids.add(e.dryTimeEntity)
  }
  return ids
}

const UNAVAILABLE = new Set(['unavailable', 'unknown', undefined, null, ''])

function sv(states, id) {
  return states?.[id]?.state
}
function sa(states, id, attr) {
  return states?.[id]?.attributes?.[attr]
}
// Raw HA state, normalized to '' when the entity is missing/unavailable/unknown.
function textField(states, id) {
  const v = sv(states, id)
  return UNAVAILABLE.has(v) ? '' : v
}

/**
 * HassProvider — the one place filament-iq's shared components are allowed
 * to know Home Assistant exists. getState()/subscribe()/rpc() are the only
 * surface; everything below (proxy.js's request/response correlation,
 * hass.callService, hass.connection.sendMessage, entity ids, HA's
 * unavailable/unknown sentinel strings) is an implementation detail.
 */
export class HassProvider {
  constructor(getHass, getSerial) {
    this._getHass = typeof getHass === 'function' ? getHass : () => getHass
    this._getSerial = typeof getSerial === 'function' ? getSerial : () => getSerial
    this._proxy = new ProxyClient(this._getHass)
  }

  // Domain snapshot — slots, AMS-unit environment (humidity/temp/drying), and
  // printer status. No entity ids or HA attribute paths leak out; the
  // active-tray -> slot cross-reference and every "unavailable"/"unknown"
  // sentinel check happen once, here.
  getState() {
    const hass = this._getHass()
    const states = hass?.states || {}
    const serial = String(this._getSerial() || DEFAULT_SERIAL).toLowerCase()

    const isPrinting = sv(states, `sensor.p1s_${serial}_current_stage`) === 'printing'
    const activeAms  = sa(states, `sensor.p1s_${serial}_active_tray`, 'ams_index')
    const activeTray = sa(states, `sensor.p1s_${serial}_active_tray`, 'tray_index')

    const slots = Object.keys(SLOT_UNIT).map(Number).sort((a, b) => a - b).map((index) => {
      const { unit, ams, tray } = SLOT_UNIT[index]
      const hex = sv(states, `sensor.ams_slot_${index}_color_hex`)
      const colorHex = !UNAVAILABLE.has(hex) ? `#${hex}` : '#555'

      const selectState = states?.[`input_select.ams_slot_${index}_select_spool`]
      const allOptions = selectState?.attributes?.options || []
      const placeholder = allOptions.find(o => o.startsWith('—') || o.startsWith('-'))
      const spoolOptions = allOptions.filter(o => o !== placeholder)
      const rawSelected = selectState?.state
      const selectedOption = (!rawSelected || rawSelected === placeholder || UNAVAILABLE.has(rawSelected))
        ? null
        : rawSelected

      return {
        index,
        unit,
        status: sv(states, `sensor.ams_slot_${index}_status`) || 'empty',
        unboundReason: textField(states, `input_text.ams_slot_${index}_unbound_reason`),
        colorHex,
        vendor: textField(states, `sensor.ams_slot_${index}_vendor`),
        material: textField(states, `sensor.ams_slot_${index}_material`),
        filamentName: textField(states, `sensor.ams_slot_${index}_name`),
        spoolId: textField(states, `input_text.ams_slot_${index}_spool_id`),
        remainingG: Number(sv(states, `sensor.ams_slot_${index}_remaining_g`)) || 0,
        ranOut: sv(states, `input_boolean.ams_slot_${index}_ran_out`) === 'on',
        isActive: isPrinting && activeAms === ams && activeTray === tray,
        spoolOptions,
        selectedOption,
      }
    })

    const unitEntities = amsUnitEntities(serial)
    const amsUnits = Object.keys(unitEntities).map((name) => {
      const e = unitEntities[name]
      const humidity = sv(states, e.humEntity)
      const connected = !UNAVAILABLE.has(humidity)
      const unit = {
        name,
        connected,
        humidity: connected ? humidity : null,
        temperature: connected ? sv(states, e.tempEntity) : null,
      }
      if (e.dryEntity) {
        unit.drying = sv(states, e.dryEntity) === 'on'
        unit.dryingRemainingMin = Math.round((parseFloat(sv(states, e.dryTimeEntity)) || 0) * 60)
      }
      return unit
    })

    return { slots, amsUnits, printer: { isPrinting } }
  }

  // Live updates. Fires cb(freshDomainSnapshot) — never a raw HA event —
  // whenever a watched entity changes; irrelevant state_changed events
  // (the vast majority on a busy HA instance) are filtered out before
  // getState() is ever called. Returns an unsubscribe fn.
  subscribe(cb) {
    const hass = this._getHass()
    if (!hass?.connection) return () => {}
    const serial = String(this._getSerial() || DEFAULT_SERIAL).toLowerCase()
    const watched = watchedEntityIds(serial)
    let unsub = null
    let cancelled = false
    hass.connection
      .subscribeEvents((event) => {
        const entityId = event?.data?.entity_id
        if (entityId && watched.has(entityId)) {
          cb(this.getState())
        }
      }, 'state_changed')
      .then((u) => {
        if (cancelled) { u(); return }
        unsub = u
      })
      .catch(() => {})
    return () => {
      cancelled = true
      if (unsub) unsub()
    }
  }

  rpc(name, payload = {}) {
    if (UNAVAILABLE_VERBS.has(name)) {
      return Promise.reject(new Error(`${name}: no AppDaemon listener registered (filament_profile_lookup not in apps.yaml)`))
    }
    switch (name) {
      // ── Spoolman CRUD (filament_iq_proxy passthrough) ──────────
      // spool/filament/vendor all hit the identical proxy shape — one
      // type-parameterized verb instead of 12 hand-named ones.
      case 'entity.list':   return this._proxy.call('GET', `/api/v1/${payload.type}`)
      case 'entity.create': return this._proxy.call('POST', `/api/v1/${payload.type}`, payload.data)
      case 'entity.update': return this._proxy.call('PATCH', `/api/v1/${payload.type}/${payload.id}`, payload.data)
      case 'entity.delete': return this._proxy.call('DELETE', `/api/v1/${payload.type}/${payload.id}`)

      // Distinct data source (bundled SpoolmanDB catalog), no CRUD siblings.
      case 'filament.searchExternal': return this._proxy.call('GET', '/api/v1/external/filament')

      // ── HA service calls ────────────────────────────────────────
      case 'slot.selectSpool':
        return this._getHass()?.callService('input_select', 'select_option', {
          entity_id: `input_select.ams_slot_${payload.index}_select_spool`,
          option: payload.option,
        })

      case 'slot.assignAndBind':
        return this._getHass()?.callService('script', 'turn_on', {
          entity_id: 'script.ams_slot_assign_and_update',
          variables: { slot: String(payload.slot) },
        })

      case 'reconcile.now':
        return this._getHass()?.callService('input_button', 'press', {
          entity_id: 'input_button.filament_iq_reconcile_now',
        })

      // ── Fire events, request/response, correlated + timed out here ──
      case 'filament.profileLookup': {
        const requestId = generateId()
        return fireAndAwait(this._getHass, {
          requestType: 'filament_iq_profile_lookup_request',
          eventData: { request_id: requestId, filament_id: payload.filament_id },
          responseType: 'filament_iq_profile_lookup_response',
          matchKey: 'request_id',
          matchValue: requestId,
          timeoutMs: 20000,
        })
      }

      case 'filament.profileVerify':
        return fireAndAwait(this._getHass, {
          requestType: 'filament_iq_profile_verify',
          eventData: payload,
          responseType: 'filament_iq_profile_verify_result',
          matchKey: 'filament_id',
          matchValue: payload.filament_id,
          timeoutMs: 10000,
        })

      case 'filament.profileBulkStatus': {
        const requestId = generateId()
        return fireAndAwait(this._getHass, {
          requestType: 'filament_iq_profile_bulk_status_request',
          eventData: { request_id: requestId },
          responseType: 'filament_iq_profile_bulk_status_response',
          matchKey: 'request_id',
          matchValue: requestId,
          timeoutMs: 30000,
        }).then((d) => d.statuses || {})
      }

      // HARDWARE (label printer). awaitResponse defaults true; pass
      // awaitResponse:false for the fire-and-forget shape (e.g. bulk
      // spool-creation loops) — same event, same payload, caller just
      // chooses whether to wait on the result.
      case 'label.print': {
        if (payload.awaitResponse === false) {
          fireEvent(this._getHass, 'filament_iq_print_label', { spool_id: payload.spool_id })
          return Promise.resolve()
        }
        return fireAndAwait(this._getHass, {
          requestType: 'filament_iq_print_label',
          eventData: { spool_id: payload.spool_id },
          responseType: 'filament_iq_label_result',
          matchKey: 'spool_id',
          matchValue: payload.spool_id,
          timeoutMs: 15000,
        })
      }

      // HARDWARE (Niimbot label printer).
      case 'label.printNiimbot':
        return fireAndAwait(this._getHass, {
          requestType: 'filament_iq_print_niimbot_label',
          eventData: { spool_id: payload.spool_id },
          responseType: 'filament_iq_niimbot_label_result',
          matchKey: 'spool_id',
          matchValue: payload.spool_id,
          timeoutMs: 15000,
        })

      // HARDWARE (AppDaemon reconcile bookkeeping) — fire-and-forget, no
      // response event exists for this one.
      case 'slot.assigned':
        fireEvent(this._getHass, 'FILAMENT_IQ_SLOT_ASSIGNED', { slot: payload.slot, spool_id: payload.spool_id })
        return Promise.resolve()

      default:
        throw new Error(`HassProvider.rpc: unknown operation "${name}"`)
    }
  }
}
