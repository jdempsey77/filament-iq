const NAV_ITEMS = [
  { id: 'slots',     label: 'Slots' },
  { id: 'spools',    label: 'Spools' },
  { id: 'filaments', label: 'Filaments' },
  { id: 'vendors',   label: 'Vendors' },
]

// Desktop-only replacement for the mobile TabBar. Same active/onChange
// contract as TabBar (see TabBar.jsx). Nav only -- AMS/HT humidity+temp
// live on the slot cards / section header in SlotsTab.jsx instead (the
// AMS unit covers 4 slots so it appears once in its section header; each
// HT unit maps to exactly one slot so its reading lives on that card).
export function Sidebar({ active, onChange }) {
  return (
    <div class="fiq-sidebar">
      <div class="fiq-sidebar-nav">
        {NAV_ITEMS.map(item => (
          <button
            key={item.id}
            class={`fiq-sidebar-item${active === item.id ? ' active' : ''}`}
            onClick={() => onChange(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
    </div>
  )
}
