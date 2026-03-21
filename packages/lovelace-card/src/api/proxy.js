function generateUUID() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c =>
    (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16)
  )
}

export class ProxyError extends Error {
  constructor(status, body) {
    super(`Spoolman proxy error: ${status}`)
    this.status = status
    this.body = body
  }
}

export class ProxyClient {
  constructor(getHass) {
    this._getHass = typeof getHass === 'function' ? getHass : () => getHass
  }

  async call(method, path, body = null) {
    try {
      return await this._doCall(method, path, body)
    } catch (e) {
      const isConnErr = e?.code === 'connection_lost'
        || e?.message?.includes('connection')
        || e?.message?.includes('not found')
      if (isConnErr) {
        await new Promise(r => setTimeout(r, 1500))
        return await this._doCall(method, path, body)
      }
      throw e
    }
  }

  async _doCall(method, path, body) {
    const hass = this._getHass()
    if (!hass) throw new ProxyError(503, { error: 'hass not available' })

    const requestId = generateUUID()

    return new Promise((resolve, reject) => {
      let unsubscribe = null
      const timeout = setTimeout(() => {
        if (unsubscribe) unsubscribe()
        reject(new ProxyError(408, { error: 'proxy timeout (10s)' }))
      }, 10000)

      hass.connection
        .subscribeEvents((event) => {
          if (event.data.request_id !== requestId) return
          clearTimeout(timeout)
          if (unsubscribe) unsubscribe()
          if (event.data.status >= 400) {
            reject(new ProxyError(event.data.status, event.data.body))
          } else {
            resolve(event.data.body)
          }
        }, 'filament_iq_proxy_response')
        .then((unsub) => {
          unsubscribe = unsub
          const serviceData = { request_id: requestId, method, path }
          if (body) serviceData.body = body
          hass.callService('filament_iq_proxy', 'api_call', serviceData)
            .catch((err) => {
              clearTimeout(timeout)
              if (unsubscribe) unsubscribe()
              reject(err)
            })
        })
        .catch((err) => {
          clearTimeout(timeout)
          reject(err)
        })
    })
  }
}
