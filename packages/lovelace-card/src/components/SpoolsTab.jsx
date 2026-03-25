import { useState, useMemo } from 'preact/hooks'
import { ConfirmDialog } from './ConfirmDialog'
import { LocationSelect } from './LocationSelect'

const LOCATION_TO_SLOT = {
  'AMS1_Slot1':   1,
  'AMS1_Slot2':   2,
  'AMS1_Slot3':   3,
  'AMS1_Slot4':   4,
  'AMS128_Slot1': 5,
  'AMS129_Slot1': 6,
}

function ColorDot({ hex }) {
  const color = hex ? `#${hex}` : '#555'
  const isBlack = !hex || hex.toLowerCase() === '000000'
  return (
    <div
      class="fiq-color-dot"
      style={{
        background: color,
        border: isBlack ? '1px solid #444' : 'none',
      }}
    />
  )
}

function LocationBadge({ location }) {
  if (!location) return null
  const loc = location.toUpperCase()
  const isAMS = loc.startsWith('AMS')
  let label = location
  if (location === 'AMS1_Slot1') label = 'AMS 1 · Slot 1'
  else if (location === 'AMS1_Slot2') label = 'AMS 1 · Slot 2'
  else if (location === 'AMS1_Slot3') label = 'AMS 1 · Slot 3'
  else if (location === 'AMS1_Slot4') label = 'AMS 1 · Slot 4'
  else if (location === 'AMS128_Slot1') label = 'HT1 · Slot 5'
  else if (location === 'AMS129_Slot1') label = 'HT2 · Slot 6'
  const cls = isAMS ? 'fiq-loc-ams'
    : location === 'Shelf' ? 'fiq-loc-shelf'
    : location === 'New' ? 'fiq-loc-new'
    : 'fiq-loc-other'
  return <span class={`fiq-loc-badge ${cls}`}>{label}</span>
}

function MatBadge({ material }) {
  const m = (material || '').toUpperCase()
  const cls = m === 'PLA' ? 'fiq-mat-pla'
    : m === 'PLA+' ? 'fiq-mat-pla-plus'
    : m === 'PETG' ? 'fiq-mat-petg'
    : m.startsWith('ABS') ? 'fiq-mat-abs'
    : m === 'TPU' ? 'fiq-mat-tpu'
    : 'fiq-mat-other'
  return <span class={`fiq-mat-badge ${cls}`}>{material || '—'}</span>
}

