import { useState, useMemo, useEffect, useCallback } from 'preact/hooks'
import { ConfirmDialog } from './ConfirmDialog'
import { LocationSelect } from './LocationSelect'

const LOCATION_TO_SLOT = {
  'AMS1_Slot1':   1,
  'AMS1_Slot2':   2,
  'AMS1_Slot3':   3,
  'AMS1_Slot4':   4,
  'AMS128_Slot1': 5,
  'AMS129_Slot1': 6,
  'AMS130_Slot1': 7,
}

const SLOT_TO_LOCATION = Object.fromEntries(
  Object.entries(LOCATION_TO_SLOT).map(([loc, slot]) => [slot, loc])
)

const SLOT_LABELS = {
  1: 'AMS 1 · Slot 1',
  2: 'AMS 1 · Slot 2',
  3: 'AMS 1 · Slot 3',
  4: 'AMS 1 · Slot 4',
  5: 'HT1 · Slot 5',
  6: 'HT2 · Slot 6',
  7: 'HT3 · Slot 7',
}

function parseNavIntent(intent) {
  if (!intent) return null
  const idx = intent.indexOf(':')
  if (idx === -1) return null
  const type = intent.slice(0, idx)
  const value = intent.slice(idx + 1)
  if (type === 'spool' && value && !isNaN(parseInt(value, 10)) && parseInt(value, 10) > 0) {
    return { type: 'spool', id: parseInt(value, 10) }
  }
  // Reserved for future: 'slot', 'action'
  return null
}

/**
 * Returns spools eligible for slot binding.
 * Uses .filter() to evaluate ALL spools — never exits early on Empty/New spools.
 * A spool with location: Empty at index N must not prevent spools at N+1, N+2, …
 * from appearing in the result.
 */
export function getBindableSpools(spools) {
  return (spools || []).filter(s => {
    if (s.archived) return false
    const loc = (s.location || '').toLowerCase()
    // Skip spools that are empty (depleted) or still in the "New" staging location.
    // Critically: we use .filter() so iteration continues past every excluded spool.
    return loc !== 'empty' && loc !== 'new'
  })
}

