import { useState } from 'preact/hooks'
import { ProviderContext } from './provider/context'
import { useSpoolman } from './hooks/useSpoolman'
import { TabBar } from './components/TabBar'
import { SpoolsTab } from './components/SpoolsTab'
import { FilamentsTab } from './components/FilamentsTab'
import { VendorsTab } from './components/VendorsTab'
import { FilamentIQLogo } from './components/FilamentIQLogo'
import SlotsTab from './components/SlotsTab'

// Fallback when the card is embedded with empty config (e.g. printer-dashboard
// FIQ tab). Normal use supplies printer_serial via card config → main.jsx.
const DEFAULT_SERIAL = '01p00c5b2201397'

export function FilamentIQCard({ provider, navIntent, config, printer_serial }) {
  const serial = String(printer_serial || config?.printer_serial || DEFAULT_SERIAL).toLowerCase()
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
          {activeTab === 'spools'    && <SpoolsTab    {...data} navIntent={navIntent} />}
          {activeTab === 'filaments' && <FilamentsTab {...data} />}
          {activeTab === 'vendors'   && <VendorsTab   {...data} />}
          {activeTab === 'slots'     && <SlotsTab     spools={data.spools} updateSpool={data.updateSpool} deleteSpool={data.deleteSpool} printer_serial={serial} />}
        </div>
      </div>
    </ProviderContext.Provider>
  )
}
