import { useState, useEffect, useCallback } from 'preact/hooks'

export function useSpoolman(client) {
  const [spools, setSpools] = useState(null)
  const [filaments, setFilaments] = useState(null)
  const [vendors, setVendors] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    if (!client) return
    setLoading(true)
    setError(null)
    try {
      const [s, f, v] = await Promise.all([
        client.call('GET', '/api/v1/spool'),
        client.call('GET', '/api/v1/filament'),
        client.call('GET', '/api/v1/vendor'),
      ])
      setSpools(Array.isArray(s) ? s : [])
      setFilaments(Array.isArray(f) ? f : [])
      setVendors(Array.isArray(v) ? v : [])
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
  }, [client])

  useEffect(() => {
    if (client) refresh()
  }, [client])

  const createSpool = useCallback(async (data) => {
    const result = await client?.call('POST', '/api/v1/spool', data)
    await refresh()
    return result
  }, [client, refresh])

  const updateSpool = useCallback(async (id, data) => {
    await client?.call('PATCH', `/api/v1/spool/${id}`, data)
    await refresh()
  }, [client, refresh])

  const deleteSpool = useCallback(async (id) => {
    await client?.call('DELETE', `/api/v1/spool/${id}`)
    await refresh()
  }, [client, refresh])

  const createFilament = useCallback(async (data) => {
    await client?.call('POST', '/api/v1/filament', data)
    await refresh()
  }, [client, refresh])

  const updateFilament = useCallback(async (id, data) => {
    await client?.call('PATCH', `/api/v1/filament/${id}`, data)
    await refresh()
  }, [client, refresh])

  const deleteFilament = useCallback(async (id) => {
    await client?.call('DELETE', `/api/v1/filament/${id}`)
    await refresh()
  }, [client, refresh])

  const createVendor = useCallback(async (data) => {
    await client?.call('POST', '/api/v1/vendor', data)
    await refresh()
  }, [client, refresh])

  const updateVendor = useCallback(async (id, data) => {
    await client?.call('PATCH', `/api/v1/vendor/${id}`, data)
    await refresh()
  }, [client, refresh])

  const deleteVendor = useCallback(async (id) => {
    await client?.call('DELETE', `/api/v1/vendor/${id}`)
    await refresh()
  }, [client, refresh])

  return {
    spools, filaments, vendors,
    loading, error, refresh,
    createSpool, updateSpool, deleteSpool,
    createFilament, updateFilament, deleteFilament,
    createVendor, updateVendor, deleteVendor,
  }
}
