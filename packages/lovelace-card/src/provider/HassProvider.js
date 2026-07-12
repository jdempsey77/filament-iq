import { ProxyClient } from '../api/proxy'

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

/**
 * HassProvider — the one place filament-iq's shared components are allowed
 * to know Home Assistant exists. getState()/subscribe()/rpc() are the only
 * surface; everything below (proxy.js's request/response correlation,
 * hass.callService, hass.connection.sendMessage) is an implementation detail.
 */
export class HassProvider {
  constructor(getHass) {
    this._getHass = typeof getHass === 'function' ? getHass : () => getHass
    this._proxy = new ProxyClient(this._getHass)
  }

  // Snapshot of every HA entity the cards read. A curated/named shape isn't
  // practical here — slot/AMS/printer entity ids are built dynamically from
  // printer_serial and slot number across ~30+ ids — so this mirrors
  // hass.states directly and callers keep using states[entityId].
  getState() {
    const hass = this._getHass()
    return { states: hass?.states || {} }
  }

  // Live entity updates. Returns an unsubscribe fn. Callers filter for the
  // entities they care about, same as before.
  subscribe(cb) {
    const hass = this._getHass()
    if (!hass?.connection) return () => {}
    let unsub = null
    let cancelled = false
    hass.connection
      .subscribeEvents((event) => cb(event), 'state_changed')
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
    switch (name) {
      // ── Spoolman CRUD (filament_iq_proxy passthrough) ──────────
      case 'spool.list':     return this._proxy.call('GET', '/api/v1/spool')
      case 'spool.create':   return this._proxy.call('POST', '/api/v1/spool', payload)
      case 'spool.update':   return this._proxy.call('PATCH', `/api/v1/spool/${payload.id}`, payload.data)
      case 'spool.delete':   return this._proxy.call('DELETE', `/api/v1/spool/${payload.id}`)

      case 'filament.list':   return this._proxy.call('GET', '/api/v1/filament')
      case 'filament.create': return this._proxy.call('POST', '/api/v1/filament', payload)
      case 'filament.update': return this._proxy.call('PATCH', `/api/v1/filament/${payload.id}`, payload.data)
      case 'filament.delete': return this._proxy.call('DELETE', `/api/v1/filament/${payload.id}`)
      case 'filament.searchExternal': return this._proxy.call('GET', '/api/v1/external/filament')

      case 'vendor.list':   return this._proxy.call('GET', '/api/v1/vendor')
      case 'vendor.create': return this._proxy.call('POST', '/api/v1/vendor', payload)
      case 'vendor.update': return this._proxy.call('PATCH', `/api/v1/vendor/${payload.id}`, payload.data)
      case 'vendor.delete': return this._proxy.call('DELETE', `/api/v1/vendor/${payload.id}`)

      // ── HA service calls ────────────────────────────────────────
      case 'slot.selectSpool':
        return this._getHass()?.callService('input_select', 'select_option', {
          entity_id: payload.entity_id,
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

      case 'navIntent.clear': {
        const hass = this._getHass()
        if (!hass?.connection) return Promise.resolve()
        hass.connection.sendMessage({
          type: 'call_service',
          domain: 'input_text',
          service: 'set_value',
          service_data: { entity_id: 'input_text.filament_iq_nav_intent', value: '' },
        })
        return Promise.resolve()
      }

      // ── Fire events (hardware-actuating: label.*, slot.assigned) ──
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

      case 'label.print':
        return fireAndAwait(this._getHass, {
          requestType: 'filament_iq_print_label',
          eventData: { spool_id: payload.spool_id },
          responseType: 'filament_iq_label_result',
          matchKey: 'spool_id',
          matchValue: payload.spool_id,
          timeoutMs: 15000,
        })

      case 'label.printNiimbot':
        return fireAndAwait(this._getHass, {
          requestType: 'filament_iq_print_niimbot_label',
          eventData: { spool_id: payload.spool_id },
          responseType: 'filament_iq_niimbot_label_result',
          matchKey: 'spool_id',
          matchValue: payload.spool_id,
          timeoutMs: 15000,
        })

      // fire-and-forget: no response event exists for this one
      case 'label.printFireAndForget':
        fireEvent(this._getHass, 'filament_iq_print_label', { spool_id: payload.spool_id })
        return Promise.resolve()

      case 'slot.assigned':
        fireEvent(this._getHass, 'FILAMENT_IQ_SLOT_ASSIGNED', { slot: payload.slot, spool_id: payload.spool_id })
        return Promise.resolve()

      default:
        throw new Error(`HassProvider.rpc: unknown operation "${name}"`)
    }
  }
}
