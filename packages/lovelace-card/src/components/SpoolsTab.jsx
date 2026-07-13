import { useState, useMemo, useEffect, useCallback } from 'preact/hooks'
import { ConfirmDialog } from './ConfirmDialog'
import { LocationSelect } from './LocationSelect'
import { useProvider } from '../provider/context'
import { useSnapshot } from '../hooks/useSnapshot'

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

function ColorDot({ hex, multiColorHexes }) {
  const colors = multiColorHexes ? multiColorHexes.split(',').map(h => `#${h.trim().replace('#','')}`) : null
  const color = hex ? `#${hex}` : '#555'
  const isBlack = !hex || hex.toLowerCase() === '000000'
  const bg = colors && colors.length >= 2
    ? `linear-gradient(135deg, ${colors[0]} 50%, ${colors[1]} 50%)`
    : color
  return (
    <div
      class="fiq-color-dot"
      style={{
        background: bg,
        border: isBlack && !colors ? '1px solid #444' : 'none',
      }}
    />
  )
}

// Replaces a blind "archive everything empty" confirm with a checked list --
// every candidate is pre-checked (the fast path still archives all in one
// click), EXCEPT a spool currently loaded in a slot right now, which starts
// unchecked and flagged (that's the one you'd regret losing). Purely
// client-side: `candidates` is already the loaded `spools` array filtered by
// remaining_weight, and slot occupancy is already in the domain snapshot
// (slots[].spoolId) -- no new endpoint or RPC verb needed for this.
function ArchiveConfirmPanel({ candidates, loadedSpoolIds, selected, onToggle, onConfirm, onCancel, archiving }) {
  const count = selected.size
  return (
    <div class="fiq-add-row">
      <div class="fiq-add-title">Archive empty spools</div>
      <div class="fiq-archive-list">
        {candidates.map(s => {
          const f = s.filament || {}
          const loaded = loadedSpoolIds.has(s.id)
          const lastUsed = s.last_used ? s.last_used.substring(0, 10) : '—'
          return (
            <label key={s.id} class={`fiq-archive-row${loaded ? ' fiq-archive-row-warn' : ''}`}>
              <input type="checkbox" checked={selected.has(s.id)} onChange={() => onToggle(s.id)} />
              <ColorDot hex={f.color_hex || '555555'} multiColorHexes={f.multi_color_hexes} />
              <div class="fiq-archive-row-info">
                <div class="fiq-fname">{f.vendor?.name || ''} {f.name || '—'}</div>
                <div class="fiq-fsub">
                  #{s.id} · last used {lastUsed} · {Math.round(s.remaining_weight || 0)}g
                  {loaded ? ' · Currently loaded in a slot' : ''}
                </div>
              </div>
            </label>
          )
        })}
      </div>
      <div class="fiq-panel-footer">
        <div />
        <div class="fiq-btn-group">
          <button class="fiq-btn-cancel" onClick={onCancel} disabled={archiving}>Cancel</button>
          <button class="fiq-btn-save" onClick={onConfirm} disabled={archiving || count === 0}>
            {archiving ? 'Archiving...' : `Archive ${count}`}
          </button>
        </div>
      </div>
    </div>
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

export function MatBadge({ material }) {
  const m = (material || '').toUpperCase()
  const cls = m === 'PLA' ? 'fiq-mat-pla'
    : m === 'PLA+' ? 'fiq-mat-pla-plus'
    : m === 'PETG' ? 'fiq-mat-petg'
    : m.startsWith('ABS') ? 'fiq-mat-abs'
    : m === 'TPU' ? 'fiq-mat-tpu'
    : 'fiq-mat-other'
  return <span class={`fiq-mat-badge ${cls}`}>{material || '—'}</span>
}

export function SpoolEditPanel({ spool, onSave, onCancel, onDelete, onPrintLabel, onPrintSwatchLabel, printingLabel, printingNiimbotLabel, identity = false }) {
  const provider = useProvider()
  const [remaining, setRemaining] = useState(Math.round(spool.remaining_weight || 0))
  const [location, setLocation] = useState(spool.location || '')
  const [firstUsed, setFirstUsed] = useState(
    spool.first_used ? spool.first_used.substring(0, 10) : ''
  )
  const f = spool.filament || {}
  const identityColorHex = (f.color_hex || '555555').replace('#', '')
  const identityIsBlack = identityColorHex.toLowerCase() === '000000'
  const identityMultiHexes = f.multi_color_hexes ? f.multi_color_hexes.split(',').map(h => `#${h.trim().replace('#','')}`) : null
  const identitySwatchBg = identityMultiHexes && identityMultiHexes.length >= 2
    ? `linear-gradient(135deg, ${identityMultiHexes[0]} 50%, ${identityMultiHexes[1]} 50%)`
    : `#${identityColorHex}`
  const identitySubtitle = [f.vendor?.name, f.material, location || 'Unassigned'].filter(Boolean).join(' · ')
  const [confirming, setConfirming] = useState(false)
  const [saving, setSaving] = useState(false)
  const [showMore, setShowMore] = useState(false)
  const [profileStatus, setProfileStatus] = useState('idle')
  const [profileData, setProfileData] = useState(null)
  const [profileLookedUp, setProfileLookedUp] = useState(false)

  useEffect(() => {
    const rawUrl = spool.filament?.extra?.profile_url
    if (!rawUrl) return
    const url = rawUrl.replace(/^"|"$/g, '').trim()
    if (url && url !== 'null') {
      setProfileStatus('verified')
      setProfileData({ profile_url: url, profile_name: spool.filament?.extra?.profile_name?.replace(/^"|"$/g, '').trim() || '' })
    }
  }, [spool.filament?.extra?.profile_url])

  useEffect(() => {
    if (!showMore || profileLookedUp || !provider || !spool.filament?.id) return
    setProfileLookedUp(true)
    setProfileStatus('loading')
    let cancelled = false
    provider.rpc('filament.profileLookup', { filament_id: spool.filament.id })
      .then((d) => {
        if (cancelled) return
        setProfileData(d)
        setProfileStatus(d.status || 'unverified')
      })
      .catch(() => {
        if (!cancelled) setProfileStatus('error')
      })
    return () => { cancelled = true }
  }, [showMore])

  const handleSave = async () => {
    setSaving(true)
    try {
      await onSave(spool.id, {
        remaining_weight: Number(remaining),
        location,
        ...(firstUsed ? { first_used: firstUsed } : {}),
      })
      // Fire slot.assigned so AppDaemon updates the slot's bound spool id
      const slot = LOCATION_TO_SLOT[location]
      if (slot && provider) {
        provider.rpc('slot.assigned', { slot, spool_id: spool.id })
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <div class="fiq-edit-panel">
      {identity && (
        <div class="fiq-detail-identity">
          <div class="fiq-detail-swatch" style={{ background: identitySwatchBg, border: identityIsBlack && !identityMultiHexes ? '1px solid var(--border)' : 'none' }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div class="fiq-detail-name">{f.name || '—'}</div>
            <div class="fiq-detail-sub">{identitySubtitle}</div>
          </div>
        </div>
      )}
      <div class="fiq-fields">
        <div>
          <div class="fiq-field-label">Remaining (g)</div>
          <input class="fiq-input" type="number" value={remaining} onInput={e => setRemaining(e.target.value)} />
        </div>
        <div>
          <div class="fiq-field-label">Location</div>
          <LocationSelect value={location} onChange={setLocation} />
        </div>
      </div>
      <div
        style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 0 6px', cursor: 'pointer', color: '#8e8e93', fontSize: 12 }}
        onClick={() => setShowMore(v => !v)}
      >
        <svg viewBox="0 0 24 24" style={{ width: 14, height: 14, fill: '#8e8e93', flexShrink: 0 }}>
          <path d="M13,9H11V7H13M13,17H11V11H13M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2Z" />
        </svg>
        More info
        <svg viewBox="0 0 24 24" style={{ width: 14, height: 14, fill: '#8e8e93', flexShrink: 0, transform: showMore ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }}>
          <path d="M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z" />
        </svg>
      </div>
      {showMore && (
        <div style={{ background: '#2c2c2e', borderRadius: 8, padding: '10px 12px', marginBottom: 8 }}>
          <div style={{ marginBottom: 8 }}>
            <div class="fiq-id-key">Lot #</div>
            <div class="fiq-id-val" style={{ wordBreak: 'break-all', whiteSpace: 'normal' }}>{spool.lot_nr || '—'}</div>
          </div>
          <div style={{ marginBottom: 8 }}>
            <div class="fiq-id-key">Spool ID</div>
            <div class="fiq-id-val">#{spool.id}</div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <div>
              <div class="fiq-field-label">First used</div>
              <input class="fiq-input" type="date" value={firstUsed} onInput={e => setFirstUsed(e.target.value)} />
            </div>
            <div>
              <div class="fiq-id-key">Last used</div>
              <div class="fiq-id-val">{spool.last_used ? spool.last_used.substring(0, 10) : '—'}</div>
            </div>
          </div>
          {profileStatus !== 'idle' && (
            <div style={{ marginTop: 10, paddingTop: 10, borderTop: '0.5px solid #3a3a3c' }}>
              <div class="fiq-id-key" style={{ marginBottom: 6 }}>3D Filament Profile</div>
              {profileStatus === 'loading' && (
                <div class="fiq-profile-loading">Looking up profile...</div>
              )}
              {profileStatus === 'verified' && profileData && (
                <div>
                  <div class="fiq-profile-match-row">
                    <span class="fiq-profile-badge fiq-profile-verified">✓ Verified</span>
                    <span class="fiq-profile-name">{profileData.profile_name}</span>
                  </div>
                  <div class="fiq-profile-actions" style={{ marginTop: 6 }}>
                    <button class="fiq-profile-btn"
                      onClick={() => window.open(profileData.profile_url, '_blank', 'noopener')}>
                      View profile ↗
                    </button>
                  </div>
                </div>
              )}
              {profileStatus === 'candidate' && profileData && (
                <div>
                  <div class="fiq-profile-match-row">
                    <span class="fiq-profile-badge fiq-profile-candidate">? Candidate</span>
                    <span class="fiq-profile-name">{profileData.profile_name}</span>
                  </div>
                  <div class="fiq-profile-actions" style={{ marginTop: 6 }}>
                    <button class="fiq-profile-btn"
                      onClick={() => window.open(profileData.profile_url, '_blank', 'noopener')}>
                      View candidate ↗
                    </button>
                    <span class="fiq-profile-loading" style={{ marginLeft: 4 }}>
                      Verify in Filaments tab
                    </span>
                  </div>
                </div>
              )}
              {(profileStatus === 'no_profile_exists' || profileStatus === 'unverified' || profileStatus === 'error') && (
                <div class="fiq-profile-match-row">
                  <span class="fiq-profile-badge fiq-profile-none">— No profile</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}
      <div class="fiq-panel-footer">
        <div class="fiq-btn-group">
          <button class="fiq-btn-del" onClick={() => setConfirming(true)} disabled={saving}>Delete spool</button>
          <button
            class="fiq-btn-print"
            onClick={() => onPrintLabel && onPrintLabel(spool.id)}
            disabled={saving || printingLabel}
          >
            {printingLabel ? '⏳ Printing...' : '🖨 Spool Label'}
          </button>
          <button
            class="fiq-btn-print"
            onClick={() => onPrintSwatchLabel && onPrintSwatchLabel(spool.id)}
            disabled={saving || printingNiimbotLabel || profileStatus !== 'verified'}
            title={profileStatus !== 'verified' ? 'Verify filament profile in Filaments tab to enable swatch printing' : ''}
          >
            {printingNiimbotLabel ? 'Queuing...' : '🖨 Swatch'}
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

function SpoolAddRow({ filaments, onCreate, onCancel }) {
  const provider = useProvider()
  const [filamentId, setFilamentId] = useState('')
  const [initialWeight, setInitialWeight] = useState(1000)
  const [remainingWeight, setRemainingWeight] = useState(1000)
  const [printLabel, setPrintLabel] = useState(true)
  const [quantity, setQuantity] = useState(1)
  const [saving, setSaving] = useState(false)

  const sortedFilaments = useMemo(() =>
    [...(filaments || [])].sort((a, b) => {
      const ak = `${a.vendor?.name || ''} · ${a.name || ''}`
      const bk = `${b.vendor?.name || ''} · ${b.name || ''}`
      return ak.localeCompare(bk)
    }),
    [filaments]
  )

  const handleCreate = async () => {
    setSaving(true)
    try {
      const created = []
      for (let i = 0; i < quantity; i++) {
        const spool = await onCreate({
          filament_id: Number(filamentId),
          location: 'New',
          initial_weight: Number(initialWeight),
          remaining_weight: Number(remainingWeight),
        })
        if (spool?.id) created.push(spool)
      }
      if (printLabel && provider && created.length > 0) {
        for (const spool of created) {
          try {
            provider.rpc('label.print', { spool_id: spool.id, awaitResponse: false })
          } catch (e) {
            // Non-fatal — spool was created successfully
          }
        }
      }
      onCancel()
    } finally {
      setSaving(false)
    }
  }

  const qty = Number(quantity)

  return (
    <div class="fiq-add-row">
      <div class="fiq-add-title">New spool</div>

      <div style={{ marginBottom: 7 }}>
        <div class="fiq-field-label">Filament</div>
        <select class="fiq-select" value={filamentId} onChange={e => setFilamentId(e.target.value)}>
          <option value="">— Select —</option>
          {sortedFilaments.map(f => (
            <option key={f.id} value={String(f.id)}>{f.vendor?.name || '?'} · {f.name || '?'}</option>
          ))}
        </select>
      </div>

      <div style={{ marginBottom: 7 }}>
        <div class="fiq-field-label">Location</div>
        <div style={{ color: '#666', fontSize: 12, padding: '6px 8px', background: '#2c2c2e', borderRadius: 6 }}>
          New · always set automatically
        </div>
      </div>

      <div style={{ marginBottom: 7 }}>
        <div class="fiq-field-label">Initial (g)</div>
        <input class="fiq-input" type="number" value={initialWeight} onInput={e => setInitialWeight(e.target.value)} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 7, marginBottom: 8 }}>
        <div>
          <div class="fiq-field-label">Remaining (g)</div>
          <input class="fiq-input" type="number" value={remainingWeight} onInput={e => setRemainingWeight(e.target.value)} />
        </div>
        <div>
          <div class="fiq-field-label">Quantity</div>
          <select class="fiq-select" value={String(qty)} onChange={e => setQuantity(Number(e.target.value))}>
            {[1,2,3,4,5,6,7,8].map(n => <option key={n} value={String(n)}>{n}</option>)}
          </select>
        </div>
      </div>

      {qty > 1 && (
        <div style={{ color: '#8e8e93', fontSize: 11, marginBottom: 8, padding: '4px 8px', background: '#2c2c2e', borderRadius: 6 }}>
          {qty} identical spools will be created, each with a unique ID
        </div>
      )}

      <div class="fiq-add-checkbox">
        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: '#aaa', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={printLabel}
            onChange={e => setPrintLabel(e.target.checked)}
            style={{ accentColor: '#4a9eff' }}
          />
          Print label
          <span style={{ color: '#666' }}>— prints {qty} label{qty !== 1 ? 's' : ''}</span>
        </label>
      </div>

      <div class="fiq-panel-footer">
        <div />
        <div class="fiq-btn-group">
          <button class="fiq-btn-cancel" onClick={onCancel} disabled={saving}>Cancel</button>
          <button class="fiq-btn-save" onClick={handleCreate} disabled={saving || !filamentId}>
            {saving
              ? (qty > 1 ? `Creating ${qty} spools...` : 'Creating...')
              : (qty > 1 ? `Create ${qty} spools` : 'Create spool')}
          </button>
        </div>
      </div>
    </div>
  )
}

const SPOOL_SORT_COLUMNS = [
  { key: 'name', label: 'Name' },
  { key: 'vendor', label: 'Vendor' },
  { key: 'material', label: 'Material' },
  { key: 'id', label: 'ID' },
  { key: 'remaining', label: 'Remaining' },
]

// isDesktop/selected/onSelect are no-ops unless a desktop shell passes them
// (see FilamentIQCard.jsx) -- mobile behavior (inline expand) is unchanged.
export function SpoolsTab({ spools, filaments, updateSpool, deleteSpool, createSpool, refresh, navIntent, onNavIntentConsumed, isDesktop, selected, onSelect }) {
  const provider = useProvider()
  const [search, setSearch] = useState('')
  const [vendorFilter, setVendorFilter] = useState('')
  const [materialFilter, setMaterialFilter] = useState('')
  const [locationFilter, setLocationFilter] = useState('')
  const [showEmpty, setShowEmpty] = useState(false)
  const [colorFamily, setColorFamily] = useState('')
  const [editId, setEditId] = useState(null)
  const [sortKey, setSortKey] = useState('name')
  const [sortDir, setSortDir] = useState('asc')

  // Nav intent: read once at mount, clear entity, pre-open spool edit panel
  // (inline on mobile, routed to the right DetailPanel on desktop)
  useEffect(() => {
    const parsed = parseNavIntent(navIntent)
    if (parsed?.type === 'spool') {
      try {
        onNavIntentConsumed?.()
      } catch (_) { /* non-fatal — entity may not exist on this install */ }
      if (isDesktop) {
        onSelect?.({ type: 'spool', id: parsed.id })
      } else {
        setEditId(parsed.id)
      }
    }
  }, [])

  const [adding, setAdding] = useState(false)
  const [binding, setBinding] = useState(false)

  const [archiveConfirm, setArchiveConfirm] = useState(false)
  const [archiveSelected, setArchiveSelected] = useState(new Set())
  const [archiving, setArchiving] = useState(false)
  const [printingSpoolId, setPrintingSpoolId] = useState(null)
  const [printingNiimbotSpoolId, setPrintingNiimbotSpoolId] = useState(null)
  const [toast, setToast] = useState(null)

  // Slot occupancy for the archive-confirm list -- already in the domain
  // snapshot (slots[].spoolId), same source SlotsSegment already reads.
  const snapshot = useSnapshot()
  const loadedSpoolIds = useMemo(() => {
    const set = new Set()
    ;(snapshot?.slots || []).forEach(s => {
      const id = parseInt(s.spoolId, 10)
      if (!isNaN(id) && id > 0) set.add(id)
    })
    return set
  }, [snapshot])

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

  const handlePrintLabel = useCallback(async (spoolId) => {
    if (!provider) return
    setPrintingSpoolId(spoolId)
    try {
      const d = await provider.rpc('label.print', { spool_id: spoolId })
      setPrintingSpoolId(null)
      if (d.success) {
        setToast({ msg: 'Label printed — spool moved to shelf', type: 'ok' })
      } else {
        setToast({ msg: `Print failed: ${d.error || 'unknown error'}`, type: 'err' })
      }
      setTimeout(() => setToast(null), 5000)
    } catch (e) {
      setPrintingSpoolId(null)
      const msg = e?.message?.endsWith('timed out') ? 'Print label timed out' : `Print failed: ${e.message || e}`
      setToast({ msg, type: 'err' })
      setTimeout(() => setToast(null), 5000)
    }
  }, [provider])

  const handlePrintSwatchLabel = useCallback(async (spoolId) => {
    if (!provider) return
    setPrintingNiimbotSpoolId(spoolId)
    try {
      const d = await provider.rpc('label.printNiimbot', { spool_id: spoolId })
      setPrintingNiimbotSpoolId(null)
      if (d.success) {
        setToast({ msg: 'Swatch label queued for printing', type: 'ok' })
      } else {
        setToast({ msg: `Swatch print failed: ${d.error || 'unknown error'}`, type: 'err' })
      }
      setTimeout(() => setToast(null), 5000)
    } catch (e) {
      setPrintingNiimbotSpoolId(null)
      const msg = e?.message?.endsWith('timed out') ? 'Swatch print timed out' : `Swatch print failed: ${e.message || e}`
      setToast({ msg, type: 'err' })
      setTimeout(() => setToast(null), 5000)
    }
  }, [provider])

  const emptySpools = useMemo(() =>
    (spools || []).filter(s => !s.archived && ((s.remaining_weight || 0) === 0)),
    [spools]
  )

  const openArchiveConfirm = () => {
    // Pre-check everything except spools currently loaded in a slot --
    // those are the ones you'd regret losing, so they start unchecked.
    setArchiveSelected(new Set(emptySpools.filter(s => !loadedSpoolIds.has(s.id)).map(s => s.id)))
    setArchiveConfirm(true)
  }

  const toggleArchiveSelected = (id) => {
    setArchiveSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const doArchive = async () => {
    setArchiveConfirm(false)
    setArchiving(true)
    try {
      await Promise.all([...archiveSelected].map(id => updateSpool(id, { archived: true })))
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

  function getColorFamily(hex) {
    if (!hex || hex === '000000') return 'Black'
    const r = parseInt(hex.slice(0,2),16)
    const g = parseInt(hex.slice(2,4),16)
    const b = parseInt(hex.slice(4,6),16)
    const max = Math.max(r,g,b), min = Math.min(r,g,b)
    const l = (max+min)/2
    if (max - min < 20) {
      if (l < 40) return 'Black'
      if (l > 210) return 'White'
      return 'Gray'
    }
    const h = max===r ? (g-b)/(max-min) : max===g ? 2+(b-r)/(max-min) : 4+(r-g)/(max-min)
    const hue = ((h*60)+360)%360
    if (hue < 15 || hue >= 345) return 'Red'
    if (hue < 45) return 'Orange'
    if (hue < 75) return 'Yellow'
    if (hue < 165) return 'Green'
    if (hue < 255) return 'Blue'
    if (hue < 285) return 'Purple'
    if (hue < 345) return 'Pink'
    return 'Other'
  }

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
      if (showEmpty ? !((s.remaining_weight || 0) === 0 || s.archived) : false) return false
      if (colorFamily) {
        const hex = (s.filament?.color_hex || s.color_hex || '').replace('#','')
        if (getColorFamily(hex) !== colorFamily) return false
      }
      return true
    })
  }, [spools, search, vendorFilter, materialFilter, locationFilter, showEmpty, colorFamily])

  const sorted = useMemo(() => {
    if (!isDesktop) return filtered
    const dir = sortDir === 'asc' ? 1 : -1
    return [...filtered].sort((a, b) => {
      let av, bv
      if (sortKey === 'id') { av = a.id; bv = b.id }
      else if (sortKey === 'remaining') { av = a.remaining_weight || 0; bv = b.remaining_weight || 0 }
      else if (sortKey === 'vendor') { av = (a.filament?.vendor?.name || '').toLowerCase(); bv = (b.filament?.vendor?.name || '').toLowerCase() }
      else if (sortKey === 'material') { av = (a.filament?.material || '').toLowerCase(); bv = (b.filament?.material || '').toLowerCase() }
      else { av = (a.filament?.name || '').toLowerCase(); bv = (b.filament?.name || '').toLowerCase() }
      if (av < bv) return -1 * dir
      if (av > bv) return 1 * dir
      return 0
    })
  }, [filtered, isDesktop, sortKey, sortDir])

  const handleSort = (key) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('asc') }
  }

  const handleRowClick = (spool) => {
    if (isDesktop) {
      onSelect?.({ type: 'spool', id: spool.id })
    } else {
      setEditId(prev => prev === spool.id ? null : spool.id)
      setAdding(false)
      setBinding(false)
    }
  }

  return (
    <div style={{position:"relative"}}>

      {toast && (
        <div class={`fiq-toast ${toast.type === 'err' ? 'fiq-toast-err' : 'fiq-toast-ok'}`}>
          {toast.msg}
        </div>
      )}

      <div class="fiq-bind-row">
        <button class="fiq-btn-bind" onClick={() => { setBinding(!binding); setAdding(false); setEditId(null) }}>⇄ Bind slot</button>
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
        <select class="fiq-filter" value={colorFamily} onChange={e => setColorFamily(e.target.value)}>
          <option value="">All colors</option>
          {['Black','White','Gray','Red','Orange','Yellow','Green','Blue','Purple','Pink']
            .map(f => <option key={f} value={f}>{f}</option>)}
        </select>
        <button
          class={`fiq-btn-bind${showEmpty ? ' fiq-btn-active' : ''}`}
          onClick={() => setShowEmpty(!showEmpty)}
          style={{ whiteSpace: 'nowrap' }}
        >⊘ Empty</button>
        <div class="fiq-spacer" />
        {emptySpools.length > 0 && (
          <button class="fiq-btn-archive" onClick={openArchiveConfirm} disabled={archiving}>
            {archiving ? 'Archiving...' : `Archive empty (${emptySpools.length})`}
          </button>
        )}
        <button class="fiq-btn-add" onClick={() => { setAdding(true); setBinding(false); setEditId(null) }}>+ Add spool</button>
      </div>

      {archiveConfirm && (
        <ArchiveConfirmPanel
          candidates={emptySpools}
          loadedSpoolIds={loadedSpoolIds}
          selected={archiveSelected}
          onToggle={toggleArchiveSelected}
          onConfirm={doArchive}
          onCancel={() => setArchiveConfirm(false)}
          archiving={archiving}
        />
      )}

      {adding && (
        <SpoolAddRow
          filaments={filaments}
          onCreate={createSpool}
          onCancel={() => setAdding(false)}
        />
      )}

      {binding && (
        <SlotBindRow
          spools={spools}
          onBind={async (spoolId, location) => {
            await updateSpool(spoolId, { location })
            // Mirror SpoolEditPanel: fire FILAMENT_IQ_SLOT_ASSIGNED so AppDaemon
            // updates the slot's bound spool id immediately.
            const slot = LOCATION_TO_SLOT[location]
            if (slot && provider) {
              provider.rpc('slot.assigned', { slot, spool_id: spoolId })
            }
            setBinding(false)
          }}
          onCancel={() => setBinding(false)}
        />
      )}

      {isDesktop && (
        <div class="fiq-table-header">
          <span />
          {SPOOL_SORT_COLUMNS.map(col => (
            <button key={col.key} class="fiq-th-sort" onClick={() => handleSort(col.key)}>
              {col.label}
              {sortKey === col.key && <span class="fiq-th-sort-arrow">{sortDir === 'asc' ? '▲' : '▼'}</span>}
            </button>
          ))}
          <span />
        </div>
      )}

      <div class="fiq-table">
        {sorted.map(spool => {
          const f = spool.filament || {}
          const remaining = Math.round(spool.remaining_weight || 0)
          const initial = Math.round(spool.initial_weight || 1000)
          const pct = initial > 0 ? Math.min(100, Math.round((remaining / initial) * 100)) : 0
          const isLow = remaining > 0 && remaining < 100
          const color = f.color_hex || '555555'
          const expanded = !isDesktop && editId === spool.id
          const isSelected = isDesktop && selected?.type === 'spool' && selected?.id === spool.id

          return (
            <div key={spool.id} class={`fiq-row${expanded ? ' expanded' : ''}${isSelected ? ' fiq-row-selected' : ''}`}>
              <div class="fiq-row-main" onClick={() => handleRowClick(spool)}>
                <ColorDot hex={color} multiColorHexes={f.multi_color_hexes} />
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
                  {isDesktop
                    ? <span class="fiq-cell right">›</span>
                    : <button class={`fiq-icon-btn${expanded ? ' icon-active' : ''}`} onClick={e => { e.stopPropagation(); handleRowClick(spool) }}>✏</button>}
                </div>
              </div>
              {expanded && (
                <SpoolEditPanel
                  spool={spool}
                  onSave={(id, patch) => updateSpool(id, patch).then(() => setEditId(null))}
                  onCancel={() => setEditId(null)}
                  onDelete={(id) => deleteSpool(id).then(() => setEditId(null))}
                  onPrintLabel={handlePrintLabel}
                  onPrintSwatchLabel={handlePrintSwatchLabel}
                  printingLabel={printingSpoolId === spool.id}
                  printingNiimbotLabel={printingNiimbotSpoolId === spool.id}
                />
              )}
            </div>
          )
        })}
      </div>

      {isDesktop && (
        <div class="fiq-table-footer">{sorted.length} spool{sorted.length !== 1 ? 's' : ''}</div>
      )}
    </div>
  )
}