function SlotBindRow({ spools, onBind, onCancel }) {
  const [slotNum, setSlotNum] = useState('')
  const [spoolId, setSpoolId] = useState('')
  const [saving, setSaving] = useState(false)

  const bindable = useMemo(() => getBindableSpools(spools), [spools])

  const handleBind = async () => {
    if (!slotNum || !spoolId) return
    setSaving(true)
    try {
      const location = SLOT_TO_LOCATION[Number(slotNum)]
      await onBind(Number(spoolId), location)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div class="fiq-add-row">
      <div class="fiq-add-title">Manual slot bind</div>
      <div class="fiq-add-fields">
        <div>
          <div class="fiq-field-label">Slot</div>
          <select class="fiq-select" value={slotNum} onChange={e => { setSlotNum(e.target.value); setSpoolId('') }}>
            <option value="">— Select slot —</option>
            {Object.entries(SLOT_LABELS).map(([num, label]) => (
              <option key={num} value={num}>{label}</option>
            ))}
          </select>
        </div>
        <div>
          <div class="fiq-field-label">Spool</div>
          <select class="fiq-select" value={spoolId} onChange={e => setSpoolId(e.target.value)} disabled={!slotNum}>
            <option value="">— Select spool —</option>
            {bindable.map(s => {
              const f = s.filament || {}
              const vendor = f.vendor?.name || ''
              const name = f.name || '?'
              const mat = f.material || ''
              const label = [vendor, name, mat].filter(Boolean).join(' · ')
              return (
                <option key={s.id} value={String(s.id)}>#{s.id} — {label}</option>
              )
            })}
          </select>
        </div>
      </div>
      <div class="fiq-panel-footer">
        <div />
        <div class="fiq-btn-group">
          <button class="fiq-btn-cancel" onClick={onCancel} disabled={saving}>Cancel</button>
          <button class="fiq-btn-save" onClick={handleBind} disabled={saving || !slotNum || !spoolId}>
            {saving ? 'Binding...' : 'Bind'}
          </button>
        </div>
      </div>
    </div>
  )
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
  else if (location === 'AMS130_Slot1') label = 'HT3 · Slot 7'
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

function SpoolEditPanel({ spool, hass, onSave, onCancel, onDelete, onPrintLabel, printingLabel }) {
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
        <div class="fiq-btn-group">
          <button class="fiq-btn-del" onClick={() => setConfirming(true)} disabled={saving}>Delete spool</button>
          <button
            class="fiq-btn-print"
            onClick={() => onPrintLabel && onPrintLabel(spool.id)}
            disabled={saving || printingLabel}
          >
            {printingLabel ? '⏳ Printing...' : '🖨 Print Label'}
          </button>
        </div>
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

function SpoolAddRow({ filaments, onCreate, onCancel, hass }) {
  const [filamentId, setFilamentId] = useState('')
  const [location, setLocation] = useState('Shelf')
  const [initialWeight, setInitialWeight] = useState(1000)
  const [remainingWeight, setRemainingWeight] = useState(1000)
  const [printLabel, setPrintLabel] = useState(true)
  const [saving, setSaving] = useState(false)

  const handleCreate = async () => {
    setSaving(true)
    try {
      const newSpool = await onCreate({
        filament_id: Number(filamentId),
        location,
        initial_weight: Number(initialWeight),
        remaining_weight: Number(remainingWeight),
      })
      // Fire print label event if checkbox is checked and we got a spool ID back
      if (printLabel && newSpool && newSpool.id && hass) {
        try {
          hass.connection.sendMessage({
            type: 'fire_event',
            event_type: 'filament_iq_print_label',
            event_data: { spool_id: newSpool.id },
          })
        } catch (e) {
          // Non-fatal — spool was created successfully
        }
      }
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
      <div class="fiq-add-checkbox">
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: '#aaa', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={printLabel}
            onChange={e => setPrintLabel(e.target.checked)}
            style={{ accentColor: '#4a9eff' }}
          />
          Print label & move to shelf after saving
        </label>
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

export function SpoolsTab({ spools, filaments, updateSpool, deleteSpool, createSpool, refresh, hass, getHass, navIntent }) {
  const [search, setSearch] = useState('')
  const [vendorFilter, setVendorFilter] = useState('')
  const [materialFilter, setMaterialFilter] = useState('')
  const [locationFilter, setLocationFilter] = useState('')
  const [editId, setEditId] = useState(null)

  // Nav intent: read once at mount, clear entity, pre-open spool edit panel
  useEffect(() => {
    const parsed = parseNavIntent(navIntent)
    if (parsed?.type === 'spool') {
      try {
        getHass().connection.sendMessage({
          type: 'call_service',
          domain: 'input_text',
          service: 'set_value',
          service_data: {
            entity_id: 'input_text.filament_iq_nav_intent',
            value: '',
          },
        })
      } catch (_) { /* non-fatal — entity may not exist on this install */ }
      setEditId(parsed.id)
    }
  }, [])
  const [adding, setAdding] = useState(false)
  const [binding, setBinding] = useState(false)
  const [archiveConfirm, setArchiveConfirm] = useState(false)
  const [archiving, setArchiving] = useState(false)
  const [printingSpoolId, setPrintingSpoolId] = useState(null)
  const [toast, setToast] = useState(null)

  // Subscribe to label result events
  useEffect(() => {
    if (!hass) return
    let unsub = null
    const subscribe = async () => {
      try {
        unsub = await hass.connection.subscribeEvents((event) => {
          const d = event.data || {}
          if (d.spool_id === printingSpoolId || printingSpoolId) {
            setPrintingSpoolId(null)
            if (d.success) {
              setToast({ msg: 'Label printed — spool moved to shelf', type: 'ok' })
            } else {
              setToast({ msg: `Print failed: ${d.error || 'unknown error'}`, type: 'err' })
            }
            setTimeout(() => setToast(null), 5000)
          }
        }, 'filament_iq_label_result')
      } catch (e) { /* ignore subscription errors */ }
    }
    subscribe()
    return () => { if (unsub) unsub() }
  }, [hass, printingSpoolId])

  // Timeout for in-flight print jobs
  useEffect(() => {
    if (!printingSpoolId) return
    const timer = setTimeout(() => {
      setPrintingSpoolId(null)
      setToast({ msg: 'Print label timed out', type: 'err' })
      setTimeout(() => setToast(null), 5000)
    }, 15000)
    return () => clearTimeout(timer)
  }, [printingSpoolId])

  const handleReconcile = useCallback(() => {
    getHass()?.callService('input_button', 'press', {
      entity_id: 'input_button.filament_iq_reconcile_now',
    })
  }, [getHass])

  const handleExport = useCallback(() => {
    const rows = [
      ['ID', 'Name', 'Material', 'Vendor', 'Color', 'Remaining (g)', 'Location', 'Lot Nr'],
      ...(spools || []).filter(s => !s.archived).map(s => [
        s.id,
        s.filament?.name || '',
        s.filament?.material || '',
        s.filament?.vendor?.name || '',
        s.filament?.color_hex || '',
        Math.round(s.remaining_weight || 0),
        s.location || '',
        s.lot_nr || '',
      ])
    ]
    const csv = rows.map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `filament-iq-spools-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }, [spools])

  const handlePrintLabel = useCallback((spoolId) => {
    if (!hass) return
    setPrintingSpoolId(spoolId)
    try {
      hass.connection.sendMessage({
        type: 'fire_event',
        event_type: 'filament_iq_print_label',
        event_data: { spool_id: spoolId },
      })
    } catch (e) {
      setPrintingSpoolId(null)
      setToast({ msg: `Print failed: ${e.message || e}`, type: 'err' })
      setTimeout(() => setToast(null), 5000)
    }
  }, [hass])

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

      {toast && (
        <div class={`fiq-toast ${toast.type === 'err' ? 'fiq-toast-err' : 'fiq-toast-ok'}`}>
          {toast.msg}
        </div>
      )}

      <div class="fiq-bind-row">
        <button class="fiq-btn-bind" onClick={() => { setBinding(!binding); setAdding(false); setEditId(null) }}>⇄ Bind slot</button>
        <button class="fiq-btn-bind" onClick={handleReconcile}>↺ Reconcile</button>
        <button class="fiq-btn-bind" onClick={handleExport}>↓ CSV</button>
      </div>

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
        <button class="fiq-btn-add" onClick={() => { setAdding(true); setBinding(false); setEditId(null) }}>+ Add spool</button>
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
          hass={hass}
          onCreate={async (data) => { const result = await createSpool(data); setAdding(false); return result }}
          onCancel={() => setAdding(false)}
        />
      )}

      {binding && (
        <SlotBindRow
          spools={spools}
          onBind={async (spoolId, location) => {
            await updateSpool(spoolId, { location })
            // Mirror SpoolEditPanel: fire FILAMENT_IQ_SLOT_ASSIGNED so the
            // reconciler updates input_text.ams_slot_N_spool_id immediately.
            const slot = LOCATION_TO_SLOT[location]
            if (slot && hass) {
              hass.connection.sendMessage({
                type: 'fire_event',
                event_type: 'FILAMENT_IQ_SLOT_ASSIGNED',
                event_data: { slot, spool_id: spoolId },
              })
            }
            setBinding(false)
          }}
          onCancel={() => setBinding(false)}
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
              <div class="fiq-row-main" onClick={() => { setEditId(expanded ? null : spool.id); setAdding(false); setBinding(false) }}>
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
                  <button class={`fiq-icon-btn${expanded ? ' icon-active' : ''}`} onClick={e => { e.stopPropagation(); setEditId(expanded ? null : spool.id); setAdding(false); setBinding(false) }}>✏</button>
                </div>
              </div>
              {expanded && (
                <SpoolEditPanel
                  spool={spool}
                  hass={hass}
                  onSave={(id, patch) => updateSpool(id, patch).then(() => setEditId(null))}
                  onCancel={() => setEditId(null)}
                  onDelete={(id) => deleteSpool(id).then(() => setEditId(null))}
                  onPrintLabel={handlePrintLabel}
                  printingLabel={printingSpoolId === spool.id}
                />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
