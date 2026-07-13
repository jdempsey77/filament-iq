import { useState, useEffect, useCallback } from 'preact/hooks'
import { cacheGet, cacheSet } from '../cache'

// Stale-while-revalidate: seed from cache so the first paint (including a
// resumed, previously-process-killed session) is instant, then always
// refetch in the background and write through on completion. Provider-
// agnostic — HassProvider's rpc() is already fast (warm LAN connection),
// so this is invisible there; BffProvider's rpc() is a real network round
// trip, where the cache seed is what makes cold resume spinner-free.
export function useSpoolman(provider) {
  const [spools, setSpools] = useState(() => cacheGet('spools'))
  const [filaments, setFilaments] = useState(() => cacheGet('filaments'))
  const [vendors, setVendors] = useState(() => cacheGet('vendors'))
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    if (!provider) return
    setLoading(true)
    setError(null)
    try {
      const [s, f, v] = await Promise.all([
        provider.rpc('entity.list', { type: 'spool' }),
        provider.rpc('entity.list', { type: 'filament' }),
        provider.rpc('entity.list', { type: 'vendor' }),
      ])
      const spoolList = Array.isArray(s) ? s : []
      const filamentList = Array.isArray(f) ? f : []
      const vendorList = Array.isArray(v) ? v : []
      setSpools(spoolList)
      setFilaments(filamentList)
      setVendors(vendorList)
      cacheSet('spools', spoolList)
      cacheSet('filaments', filamentList)
      cacheSet('vendors', vendorList)
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
  }, [provider])

  useEffect(() => {
    if (provider) refresh()
  }, [provider])

  const createSpool = useCallback(async (data) => {
    const result = await provider?.rpc('entity.create', { type: 'spool', data })
    await refresh()
    return result
  }, [provider, refresh])

  const updateSpool = useCallback(async (id, data) => {
    await provider?.rpc('entity.update', { type: 'spool', id, data })
    await refresh()
  }, [provider, refresh])

  const deleteSpool = useCallback(async (id) => {
    await provider?.rpc('entity.delete', { type: 'spool', id })
    await refresh()
  }, [provider, refresh])

  const createFilament = useCallback(async (data) => {
    await provider?.rpc('entity.create', { type: 'filament', data })
    await refresh()
  }, [provider, refresh])

  const updateFilament = useCallback(async (id, data) => {
    await provider?.rpc('entity.update', { type: 'filament', id, data })
    await refresh()
  }, [provider, refresh])

  const deleteFilament = useCallback(async (id) => {
    await provider?.rpc('entity.delete', { type: 'filament', id })
    await refresh()
  }, [provider, refresh])

  const createVendor = useCallback(async (data) => {
    await provider?.rpc('entity.create', { type: 'vendor', data })
    await refresh()
  }, [provider, refresh])

  const updateVendor = useCallback(async (id, data) => {
    await provider?.rpc('entity.update', { type: 'vendor', id, data })
    await refresh()
  }, [provider, refresh])

  const deleteVendor = useCallback(async (id) => {
    await provider?.rpc('entity.delete', { type: 'vendor', id })
    await refresh()
  }, [provider, refresh])

  return {
    spools, filaments, vendors,
    loading, error, refresh,
    createSpool, updateSpool, deleteSpool,
    createFilament, updateFilament, deleteFilament,
    createVendor, updateVendor, deleteVendor,
  }
}
