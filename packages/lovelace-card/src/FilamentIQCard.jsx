import { useState, useRef, useEffect } from 'preact/hooks'
import { ProviderContext } from './provider/context'
import { useSpoolman } from './hooks/useSpoolman'
import { useIsDesktop } from './hooks/useIsDesktop'
import { useSpoolPrintActions } from './hooks/useSpoolPrintActions'
import { TabBar } from './components/TabBar'
import { Sidebar } from './components/Sidebar'
import { SpoolsTab, SpoolEditPanel } from './components/SpoolsTab'
import { FilamentsTab, FilamentEditPanel } from './components/FilamentsTab'
import { VendorsTab, VendorEditPanel } from './components/VendorsTab'
import { FilamentIQLogo } from './components/FilamentIQLogo'
import SlotsTab, { SlotDetailPanel } from './components/SlotsTab'

// Desktop-only: the right panel's spool view. Owns its own print-action
// state via useSpoolPrintActions (the panel isn't a row in a list, so there's
// no parent already tracking per-row printing/toast state the way SpoolsTab
// does for its inline-expand path).
function SpoolDetail({ spool, data, onClose }) {
  const { printingLabel, printingNiimbotLabel, toast, handlePrintLabel, handlePrintSwatchLabel } = useSpoolPrintActions(spool)
  return (
    <>
      {toast && <div class={`fiq-toast ${toast.type === 'err' ? 'fiq-toast-err' : 'fiq-toast-ok'}`}>{toast.msg}</div>}
      <SpoolEditPanel
        spool={spool}
        identity={true}
        onSave={(id, patch) => data.updateSpool(id, patch)}
        onCancel={onClose}
        onDelete={(id) => data.deleteSpool(id).then(() => onClose())}
        onPrintLabel={handlePrintLabel}
        onPrintSwatchLabel={handlePrintSwatchLabel}
        printingLabel={printingLabel}
        printingNiimbotLabel={printingNiimbotLabel}
      />
    </>
  )
}

// Desktop-only right panel -- one persistent panel component for both slot
// and spool contexts (and filament/vendor rows), replacing the mobile
// full-screen takeover. Tapping a bound spool while viewing a slot switches
// this panel from slot context to spool context in place.
function DetailPanel({ selected, data, onSelect }) {
  if (!selected) {
    return <div class="fiq-detail-empty">Select a row to see details</div>
  }

  if (selected.type === 'slot') {
    return (
      <SlotDetailPanel
        popup={selected.data}
        spools={data.spools}
        onOpenSpool={(spool) => onSelect({ type: 'spool', id: spool.id })}
      />
    )
  }

  if (selected.type === 'spool') {
    const spool = data.spools?.find(s => s.id === selected.id)
    if (!spool) return <div class="fiq-detail-empty">Spool not found</div>
    return <SpoolDetail spool={spool} data={data} onClose={() => onSelect(null)} />
  }

  if (selected.type === 'filament') {
    const filament = data.filaments?.find(f => f.id === selected.id)
    if (!filament) return <div class="fiq-detail-empty">Filament not found</div>
    return (
      <FilamentEditPanel
        filament={filament}
        vendors={data.vendors}
        onSave={(id, patch) => data.updateFilament(id, patch)}
        onCancel={() => onSelect(null)}
        onDelete={(id) => data.deleteFilament(id).then(() => onSelect(null))}
      />
    )
  }

  if (selected.type === 'vendor') {
    const vendor = data.vendors?.find(v => v.id === selected.id)
    if (!vendor) return <div class="fiq-detail-empty">Vendor not found</div>
    return (
      <VendorEditPanel
        vendor={vendor}
        onSave={(id, patch) => data.updateVendor(id, patch)}
        onCancel={() => onSelect(null)}
        onDelete={(id) => data.deleteVendor(id).then(() => onSelect(null))}
      />
    )
  }

  return null
}

