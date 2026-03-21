import { useState, useMemo } from 'preact/hooks'
import { ConfirmDialog } from './ConfirmDialog'

function VendorEditPanel({ vendor, onSave, onCancel, onDelete }) {
  const [name, setName] = useState(vendor.name || '')
  const [comment, setComment] = useState(vendor.comment || '')
  const [confirming, setConfirming] = useState(false)
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    setSaving(true)
    try {
      await onSave(vendor.id, { name, comment })
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
          <div class="fiq-field-label">Comment</div>
          <input class="fiq-input" value={comment} onInput={e => setComment(e.target.value)} />
        </div>
      </div>
      <div class="fiq-panel-footer">
        <button class="fiq-btn-del" onClick={() => setConfirming(true)} disabled={saving}>Delete vendor</button>
        <div class="fiq-btn-group">
          <button class="fiq-btn-cancel" onClick={onCancel} disabled={saving}>Cancel</button>
          <button class="fiq-btn-save" onClick={handleSave} disabled={saving}>{saving ? 'Saving...' : 'Save changes'}</button>
        </div>
      </div>
      {confirming && (
        <ConfirmDialog
          message="Delete this vendor?"
          onConfirm={() => { setConfirming(false); onDelete(vendor.id) }}
          onCancel={() => setConfirming(false)}
        />
      )}
    </div>
  )
}

function VendorAddRow({ onCreate, onCancel }) {
  const [name, setName] = useState('')
  const [comment, setComment] = useState('')
  const [saving, setSaving] = useState(false)

  const handleCreate = async () => {
    setSaving(true)
    try {
      await onCreate({ name, comment })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div class="fiq-add-row">
      <div class="fiq-add-title">New vendor</div>
      <div class="fiq-fields">
        <div>
          <div class="fiq-field-label">Name</div>
          <input class="fiq-input" value={name} onInput={e => setName(e.target.value)} placeholder="Vendor name" />
        </div>
        <div>
          <div class="fiq-field-label">Comment</div>
          <input class="fiq-input" value={comment} onInput={e => setComment(e.target.value)} />
        </div>
      </div>
      <div class="fiq-panel-footer">
        <div />
        <div class="fiq-btn-group">
          <button class="fiq-btn-cancel" onClick={onCancel} disabled={saving}>Cancel</button>
          <button class="fiq-btn-save" onClick={handleCreate} disabled={saving || !name}>{saving ? 'Creating...' : 'Create vendor'}</button>
        </div>
      </div>
    </div>
  )
}

export function VendorsTab({ vendors, filaments, updateVendor, deleteVendor, createVendor }) {
  const [search, setSearch] = useState('')
  const [editId, setEditId] = useState(null)
  const [adding, setAdding] = useState(false)

  const filamentCounts = useMemo(() => {
    const counts = {}
    ;(filaments || []).forEach(f => {
      const vid = f.vendor?.id
      if (vid) counts[vid] = (counts[vid] || 0) + 1
    })
    return counts
  }, [filaments])

  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    return (vendors || []).filter(v => {
      if (q && !(v.name || '').toLowerCase().includes(q)) return false
      return true
    })
  }, [vendors, search])

  return (
    <div>
      <div class="fiq-toolbar">
        <input class="fiq-search" type="text" placeholder="Search..." value={search} onInput={e => setSearch(e.target.value)} />
        <div class="fiq-spacer" />
        <button class="fiq-btn-add" onClick={() => { setAdding(true); setEditId(null) }}>+ Add vendor</button>
      </div>

      {adding && (
        <VendorAddRow
          onCreate={async (data) => { await createVendor(data); setAdding(false) }}
          onCancel={() => setAdding(false)}
        />
      )}

      <div class="fiq-table">
        {filtered.map(vendor => {
          const expanded = editId === vendor.id
          return (
            <div key={vendor.id} class={`fiq-row${expanded ? ' expanded' : ''}`}>
              <div class="fiq-row-main cols-4" onClick={() => { setEditId(expanded ? null : vendor.id); setAdding(false) }}>
                <div>
                  <div class="fiq-fname">{vendor.name || '—'}</div>
                  <div class="fiq-fsub">{vendor.external_id || ''}</div>
                </div>
                <div class="fiq-cell">{vendor.comment || ''}</div>
                <div class="fiq-cell">{filamentCounts[vendor.id] || 0} filaments</div>
                <div class="fiq-row-acts">
                  <button class={`fiq-icon-btn${expanded ? ' icon-active' : ''}`} onClick={e => { e.stopPropagation(); setEditId(expanded ? null : vendor.id); setAdding(false) }}>✏</button>
                </div>
              </div>
              {expanded && (
                <VendorEditPanel
                  vendor={vendor}
                  onSave={(id, patch) => updateVendor(id, patch).then(() => setEditId(null))}
                  onCancel={() => setEditId(null)}
                  onDelete={(id) => deleteVendor(id).then(() => setEditId(null))}
                />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
