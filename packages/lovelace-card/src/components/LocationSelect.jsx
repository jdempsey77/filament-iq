const LOCATIONS = [
  { value: '',              label: '— Unassigned —' },
  { value: 'AMS1_Slot1',   label: 'AMS 1 · Slot 1' },
  { value: 'AMS1_Slot2',   label: 'AMS 1 · Slot 2' },
  { value: 'AMS1_Slot3',   label: 'AMS 1 · Slot 3' },
  { value: 'AMS1_Slot4',   label: 'AMS 1 · Slot 4' },
  { value: 'AMS128_Slot1', label: 'HT1 · Slot 5' },
  { value: 'AMS129_Slot1', label: 'HT2 · Slot 6' },
  { value: 'AMS130_Slot1', label: 'HT3 · Slot 7' },
  { value: 'Shelf',        label: 'Shelf' },
  { value: 'New',          label: 'New' },
]

export function LocationSelect({ value, onChange, className = 'fiq-select' }) {
  return (
    <select class={className} value={value} onChange={e => onChange(e.target.value)}>
      {LOCATIONS.map(loc => (
        <option key={loc.value} value={loc.value}>{loc.label}</option>
      ))}
      {value && !LOCATIONS.find(l => l.value === value) && (
        <option value={value}>{value}</option>
      )}
    </select>
  )
}
