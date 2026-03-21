const TABS = [
  { id: 'spools', label: 'Spools' },
  { id: 'filaments', label: 'Filaments' },
  { id: 'vendors', label: 'Vendors' },
]

export function TabBar({ active, onChange }) {
  return (
    <div class="fiq-tabs">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          class={`fiq-tab ${active === tab.id ? 'active' : ''}`}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}