export function FilamentIQCard({ provider, navIntent, onNavIntentConsumed, config }) {
  const cardRef = useRef(null)
  const isDesktop = useIsDesktop(cardRef)
  const [activeTab, setActiveTab] = useState(() => localStorage.getItem('filamentiq_tab') || config?.initial_tab || 'slots')
  const [selected, setSelected] = useState(null)
  // Desktop-only: Reconcile lives in the topbar (next to Refresh) rather than
  // as a full-width button in the Slots tab body -- that's a mobile pattern.
  // Mobile keeps its own full-width Reconcile inside SlotsSegment, unchanged.
  const [reconciling, setReconciling] = useState(false)
  const handleReconcile = () => {
    if (!provider) return
    provider.rpc('reconcile.now')
    setReconciling(true)
    setTimeout(() => setReconciling(false), 4000)
  }

  const handleTabChange = (tab) => {
    localStorage.setItem('filamentiq_tab', tab)
    setActiveTab(tab)
    setSelected(null)
  }

  const data = useSpoolman(provider)

  // Esc closes the desktop detail panel, mirroring the X button.
  useEffect(() => {
    if (!isDesktop || !selected) return
    const onKeyDown = (e) => { if (e.key === 'Escape') setSelected(null) }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isDesktop, selected])

  if (!provider) {
    return <div class="fiq-card" ref={cardRef}><div class="fiq-loading">Connecting to Home Assistant...</div></div>
  }

  if (data.error) {
    return <div class="fiq-card" ref={cardRef}><div class="fiq-error">{String(data.error?.message || data.error)}</div></div>
  }

  const topbar = (
    <div class="fiq-topbar">
      <span class="fiq-title">
        <span class="fiq-dot" />
        <FilamentIQLogo height={28} showWordmark={true} />
      </span>
      {isDesktop ? (
        <div class="fiq-topbar-actions">
          {activeTab === 'slots' && (
            <button
              class="fiq-btn-topbar-action"
              onClick={handleReconcile}
              disabled={reconciling}
              title="Reconcile slot bindings against the printer"
            >
              {reconciling ? '↻ Reconciling…' : '↺ Reconcile'}
            </button>
          )}
          <button class="fiq-btn-topbar-action" onClick={() => data.refresh()} title="Reload from Spoolman">
            ↺ Refresh
          </button>
        </div>
      ) : (
        <button class="fiq-btn-refresh" onClick={() => data.refresh()} title="Reload from Spoolman">↺</button>
      )}
    </div>
  )

  const body = (
    <>
      {activeTab === 'spools'    && <SpoolsTab    {...data} navIntent={navIntent} onNavIntentConsumed={onNavIntentConsumed} isDesktop={isDesktop} selected={selected} onSelect={setSelected} />}
      {activeTab === 'filaments' && <FilamentsTab {...data} isDesktop={isDesktop} selected={selected} onSelect={setSelected} />}
      {activeTab === 'vendors'   && <VendorsTab   {...data} isDesktop={isDesktop} selected={selected} onSelect={setSelected} />}
      {activeTab === 'slots'     && <SlotsTab     spools={data.spools} updateSpool={data.updateSpool} deleteSpool={data.deleteSpool} isDesktop={isDesktop} onSelectSlot={(slotData) => setSelected({ type: 'slot', data: slotData })} />}
    </>
  )

  return (
    <ProviderContext.Provider value={provider}>
      <div class="fiq-card" ref={cardRef}>
        {isDesktop ? (
          <div class="fiq-desktop-shell">
            <Sidebar active={activeTab} onChange={handleTabChange} />
            <div class="fiq-main">
              {topbar}
              <div class="fiq-main-body">{body}</div>
            </div>
            <div class="fiq-detail-panel">
              {selected && (
                <button class="fiq-detail-close" onClick={() => setSelected(null)} title="Close (Esc)">×</button>
              )}
              <DetailPanel selected={selected} data={data} onSelect={setSelected} />
            </div>
          </div>
        ) : (
          <>
            {topbar}
            <div class="fiq-subnav">
              <TabBar active={activeTab} onChange={handleTabChange} />
            </div>
            <div class="fiq-body">{body}</div>
          </>
        )}
      </div>
    </ProviderContext.Provider>
  )
}
