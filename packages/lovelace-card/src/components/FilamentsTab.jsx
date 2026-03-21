import { useState, useMemo } from 'preact/hooks'
import { ConfirmDialog } from './ConfirmDialog'
import { SpoolmanDBImport } from './SpoolmanDBImport'

function ColorDot({ hex }) {
  const color = hex ? `#${hex}` : '#555'
  const isBlack = !hex || hex.toLowerCase() === '000000'
  return (
    <div class="fiq-color-dot" style={{ background: color, border: isBlack ? '1px solid #444' : 'none' }} />
  )
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

function FilamentEditPanel({ filament, vendors, onSave, onCancel, onDelete }) {
  const [name, setName] = useState(filament.name || '')
  const [material, setMaterial] = useState(filament.material || '')
  const [colorHex, setColorHex] = useState(filament.color_hex || '')
  const [vendorId, setVendorId] = useState(String(filament.vendor?.id || ''))
  const [weight, setWeight] = useState(filament.weight ?? '')
  const [diameter, setDiameter] = useState(filament.diameter ?? '')
  const [density, setDensity] = useState(filament.density ?? '')
  const [externalId, setExternalId] = useState(filament.external_id || '')
  const [confirming, setConfirming] = useState(false)
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    setSaving(true)
    try {
      const patch = { name, material, color_hex: colorHex, external_id: externalId }
      if (vendorId) patch.vendor_id = Number(vendorId)
      if (weight !== '') patch.weight = Number(weight)
      if (diameter !== '') patch.diameter = Number(diameter)
      if (density !== '') patch.density = Number(density)
      await onSave(filament.id, patch)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div class="fiq-edit-panel">
      <div class="fiq-fields">
        <div>
          <div class="fiq-field-label">Name</div>
          <input class="fiq-input" value={name} onInput={e => setName(e.target.value)} />
        </div>
        <div>
          <div class="fiq-field-label">Material</div>
          <input class="fiq-input" value={material} onInput={e => setMaterial(e.target.value)} />
        </div>
        <div>
          <div class="fiq-field-label">Color hex</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span class="fiq-color-preview" style={{ background: `#${colorHex || '888'}` }} />
            <input class="fiq-input" value={colorHex} onInput={e => setColorHex(e.target.value)} />
          </div>
        </div>
        <div>
          <div class="fiq-field-label">Vendor</div>
          <select class="fiq-select" value={vendorId} onChange={e => setVendorId(e.target.value)}>
            <option value="">— Select —</option>
            {(vendors || []).map(v => <option key={v.id} value={String(v.id)}>{v.name}</option>)}
          </select>
        </div>
        <div>
          <div class="fiq-field-label">Weight (g)</div>
          <input class="fiq-input" type="number" value={weight} onInput={e => setWeight(e.target.value)} />
        </div>
        <div>
          <div class="fiq-field-label">Diameter (mm)</div>
          <input class="fiq-input" type="number" step="0.01" value={diameter} onInput={e => setDiameter(e.target.value)} />
        </div>
      </div>
      <div class="fiq-panel-footer">
        <button class="fiq-btn-del" onClick={() => setConfirming(true)} disabled={saving}>Delete filament</button>
        <div class="fiq-btn-group">
          <button class="fiq-btn-cancel" onClick={onCancel} disabled={saving}>Cancel</button>
          <button class="fiq-btn-save" onClick={handleSave} disabled={saving}>{saving ? 'Saving...' : 'Save changes'}</button>
        </div>
      </div>
      {confirming && (
        <ConfirmDialog
          message="Delete this filament?"
          onConfirm={() => { setConfirming(false); onDelete(filament.id) }}
          onCancel={() => setConfirming(false)}
        />
      )}
    </div>
  )
}

function FilamentAddRow({ vendors, onCreate, onCancel }) {
  const [name, setName] = useState('')
  const [vendorId, setVendorId] = useState('')
  const [material, setMaterial] = useState('')
  const [colorHex, setColorHex] = useState('')
  const [saving, setSaving] = useState(false)

  const handleCreate = async () => {
    setSaving(true)
    try {
      const data = { name, material, color_hex: colorHex, weight: 1000, diameter: 1.75 }
      if (vendorId) data.vendor_id = Number(vendorId)
      await onCreate(data)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div class="fiq-add-row">
      <div class="fiq-add-title">New filament</div>
      <div class="fiq-add-fields">
        <div>
          <div class="fiq-field-label">Name</div>
          <input class="fiq-input" value={name} onInput={e => setName(e.target.value)} placeholder="PLA Basic Red" />
        </div>
        <div>
          <div class="fiq-field-label">Vendor</div>
          <select class="fiq-select" value={vendorId} onChange={e => setVendorId(e.target.value)}>
            <option value="">— Select —</option>
            {(vendors || []).map(v => <option key={v.id} value={String(v.id)}>{v.name}</option>)}
          </select>
        </div>
        <div>
          <div class="fiq-field-label">Material</div>
          <input class="fiq-input" value={material} onInput={e => setMaterial(e.target.value)} placeholder="PLA" />
        </div>
        <div>
          <div class="fiq-field-label">Color hex</div>
          <input class="fiq-input" value={colorHex} onInput={e => setColorHex(e.target.value)} placeholder="ff0000" />
        </div>
      </div>
      <div class="fiq-panel-footer">
        <div />
        <div class="fiq-btn-group">
          <button class="fiq-btn-cancel" onClick={onCancel} disabled={saving}>Cancel</button>
          <button class="fiq-btn-save" onClick={handleCreate} disabled={saving || !name}>{saving ? 'Creating...' : 'Create filament'}</button>
        </div>
      </div>
    </div>
  )
}

export function FilamentsTab({ filaments, vendors, updateFilament, deleteFilament, createFilament, client }) {
  const [search, setSearch] = useState('')
  const [vendorFilter, setVendorFilter] = useState('')
  const [materialFilter, setMaterialFilter] = useState('')
  const [editId, setEditId] = useState(null)
  const [adding, setAdding] = useState(false)
  const [showImport, setShowImport] = useState(false)

  const allVendors = useMemo(() => {
    const set = new Set()
    ;(filaments || []).forEach(f => { const v = f.vendor?.name; if (v) set.add(v) })
    return [...set].sort()
  }, [filaments])

  const allMaterials = useMemo(() => {
    const set = new Set()
    ;(filaments || []).forEach(f => { if (f.material) set.add(f.material) })
    return [...set].sort()
  }, [filaments])

  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    return (filaments || []).filter(f => {
      const name = (f.name || '').toLowerCase()
      const vendor = (f.vendor?.name || '').toLowerCase()
      const mat = (f.material || '').toLowerCase()
      if (q && !name.includes(q) && !vendor.includes(q) && !mat.includes(q)) return false
      if (vendorFilter && vendor !== vendorFilter.toLowerCase()) return false
      if (materialFilter && mat !== materialFilter.toLowerCase()) return false
      return true
    })
  }, [filaments, search, vendorFilter, materialFilter])

  return (
    <div>
      <div class="fiq-toolbar">
        <input class="fiq-search" type="text" placeholder="Search..." value={search} onInput={e => setSearch(e.target.value)} />
        <select class="fiq-filter" value={vendorFilter} onChange={e => setVendorFilter(e.target.value)}>
          <option value="">All vendors</option>
          {allVendors.map(v => <option key={v} value={v}>{v}</option>)}
        </select>
        <select class="fiq-filter" value={materialFilter} onChange={e => setMaterialFilter(e.target.value)}>
          <option value="">All materials</option>
          {allMaterials.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <div class="fiq-spacer" />
        <button class="fiq-btn-import" onClick={() => { setShowImport(true); setAdding(false); setEditId(null) }}>Import</button>
        <button class="fiq-btn-add" onClick={() => { setAdding(true); setShowImport(false); setEditId(null) }}>+ Add filament</button>
      </div>

      {showImport && (
        <SpoolmanDBImport
          client={client}
          vendors={vendors || []}
          onImport={async (payload) => {
            await createFilament(payload)
            setShowImport(false)
          }}
          onCancel={() => setShowImport(false)}
        />
      )}

      {adding && (
        <FilamentAddRow
          vendors={vendors}
          onCreate={async (data) => { await createFilament(data); setAdding(false) }}
          onCancel={() => setAdding(false)}
        />
      )}

      <div class="fiq-table">
        {filtered.map(fil => {
          const expanded = editId === fil.id
          return (
            <div key={fil.id} class={`fiq-row${expanded ? ' expanded' : ''}`}>
              <div class="fiq-row-main cols-6" onClick={() => { setEditId(expanded ? null : fil.id); setAdding(false) }}>
                <ColorDot hex={fil.color_hex} />
                <div>
                  <div class="fiq-fname">{fil.name || '—'}</div>
                  <div class="fiq-fsub">{fil.vendor?.name || ''}{fil.vendor?.name && fil.material ? ' · ' : ''}{fil.material || ''}</div>
                </div>
                <div class="fiq-cell">{fil.vendor?.name || ''}</div>
                <div><MatBadge material={fil.material} /></div>
                <div class="fiq-cell weight">{fil.weight ? `${fil.weight}g` : ''}</div>
                <div class="fiq-row-acts">
                  <button class={`fiq-icon-btn${expanded ? ' icon-active' : ''}`} onClick={e => { e.stopPropagation(); setEditId(expanded ? null : fil.id); setAdding(false) }}>✏</button>
                </div>
              </div>
              {expanded && (
                <FilamentEditPanel
                  filament={fil}
                  vendors={vendors}
                  onSave={(id, patch) => updateFilament(id, patch).then(() => setEditId(null))}
                  onCancel={() => setEditId(null)}
                  onDelete={(id) => deleteFilament(id).then(() => setEditId(null))}
                />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
