import { cacheGet, cacheSet } from '../cache'

// Same 8 slots / 4 AMS units shape HassProvider.getState() always returns,
// even with no data yet -- so components never need a "provider not ready"
// special case. No entity ids here, just the domain contract's own literal
// values (unit names are part of the contract itself, not HA internals).
const EMPTY_SNAPSHOT = {
  slots: [
    { index: 1, unit: 'AMS 2 Pro' }, { index: 2, unit: 'AMS 2 Pro' },
    { index: 3, unit: 'AMS 2 Pro' }, { index: 4, unit: 'AMS 2 Pro' },
    { index: 5, unit: 'HT1' }, { index: 6, unit: 'HT2' },
    { index: 7, unit: 'HT3' }, { index: 8, unit: 'external' },
  ].map((s) => ({
    ...s,
    status: 'empty',
    unboundReason: '',
    colorHex: '#555',
    vendor: '',
    material: '',
    filamentName: '',
    spoolId: '',
    remainingG: 0,
    ranOut: false,
    isActive: false,
    spoolOptions: [],
    selectedOption: null,
  })),
  amsUnits: ['AMS 2 Pro', 'HT1', 'HT2', 'HT3'].map((name) => ({
    name,
    connected: false,
    humidity: null,
    temperature: null,
    ...(name === 'AMS 2 Pro' ? {} : { drying: false, dryingRemainingMin: 0 }),
  })),
  printer: { isPrinting: false },
}

/**
 * BffProvider — same getState()/subscribe()/rpc() contract as HassProvider,
 * implemented against filament-iq-ops instead of Home Assistant. No entity
 * ids, ever; the BFF owns that mapping (see filament-iq-ops's domain.py).
 *
 * Stale-while-revalidate: constructor seeds the in-memory snapshot from
 * localStorage so getState() is never empty on a resumed session, then
 * immediately opens the SSE connection -- its first frame (the BFF always
 * sends the current snapshot immediately on connect) is the revalidation.
 *
 * baseUrl is a same-origin relative path (e.g. import.meta.env.BASE_URL +
 * 'api'), never an absolute host:port -- this is a public repo; no IPs or
 * secrets may appear in source.
 */
export class BffProvider {
  constructor(baseUrl) {
    this._baseUrl = baseUrl.replace(/\/$/, '')
    this._snapshot = cacheGet('snapshot') || EMPTY_SNAPSHOT
    this._subscribers = new Set()
    this._eventSource = null
    this._connect()
  }

  getState() {
    return this._snapshot
  }

  subscribe(cb) {
    this._subscribers.add(cb)
    return () => {
      this._subscribers.delete(cb)
    }
  }

  async rpc(name, payload = {}) {
    // No Authorization header set here on purpose: POST /rpc/* requires
    // HTTP Basic as defense-in-depth behind Authelia. The browser's native
    // Basic-auth prompt (triggered by the server's 401 challenge, cached
    // by the browser for the session) supplies it -- this file never
    // embeds a credential, matching "zero secrets in a public repo".
    const res = await fetch(`${this._baseUrl}/rpc/${name}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      let detail = `HTTP ${res.status}`
      try {
        const body = await res.json()
        if (body?.detail) detail = body.detail
      } catch (_) { /* non-JSON error body */ }
      throw new Error(detail)
    }
    const body = await res.json()
    return body.result
  }

  _connect() {
    if (this._eventSource) return
    const es = new EventSource(`${this._baseUrl}/events`)
    es.onmessage = (evt) => {
      let snapshot
      try {
        snapshot = JSON.parse(evt.data)
      } catch (e) {
        // Surfaced, not swallowed: a malformed frame here (e.g. a proxy
        // mangling/truncating the response in transit) previously failed
        // silently, leaving the shell stuck on stale cached data with no
        // visible error to debug from.
        console.error('[BffProvider] malformed SSE frame, keeping prior snapshot', e, evt.data?.slice(0, 200))
        return
      }
      if (!Array.isArray(snapshot?.slots) || !Array.isArray(snapshot?.amsUnits)) {
        console.error('[BffProvider] SSE frame missing expected shape, keeping prior snapshot', snapshot)
        return
      }
      this._snapshot = snapshot
      cacheSet('snapshot', snapshot)
      for (const cb of this._subscribers) cb(snapshot)
    }
    es.onerror = () => {
      // EventSource auto-reconnects on its own with the browser's built-in
      // backoff; log so a broken connection is visible instead of the UI
      // just silently sitting on stale data with no indication why.
      console.warn('[BffProvider] SSE connection error -- browser will auto-reconnect')
    }
    this._eventSource = es
  }
}
