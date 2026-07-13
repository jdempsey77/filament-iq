import { useState, useMemo, useEffect } from 'preact/hooks'
import { ConfirmDialog } from './ConfirmDialog'
import { SpoolmanDBImport } from './SpoolmanDBImport'
import { useProvider } from '../provider/context'

function ColorDot({ hex, multiColorHexes }) {
  const colors = multiColorHexes ? multiColorHexes.split(',').map(h => `#${h.trim().replace('#','')}`) : null
  const color = hex ? `#${hex}` : '#555'
  const isBlack = !hex || hex.toLowerCase() === '000000'
  const bg = colors && colors.length >= 2
    ? `linear-gradient(135deg, ${colors[0]} 50%, ${colors[1]} 50%)`
    : color
  return (
    <div class="fiq-color-dot" style={{ background: bg, border: isBlack && !colors ? '1px solid #444' : 'none' }} />
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

function FilamentEditPanel({ filament, vendors, onSave, onCancel, onDelete, initialProfileStatus, onProfileStatusChange }) {
  const provider = useProvider()
  const [name, setName] = useState(filament.name || '')
  const [material, setMaterial] = useState(filament.material || '')
  const multiColorHexes = filament.multi_color_hexes || null
  const [vendorId, setVendorId] = useState(String(filament.vendor?.id || ''))
  const [weight, setWeight] = useState(filament.weight ?? '')
  const [diameter, setDiameter] = useState(filament.diameter ?? '')
  const [density, setDensity] = useState(filament.density ?? '')
  const [externalId, setExternalId] = useState(filament.external_id || '')
  const [confirming, setConfirming] = useState(false)
  const [saving, setSaving] = useState(false)

  const [profileStatus, setProfileStatus] = useState(initialProfileStatus || 'idle')
  const [profileData, setProfileData] = useState(null)
  const [profileAction, setProfileAction] = useState(null)
  const [profileError, setProfileError] = useState(null)

  const [manualEntry, setManualEntry] = useState(false)
  const [manualInput, setManualInput] = useState('')
  const [manualError, setManualError] = useState(null)

  function parseProfileId(input) {
    const trimmed = (input || '').trim()
    const bare = parseInt(trimmed, 10)
    if (!isNaN(bare) && String(bare) === trimmed) return bare
    const match = trimmed.match(/\/filament\/details\/(\d+)/)
    if (match) return parseInt(match[1], 10)
    return null
  }

  useEffect(() => {
    if (!provider) return
    let cancelled = false
    if (!initialProfileStatus || initialProfileStatus === 'idle') {
      setProfileStatus('loading')
    }
    provider.rpc('filament.profileLookup', { filament_id: filament.id })
      .then((d) => {
        if (cancelled) return
        setProfileData(d)
        setProfileStatus(d.status || (d.matched ? 'candidate' : 'unverified'))
      })
      .catch(() => {
        if (!cancelled) setProfileStatus('error')
      })
    return () => { cancelled = true }
  }, [])

  const sendVerify = async (eventData, onResult) => {
    try {
      const result = await provider.rpc('filament.profileVerify', eventData)
      onResult(result, null)
    } catch (e) {
      onResult(null, e.message || 'error')
    }
  }

  const handleConfirm = async () => {
    if (!provider || profileAction) return
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
          onProfileStatusChange?.(filament.id, 'verified')
        }
      }
    )
  }

  const handleReject = async () => {
    if (!provider) return
    setProfileAction('rejecting')
    await sendVerify(
      { filament_id: filament.id, action: 'reject' },
      () => {
        setProfileStatus('idle')
        setProfileData(null)
        setProfileAction(null)
        onProfileStatusChange?.(filament.id, null)
      }
    )
  }

  const handleNoMatch = async () => {
    if (!provider || profileAction) return
    setProfileAction('rejecting')
    await sendVerify(
      { filament_id: filament.id, action: 'no_match' },
      () => {
        setProfileStatus('no_profile_exists')
        setProfileAction(null)
        onProfileStatusChange?.(filament.id, 'no_profile_exists')
      }
    )
  }

  const handleManualConfirm = async () => {
    const profileId = parseProfileId(manualInput)
    if (!profileId) {
      setManualError('Enter a valid profile ID or 3dfilamentprofiles.com URL')
      return
    }
    const profileUrl = `https://3dfilamentprofiles.com/filament/details/${profileId}`
    setProfileAction('confirming')
    await sendVerify(
      {
        filament_id: filament.id,
        action: 'confirm',
        profile_id: profileId,
        profile_url: profileUrl,
        profile_name: manualInput.trim(),
      },
      (result, err) => {
        if (err || !result?.success) {
          setManualError('Failed to save — try again')
          setProfileAction(null)
        } else {
          setProfileStatus('verified')
          setProfileData({ profile_id: profileId, profile_url: profileUrl, profile_name: `Profile #${profileId}` })
          setManualEntry(false)
          setManualInput('')
          setManualError(null)
          setProfileAction(null)
          onProfileStatusChange?.(filament.id, 'verified')
        }
      }
    )
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const patch = { name, material, external_id: externalId }
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
          <div class="fiq-field-label">Colors</div>
          {(multiColorHexes ? multiColorHexes.split(',') : [filament.color_hex]).map((raw, i) => {
            const hex = (raw || '888').trim().replace('#', '')
            return (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: i > 0 ? 4 : 0 }}>
                <span class="fiq-color-preview" style={{ background: `#${hex}` }} />
                <span style={{ fontSize: 12, color: '#e5e5e7' }}>{hex}</span>
              </div>
            )
          })}
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

          {profileStatus === 'verified' && (
            <>
              <div class="fiq-profile-match-row">
                <span class="fiq-profile-badge fiq-profile-verified">✓ Verified</span>
                {profileData && <span class="fiq-profile-name">{profileData.profile_name}</span>}
              </div>
              {profileData && (
                <div class="fiq-profile-actions">
                  <button class="fiq-profile-btn"
                    onClick={() => window.open(profileData.profile_url, '_blank', 'noopener')}>
                    View profile ↗
                  </button>
                  <button class="fiq-profile-btn fiq-profile-btn-danger" onClick={handleReject}>
                    Unlink
                  </button>
                </div>
              )}
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
                <button class="fiq-profile-btn"
                  onClick={() => window.open(profileData.profile_url, '_blank', 'noopener')}>
                  View candidate ↗
                </button>
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
                <button class="fiq-profile-btn"
                  onClick={() => { setManualEntry(true); setManualError(null) }}>
                  Link manually
                </button>
              </div>
              {manualEntry && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input
                      class="fiq-input"
                      style={{ flex: 1, fontSize: 12 }}
                      placeholder="Paste profile URL or ID (e.g. 631)"
                      value={manualInput}
                      onInput={e => { setManualInput(e.target.value); setManualError(null) }}
                    />
                    <button class="fiq-profile-btn fiq-profile-btn-confirm"
                      onClick={handleManualConfirm}
                      disabled={profileAction !== null || !manualInput.trim()}>
                      Link
                    </button>
                    <button class="fiq-profile-btn"
                      onClick={() => { setManualEntry(false); setManualInput(''); setManualError(null) }}>
                      Cancel
                    </button>
                  </div>
                  {manualError && (
                    <div style={{ fontSize: 11, color: '#ff453a', marginTop: 4 }}>
                      {manualError}
                    </div>
                  )}
                </div>
              )}
            </>
          )}

          {(profileStatus === 'no_profile_exists' || profileStatus === 'unverified') && (
            <>
              <div class="fiq-profile-match-row">
                <span class="fiq-profile-badge fiq-profile-none">— No profile</span>
                <span class="fiq-profile-name">No match on 3dfilamentprofiles.com</span>
              </div>
              <div class="fiq-profile-actions">
                <button class="fiq-profile-btn"
                  onClick={() => window.open('https://3dfilamentprofiles.com', '_blank', 'noopener')}>
                  Search manually ↗
                </button>
                <button class="fiq-profile-btn" onClick={handleReject}>Reset</button>
                <button class="fiq-profile-btn"
                  onClick={() => { setManualEntry(true); setManualError(null) }}>
                  Link manually
                </button>
              </div>
              {manualEntry && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input
                      class="fiq-input"
                      style={{ flex: 1, fontSize: 12 }}
                      placeholder="Paste profile URL or ID (e.g. 631)"
                      value={manualInput}
                      onInput={e => { setManualInput(e.target.value); setManualError(null) }}
                    />
                    <button class="fiq-profile-btn fiq-profile-btn-confirm"
                      onClick={handleManualConfirm}
                      disabled={profileAction !== null || !manualInput.trim()}>
                      Link
                    </button>
                    <button class="fiq-profile-btn"
                      onClick={() => { setManualEntry(false); setManualInput(''); setManualError(null) }}>
                      Cancel
                    </button>
                  </div>
                  {manualError && (
                    <div style={{ fontSize: 11, color: '#ff453a', marginTop: 4 }}>
                      {manualError}
                    </div>
                  )}
                </div>
              )}
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

function formatSpoolmanError(e) {
  const detail = e?.body?.detail
  if (Array.isArray(detail)) {
    return detail.map(d => {
      const field = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : null
      return field ? `${field}: ${d.msg}` : d.msg
    }).join('; ')
  }
  if (typeof detail === 'string') return detail
  return e?.message || 'Failed to create filament'
}

function FilamentAddRow({ vendors, onCreate, onCancel }) {
  const [name, setName] = useState('')
  const [vendorId, setVendorId] = useState('')
  const [material, setMaterial] = useState('')
  const [colorHex, setColorHex] = useState('')
  const [density, setDensity] = useState('')
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState(null)

  const handleCreate = async () => {
    setSaving(true)
    try {
      const data = { name, material, color_hex: colorHex, weight: 1000, diameter: 1.75, density: Number(density) }
      if (vendorId) data.vendor_id = Number(vendorId)
      await onCreate(data)
    } catch (e) {
      setToast({ msg: formatSpoolmanError(e), type: 'err' })
      setTimeout(() => setToast(null), 5000)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div class="fiq-add-row">
      <div class="fiq-add-title">New filament</div>
      {toast && (
        <div class={`fiq-toast ${toast.type === 'err' ? 'fiq-toast-err' : 'fiq-toast-ok'}`}>
          {toast.msg}
        </div>
      )}
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
        <div>
          <div class="fiq-field-label">Density (g/cm³)</div>
          <input class="fiq-input" type="number" step="0.01" value={density} onInput={e => setDensity(e.target.value)} placeholder="1.24" />
        </div>
      </div>
      <div class="fiq-panel-footer">
        <div />
        <div class="fiq-btn-group">
          <button class="fiq-btn-cancel" onClick={onCancel} disabled={saving}>Cancel</button>
          <button class="fiq-btn-save" onClick={handleCreate} disabled={saving || !name || density === ''}>{saving ? 'Creating...' : 'Create filament'}</button>
        </div>
      </div>
    </div>
  )
}

export function FilamentsTab({ filaments, vendors, updateFilament, deleteFilament, createFilament }) {
  const provider = useProvider()
  const [search, setSearch] = useState('')
  const [vendorFilter, setVendorFilter] = useState('')
  const [materialFilter, setMaterialFilter] = useState('')
  const [profileFilter, setProfileFilter] = useState('')
  const [editId, setEditId] = useState(null)
  const [adding, setAdding] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [profileStatuses, setProfileStatuses] = useState({})
  const [profileStatusesUnavailable, setProfileStatusesUnavailable] = useState(false)

  useEffect(() => {
    if (!provider) return
    let cancelled = false
    provider.rpc('filament.profileBulkStatus')
      .then((statuses) => {
        if (!cancelled) setProfileStatuses(statuses)
      })
      .catch(() => {
        // Silent before: the profile filter dropdown would just show
        // misleadingly empty/wrong results (pStatus undefined for every
        // filament) with no indication why. Each row's own profileLookup
        // effect still surfaces "Profile lookup unavailable" independently
        // -- this only needs to disable the filter, not repeat that message.
        if (!cancelled) setProfileStatusesUnavailable(true)
      })
    return () => { cancelled = true }
  }, [])

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
      if (profileFilter) {
        const pStatus = profileStatuses[String(f.id)]
        if (profileFilter === 'verified' && pStatus !== 'verified') return false
        if (profileFilter === 'needs_verification' && pStatus === 'verified') return false
      }
      return true
    })
  }, [filaments, search, vendorFilter, materialFilter, profileFilter, profileStatuses])

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
        <select
          class="fiq-filter"
          value={profileFilter}
          onChange={e => setProfileFilter(e.target.value)}
          disabled={profileStatusesUnavailable}
          title={profileStatusesUnavailable ? 'Profile status check unavailable' : ''}
        >
          <option value="">{profileStatusesUnavailable ? 'Profiles unavailable' : 'All profiles'}</option>
          <option value="verified">✓ Verified</option>
          <option value="needs_verification">? Needs verification</option>
        </select>
        <div class="fiq-spacer" />
        <button class="fiq-btn-import" onClick={() => { setShowImport(true); setAdding(false); setEditId(null) }}>Import</button>
        <button class="fiq-btn-add" onClick={() => { setAdding(true); setShowImport(false); setEditId(null) }}>+ Add filament</button>
      </div>

      {showImport && (
        <SpoolmanDBImport
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
          const pStatus = profileStatuses[String(fil.id)]
          return (
            <div key={fil.id} class={`fiq-row${expanded ? ' expanded' : ''}`}>
              <div class="fiq-row-main cols-6" onClick={() => { setEditId(expanded ? null : fil.id); setAdding(false) }}>
                <ColorDot hex={fil.color_hex} multiColorHexes={fil.multi_color_hexes} />
                <div>
                  <div class="fiq-fname">{fil.name || '—'}</div>
                  <div class="fiq-fsub">{fil.vendor?.name || ''}{fil.vendor?.name && fil.material ? ' · ' : ''}{fil.material || ''}</div>
                </div>
                <div class="fiq-cell">{fil.vendor?.name || ''}</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <MatBadge material={fil.material} />
                  {pStatus === 'verified' && (
                    <span class="fiq-profile-badge fiq-profile-verified" title="Profile verified">✓</span>
                  )}
                  {pStatus === 'candidate' && (
                    <span class="fiq-profile-badge fiq-profile-candidate" title="Candidate — verify in edit panel">?</span>
                  )}
                </div>
                <div class="fiq-cell weight">
                  <div>{fil.weight ? `${fil.weight}g` : ''}</div>
                  <span class="fiq-id-badge">#{fil.id}</span>
                </div>
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
                  initialProfileStatus={profileStatuses[String(fil.id)] || 'idle'}
                  onProfileStatusChange={(id, status) => {
                    setProfileStatuses(prev => {
                      const next = { ...prev }
                      if (status === null) delete next[String(id)]
                      else next[String(id)] = status
                      return next
                    })
                  }}
                />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
