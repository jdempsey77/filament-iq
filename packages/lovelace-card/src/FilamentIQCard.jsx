import { useState, useMemo } from 'preact/hooks'
import { ProxyClient } from './api/proxy'
import { useSpoolman } from './hooks/useSpoolman'
import { TabBar } from './components/TabBar'
import { StatsBar } from './components/StatsBar'
import { SpoolsTab } from './components/SpoolsTab'
import { FilamentsTab } from './components/FilamentsTab'
import { VendorsTab } from './components/VendorsTab'
import { FilamentIQLogo } from './components/FilamentIQLogo'

export function FilamentIQCard({ hass, getHass }) {
  const [activeTab, setActiveTab] = useState('spools')

  const client = useMemo(() => {
    if (!hass) return null
    return new ProxyClient(getHass || (() => hass))
  }, [])

  const data = useSpoolman(client)

  if (!hass || !client) {
    return <div class="fiq-card"><div class="fiq-loading">Connecting to Home Assistant...</div></div>
  }

  if (data.error) {
    return <div class="fiq-card"><div class="fiq-error">{String(data.error?.message || data.error)}</div></div>
  }

  return (
    <div class="fiq-card">
      <div class="fiq-topbar">
        <span class="fiq-title">
          <span class="fiq-dot" />
          <FilamentIQLogo height={28} showWordmark={true} />
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button class="fiq-btn-refresh" onClick={() => data.refresh()} title="Reload from Spoolman">↺</button>
          <TabBar active={activeTab} onChange={setActiveTab} />
        </div>
      </div>
      <StatsBar spools={data.spools} />
      <div class="fiq-body">
        {activeTab === 'spools'    && <SpoolsTab    {...data} hass={hass} />}
        {activeTab === 'filaments' && <FilamentsTab {...data} client={client} />}
        {activeTab === 'vendors'   && <VendorsTab   {...data} />}
      </div>
    </div>
  )
}
