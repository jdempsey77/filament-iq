import { useState, useEffect } from 'preact/hooks'
import { useProvider } from '../provider/context'

// Subscribes to the provider's domain snapshot for the lifetime of the
// component. Replaces direct provider.getState() calls — components that
// need live slot/AMS/printer data consume this hook instead, so they update
// on any watched-entity change, not just on an incidental parent re-render.
// Works identically against HassProvider or (Phase 2) BffProvider — both
// implement the same getState()/subscribe(cb) contract.
export function useSnapshot() {
  const provider = useProvider()
  const [snapshot, setSnapshot] = useState(() => (provider ? provider.getState() : null))

  useEffect(() => {
    if (!provider) return
    setSnapshot(provider.getState())
    return provider.subscribe(setSnapshot)
  }, [provider])

  return snapshot
}
