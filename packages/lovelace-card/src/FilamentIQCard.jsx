import { useState, useMemo } from 'preact/hooks'
import { ProxyClient } from './api/proxy'
import { useSpoolman } from './hooks/useSpoolman'
import { TabBar } from './components/TabBar'
import { SpoolsTab } from './components/SpoolsTab'
import { FilamentsTab } from './components/FilamentsTab'
import { VendorsTab } from './components/VendorsTab'
import { FilamentIQLogo } from './components/FilamentIQLogo'
import SlotsTab from './components/SlotsTab'

export function FilamentIQCard({ hass, getHass, navIntent, config }) {
  const [activeTab, setActiveTab] = useState(config?.initial_tab || 'spools')

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
        <button class="fiq-btn-refresh" onClick={() => data.refresh()} title="Reload from Spoolman">↺</button>
      </div>
      <div class="fiq-subnav">
        <TabBar active={activeTab} onChange={setActiveTab} />
      </div>
      <div class="fiq-body">
        {activeTab === 'spools'    && <SpoolsTab    {...data} hass={hass} getHass={getHass} navIntent={navIntent} />}
        {activeTab === 'filaments' && <FilamentsTab {...data} client={client} />}
        {activeTab === 'vendors'   && <VendorsTab   {...data} />}
        {activeTab === 'slots'     && <SlotsTab     getHass={getHass} hass={hass} spools={data.spools} updateSpool={data.updateSpool} deleteSpool={data.deleteSpool} />}
      </div>
    </div>
  )
}