function SpoolEditPanel({ spool, hass, onSave, onCancel, onDelete }) {
  const [remaining, setRemaining] = useState(Math.round(spool.remaining_weight || 0))
  const [location, setLocation] = useState(spool.location || '')
  const [firstUsed, setFirstUsed] = useState(
    spool.first_used ? spool.first_used.substring(0, 10) : ''
  )
  const [confirming, setConfirming] = useState(false)
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    setSaving(true)
    try {
      await onSave(spool.id, {
        remaining_weight: Number(remaining),
        location,
        ...(firstUsed ? { first_used: firstUsed } : {}),
      })
      // Fire FILAMENT_IQ_SLOT_ASSIGNED so reconciler writes input_text.ams_slot_N_spool_id
      const slot = LOCATION_TO_SLOT[location]
      if (slot && hass) {
        hass.connection.sendMessage({
          type: 'fire_event',
          event_type: 'FILAMENT_IQ_SLOT_ASSIGNED',
          event_data: { slot, spool_id: spool.id },
        })
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <div class="fiq-edit-panel">
      <div class="fiq-identity">
        <div>
          <div class="fiq-id-key">Lot #</div>
          <div class="fiq-id-val">{spool.lot_nr || '—'}</div>
        </div>
        <div>
          <div class="fiq-id-key">Spool ID</div>
          <div class="fiq-id-val">#{spool.id}</div>
        </div>
        <div>
          <div class="fiq-id-key">Last used</div>
          <div class="fiq-id-val">{spool.last_used ? spool.last_used.substring(0, 10) : '—'}</div>
        </div>
      </div>
      <div class="fiq-fields">
        <div>
          <div class="fiq-field-label">Remaining (g)</div>
          <input class="fiq-input" type="number" value={remaining} onInput={e => setRemaining(e.target.value)} />
        </div>
        <div>
          <div class="fiq-field-label">Location</div>
          <LocationSelect value={location} onChange={setLocation} />
        </div>
        <div>
          <div class="fiq-field-label">First used</div>
          <input class="fiq-input" type="date" value={firstUsed} onInput={e => setFirstUsed(e.target.value)} />
        </div>
      </div>
      <div class="fiq-panel-footer">
        <button class="fiq-btn-del" onClick={() => setConfirming(true)} disabled={saving}>Delete spool</button>
        <div class="fiq-btn-group">
          <button class="fiq-btn-cancel" onClick={onCancel} disabled={saving}>Cancel</button>
          <button class="fiq-btn-save" onClick={handleSave} disabled={saving}>{saving ? 'Saving...' : 'Save changes'}</button>
        </div>
      </div>
      {confirming && (
        <ConfirmDialog
          message="Delete this spool?"
          onConfirm={() => { setConfirming(false); onDelete(spool.id) }}
          onCancel={() => setConfirming(false)}
        />
      )}
    </div>
  )
}

function SpoolAddRow({ filaments, onCreate, onCancel }) {
  const [filamentId, setFilamentId] = useState('')
  const [location, setLocation] = useState('Shelf')
  const [initialWeight, setInitialWeight] = useState(1000)
  const [remainingWeight, setRemainingWeight] = useState(1000)
  const [saving, setSaving] = useState(false)

  const handleCreate = async () => {
    setSaving(true)
    try {
      await onCreate({
        filament_id: Number(filamentId),
        location,
        initial_weight: Number(initialWeight),
        remaining_weight: Number(remainingWeight),
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div class="fiq-add-row">
      <div class="fiq-add-title">New spool</div>
      <div class="fiq-add-fields">
        <div>
          <div class="fiq-field-label">Filament</div>
          <select class="fiq-select" value={filamentId} onChange={e => setFilamentId(e.target.value)}>
            <option value="">— Select —</option>
            {(filaments || []).map(f => (
              <option key={f.id} value={String(f.id)}>{f.vendor?.name || '?'} — {f.name || '?'}</option>
            ))}
          </select>
        </div>
        <div>
          <div class="fiq-field-label">Location</div>
          <LocationSelect value={location} onChange={setLocation} />
        </div>
        <div>
          <div class="fiq-field-label">Initial (g)</div>
          <input class="fiq-input" type="number" value={initialWeight} onInput={e => setInitialWeight(e.target.value)} />
        </div>
        <div>
          <div class="fiq-field-label">Remaining (g)</div>
          <input class="fiq-input" type="number" value={remainingWeight} onInput={e => setRemainingWeight(e.target.value)} />
        </div>
      </div>
      <div class="fiq-panel-footer">
        <div />
        <div class="fiq-btn-group">
          <button class="fiq-btn-cancel" onClick={onCancel} disabled={saving}>Cancel</button>
          <button class="fiq-btn-save" onClick={handleCreate} disabled={saving || !filamentId}>{saving ? 'Creating...' : 'Create spool'}</button>
        </div>
      </div>
    </div>
  )
}

export function SpoolsTab({ spools, filaments, updateSpool, deleteSpool, createSpool, refresh, hass }) {
  const [search, setSearch] = useState('')
  const [vendorFilter, setVendorFilter] = useState('')
  const [materialFilter, setMaterialFilter] = useState('')
  const [locationFilter, setLocationFilter] = useState('')
  const [editId, setEditId] = useState(null)
  const [adding, setAdding] = useState(false)
  const [archiveConfirm, setArchiveConfirm] = useState(false)
  const [archiving, setArchiving] = useState(false)

  const emptySpools = useMemo(() =>
    (spools || []).filter(s => !s.archived && ((s.remaining_weight || 0) === 0)),
    [spools]
  )

  const doArchive = async () => {
    setArchiveConfirm(false)
    setArchiving(true)
    try {
      await Promise.all(emptySpools.map(s => updateSpool(s.id, { archived: true })))
    } finally {
      setArchiving(false)
    }
  }

  const vendors = useMemo(() => {
    const set = new Set()
    ;(spools || []).forEach(s => { const v = s.filament?.vendor?.name; if (v) set.add(v) })
    return [...set].sort()
  }, [spools])

  const materials = useMemo(() => {
    const set = new Set()
    ;(spools || []).forEach(s => { const m = s.filament?.material; if (m) set.add(m) })
    return [...set].sort()
  }, [spools])

  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    return (spools || []).filter(s => {
      const f = s.filament || {}
      const name = (f.name || '').toLowerCase()
      const vendor = (f.vendor?.name || '').toLowerCase()
      const mat = (f.material || '').toLowerCase()
      if (q && !name.includes(q) && !vendor.includes(q) && !mat.includes(q)) return false
      if (vendorFilter && vendor !== vendorFilter.toLowerCase()) return false
      if (materialFilter && mat !== materialFilter.toLowerCase()) return false
      if (locationFilter === 'ams') {
        if (!(s.location || '').toUpperCase().startsWith('AMS')) return false
      } else if (locationFilter === 'none') {
        if (s.location && s.location.trim() !== '') return false
      } else if (locationFilter) {
        if ((s.location || '') !== locationFilter) return false
      }
      return true
    })
  }, [spools, search, vendorFilter, materialFilter, locationFilter])

  return (
    <div style={{position:"relative"}}>

      <div class="fiq-toolbar">
        <input class="fiq-search" type="text" placeholder="Search..." value={search} onInput={e => setSearch(e.target.value)} />
        <select class="fiq-filter" value={vendorFilter} onChange={e => setVendorFilter(e.target.value)}>
          <option value="">All vendors</option>
          {vendors.map(v => <option key={v} value={v}>{v}</option>)}
        </select>
        <select class="fiq-filter" value={materialFilter} onChange={e => setMaterialFilter(e.target.value)}>
          <option value="">All materials</option>
          {materials.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <select class="fiq-filter" value={locationFilter} onChange={e => setLocationFilter(e.target.value)}>
          <option value="">All locations</option>
          <option value="ams">In AMS</option>
          <option value="Shelf">Shelf</option>
          <option value="New">New</option>
          <option value="none">Unassigned</option>
        </select>
        <div class="fiq-spacer" />
        {emptySpools.length > 0 && (
          <button class="fiq-btn-archive" onClick={() => setArchiveConfirm(true)} disabled={archiving}>
            {archiving ? 'Archiving...' : `Archive empty (${emptySpools.length})`}
          </button>
        )}
        <button class="fiq-btn-add" onClick={() => { setAdding(true); setEditId(null) }}>+ Add spool</button>
      </div>

      {archiveConfirm && (
        <ConfirmDialog
          message={`Archive ${emptySpools.length} empty spool${emptySpools.length !== 1 ? 's' : ''}? They will be hidden from the list.`}
          confirmLabel="Archive"
          onConfirm={doArchive}
          onCancel={() => setArchiveConfirm(false)}
        />
      )}

      {adding && (
        <SpoolAddRow
          filaments={filaments}
          onCreate={async (data) => { await createSpool(data); setAdding(false) }}
          onCancel={() => setAdding(false)}
        />
      )}

      <div class="fiq-table">
        {filtered.map(spool => {
          const f = spool.filament || {}
          const remaining = Math.round(spool.remaining_weight || 0)
          const initial = Math.round(spool.initial_weight || 1000)
          const pct = initial > 0 ? Math.min(100, Math.round((remaining / initial) * 100)) : 0
          const isLow = remaining > 0 && remaining < 100
          const color = f.color_hex || '555555'
          const expanded = editId === spool.id

          return (
            <div key={spool.id} class={`fiq-row${expanded ? ' expanded' : ''}`}>
              <div class="fiq-row-main" onClick={() => { setEditId(expanded ? null : spool.id); setAdding(false) }}>
                <ColorDot hex={color} />
                <div>
                  <div class="fiq-fname">{f.name || '—'}</div>
                  <div class="fiq-pbar">
                    <div class="fiq-pfill" style={{ width: `${pct}%`, background: `#${color}` }} />
                  </div>
                  <div class="fiq-row-sub-line">
                    {spool.location && <LocationBadge location={spool.location} />}
                    <span class="fiq-fsub" style={{ marginTop: 0 }}>
                      {f.vendor?.name || ''}{f.vendor?.name ? ' · ' : ''}{pct}%
                    </span>
                  </div>
                </div>
                <div class="fiq-cell">{f.vendor?.name || ''}</div>
                <div><MatBadge material={f.material} /></div>
                <div><span class="fiq-id-badge">#{spool.id}</span></div>
                <div class={`fiq-cell${isLow ? ' low' : ' weight'}`}>
                  {remaining}g{isLow ? ' ⚠' : ''}
                </div>
                <div class="fiq-row-acts">
                  <button class={`fiq-icon-btn${expanded ? ' icon-active' : ''}`} onClick={e => { e.stopPropagation(); setEditId(expanded ? null : spool.id); setAdding(false) }}>✏</button>
                </div>
              </div>
              {expanded && (
                <SpoolEditPanel
                  spool={spool}
                  hass={hass}
                  onSave={(id, patch) => updateSpool(id, patch).then(() => setEditId(null))}
                  onCancel={() => setEditId(null)}
                  onDelete={(id) => deleteSpool(id).then(() => setEditId(null))}
                />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
