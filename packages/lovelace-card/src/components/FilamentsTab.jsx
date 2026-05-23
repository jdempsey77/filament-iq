import { useState, useMemo, useEffect } from 'preact/hooks'
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

function FilamentEditPanel({ filament, vendors, onSave, onCancel, onDelete, hass }) {
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

  const [profileStatus, setProfileStatus] = useState('idle')
  const [profileData, setProfileData] = useState(null)
  const [profileAction, setProfileAction] = useState(null)
  const [profileError, setProfileError] = useState(null)

  useEffect(() => {
    if (!hass) return
    const requestId = Math.random().toString(36).slice(2)
    let unsub = null
    let timer = null
    let done = false

    const cleanup = () => {
      if (timer) { clearTimeout(timer); timer = null }
      if (unsub) { unsub(); unsub = null }
    }

    const run = async () => {
      try {
        unsub = await hass.connection.subscribeEvents((event) => {
          const d = event.data || {}
          if (d.request_id !== requestId || done) return
          done = true
          cleanup()
          setProfileData(d)
          setProfileStatus(d.status || (d.matched ? 'candidate' : 'unverified'))
        }, 'filament_iq_profile_lookup_response')

        hass.connection.sendMessage({
          type: 'fire_event',
          event_type: 'filament_iq_profile_lookup_request',
          event_data: { request_id: requestId, filament_id: filament.id },
        })
        setProfileStatus('loading')

        timer = setTimeout(() => {
          if (done) return
          done = true
          cleanup()
          setProfileStatus('error')
        }, 10000)
      } catch (e) {
        cleanup()
        setProfileStatus('error')
      }
    }

    run()
    return cleanup
  }, [])

  const sendVerify = async (eventData, onResult) => {
    let unsub = null
    let timer = null
    let done = false

    const cleanup = () => {
      if (timer) { clearTimeout(timer); timer = null }
      if (unsub) { unsub(); unsub = null }
    }

    try {
      unsub = await hass.connection.subscribeEvents((event) => {
        const d = event.data || {}
        if (d.filament_id !== filament.id || done) return
        done = true
        cleanup()
        onResult(d, null)
      }, 'filament_iq_profile_verify_result')

      hass.connection.sendMessage({
        type: 'fire_event',
        event_type: 'filament_iq_profile_verify',
        event_data: eventData,
      })

      timer = setTimeout(() => {
        if (done) return
        done = true
        cleanup()
        onResult(null, 'timeout')
      }, 10000)
    } catch (e) {
      cleanup()
      onResult(null, e.message || 'error')
    }
  }

  const handleConfirm = async () => {
    if (!hass || profileAction) return
    setProfileAction('confirming')
    await sendVerify(
      {
        filament_id: filament.id,
        action: 'confirm',
        profile_id: profileData.profile_id,
        profile_url: profileData.profile_url,
        profile_name: profileData.profile_name,
      },
      (result, err) => {
        if (err || !result?.success) {
          setProfileError(result?.error || err || 'Verification failed')
          setProfileAction(null)
        } else {
          setProfileStatus('verified')
          setProfileAction(null)
        }
      }
    )
  }

  const handleReject = async () => {
    if (!hass) return
    setProfileAction('rejecting')
    await sendVerify(
      { filament_id: filament.id, action: 'reject' },
      () => {
        setProfileStatus('idle')
        setProfileData(null)
        setProfileAction(null)
      }
    )
  }

  const handleNoMatch = async () => {
    if (!hass || profileAction) return
    setProfileAction('rejecting')
    await sendVerify(
      { filament_id: filament.id, action: 'no_match' },
      () => {
        setProfileStatus('no_profile_exists')
        setProfileAction(null)
      }
    )
  }

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

      {profileStatus !== 'idle' && (
        <div class="fiq-profile-section">
          <div class="fiq-profile-label">3D filament profile</div>

          {profileStatus === 'loading' && (
            <div class="fiq-profile-loading">Looking up profile...</div>
          )}

          {profileStatus === 'error' && (
            <div class="fiq-profile-loading">Profile lookup unavailable</div>
          )}

          {profileStatus === 'verified' && profileData && (
            <>
              <div class="fiq-profile-match-row">
                <span class="fiq-profile-badge fiq-profile-verified">✓ Verified</span>
                <span class="fiq-profile-name">{profileData.profile_name}</span>
              </div>
              <div class="fiq-profile-actions">
                <a href={profileData.profile_url} target="_blank" class="fiq-profile-btn">
                  View profile ↗
                </a>
                <button class="fiq-profile-btn fiq-profile-btn-danger" onClick={handleReject}>
                  Unlink
                </button>
              </div>
            </>
          )}

          {profileStatus === 'candidate' && profileData && (
            <>
              <div class="fiq-profile-match-row">
                <span class="fiq-profile-badge fiq-profile-candidate">? Candidate</span>
                <span class="fiq-profile-name">{profileData.profile_name}</span>
              </div>
              <div class="fiq-profile-hint">
                Is this the right profile? View it first, then confirm.
                Confirming links all spools of this filament type.
              </div>
              <div class="fiq-profile-actions">
                <a href={profileData.profile_url} target="_blank" class="fiq-profile-btn">
                  View candidate ↗
                </a>
                <button
                  class="fiq-profile-btn fiq-profile-btn-confirm"
                  onClick={handleConfirm}
                  disabled={profileAction !== null}
                >
                  {profileAction === 'confirming' ? 'Confirming...' : '✓ Confirm'}
                </button>
                <button
                  class="fiq-profile-btn fiq-profile-btn-danger"
                  onClick={handleNoMatch}
                  disabled={profileAction !== null}
                >
                  ✕ Wrong
                </button>
              </div>
            </>
          )}

          {(profileStatus === 'no_profile_exists' || profileStatus === 'unverified') && (
            <>
              <div class="fiq-profile-match-row">
                <span class="fiq-profile-badge fiq-profile-none">— No profile</span>
                <span class="fiq-profile-name">No match on 3dfilamentprofiles.com</span>
              </div>
              <div class="fiq-profile-actions">
                <a href="https://3dfilamentprofiles.com" target="_blank" class="fiq-profile-btn">
                  Search manually ↗
                </a>
                <button class="fiq-profile-btn" onClick={handleReject}>Reset</button>
              </div>
            </>
          )}

          {profileError && (
            <div class="fiq-profile-loading" style={{ color: '#ff453a', marginTop: '4px' }}>
              {profileError}
            </div>
          )}
        </div>
      )}

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

export function FilamentsTab({ filaments, vendors, updateFilament, deleteFilament, createFilament, client, hass }) {
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
                  hass={hass}
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
