import { useState } from 'preact/hooks'
import { ProviderContext } from './provider/context'
import { useSpoolman } from './hooks/useSpoolman'
import { TabBar } from './components/TabBar'
import { SpoolsTab } from './components/SpoolsTab'
import { FilamentsTab } from './components/FilamentsTab'
import { VendorsTab } from './components/VendorsTab'
import { FilamentIQLogo } from './components/FilamentIQLogo'
import SlotsTab from './components/SlotsTab'

export function FilamentIQCard({ provider, navIntent, onNavIntentConsumed, config }) {
  const [activeTab, setActiveTab] = useState(() => localStorage.getItem('filamentiq_tab') || config?.initial_tab || 'slots')
  const handleTabChange = (tab) => {
    localStorage.setItem('filamentiq_tab', tab)
    setActiveTab(tab)
  }

  const data = useSpoolman(provider)

  if (!provider) {
    return <div class="fiq-card"><div class="fiq-loading">Connecting to Home Assistant...</div></div>
  }

  if (data.error) {
    return <div class="fiq-card"><div class="fiq-error">{String(data.error?.message || data.error)}</div></div>
  }

  return (
    <ProviderContext.Provider value={provider}>
      <div class="fiq-card">
        <div class="fiq-topbar">
          <span class="fiq-title">
            <span class="fiq-dot" />
            <FilamentIQLogo height={28} showWordmark={true} />
          </span>
          <button class="fiq-btn-refresh" onClick={() => data.refresh()} title="Reload from Spoolman">↺</button>
        </div>
        <div class="fiq-subnav">
          <TabBar active={activeTab} onChange={handleTabChange} />
        </div>
        <div class="fiq-body">
          {activeTab === 'spools'    && <SpoolsTab    {...data} navIntent={navIntent} onNavIntentConsumed={onNavIntentConsumed} />}
          {activeTab === 'filaments' && <FilamentsTab {...data} />}
          {activeTab === 'vendors'   && <VendorsTab   {...data} />}
          {activeTab === 'slots'     && <SlotsTab     spools={data.spools} updateSpool={data.updateSpool} deleteSpool={data.deleteSpool} />}
        </div>
      </div>
    </ProviderContext.Provider>
  )
}
