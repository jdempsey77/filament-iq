import { useState, useCallback, useEffect, useRef } from 'preact/hooks'

let _db = null
let _dbLoading = false
let _dbCallbacks = []

async function loadDB(client) {
  if (_db) return _db
  if (_dbLoading) return new Promise(r => _dbCallbacks.push(r))
  _dbLoading = true
  try {
    const data = await client.call('GET', '/api/v1/external/filament')
    _db = Array.isArray(data) ? data : []
  } catch (e) {
    _db = []
  }
  _dbLoading = false
  _dbCallbacks.forEach(cb => cb(_db))
  _dbCallbacks = []
  return _db
}

function fuzzyScore(result, query) {
  const haystack = [result.manufacturer, result.name, result.material]
    .filter(Boolean).join(' ').toLowerCase()
  const words = query.toLowerCase().trim().split(/\s+/).filter(Boolean)
  if (!words.length) return 0
  let score = 0
  for (const word of words) {
    if (haystack.includes(word)) score++
    if (result.manufacturer?.toLowerCase().includes(word)) score += 0.5
    if (result.material?.toLowerCase() === word) score += 0.5
  }
  return score
}

function searchDB(db, query, limit = 30) {
  if (!query || query.trim().length < 2) return []
  const results = []
  for (const item of db) {
    const score = fuzzyScore(item, query)
    if (score > 0) results.push({ item, score })
  }
  results.sort((a, b) => b.score - a.score)
  return results.slice(0, limit).map(r => r.item)
}

export function SpoolmanDBImport({ client, vendors, onImport, onCancel }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [dbLoading, setDbLoading] = useState(false)
  const [selected, setSelected] = useState(null)
  const [vendorId, setVendorId] = useState('')
  const [importing, setImporting] = useState(false)
  const dbRef = useRef(null)

  useEffect(() => {
    setDbLoading(true)
    loadDB(client).then(db => {
      dbRef.current = db
      setDbLoading(false)
    })
  }, [client])

  const handleQuery = useCallback((e) => {
    const q = e.target.value
    setQuery(q)
    setSelected(null)
    if (dbRef.current) {
      setResults(searchDB(dbRef.current, q))
    }
  }, [])

  const handleSelect = (result) => {
    setSelected(result)
    const match = (vendors || []).find(v =>
      v.name.toLowerCase() === (result.manufacturer || '').toLowerCase()
    )
    setVendorId(match ? String(match.id) : '')
  }

  const handleImport = async () => {
    if (!selected) return
    setImporting(true)
    try {
      const payload = {
        name: selected.name,
        material: selected.material,
        color_hex: selected.color_hex || undefined,
        density: selected.density || undefined,
        diameter: selected.diameter || undefined,
        weight: selected.weight || undefined,
        spool_weight: selected.spool_weight || undefined,
      }
      if (selected.extruder_temp) payload.settings_extruder_temp = selected.extruder_temp
      if (selected.bed_temp) payload.settings_bed_temp = selected.bed_temp

      if (vendorId) {
        payload.vendor_id = Number(vendorId)
      } else if (selected.manufacturer) {
        const newVendor = await client.call('POST', '/api/v1/vendor', {
          name: selected.manufacturer,
        })
        if (newVendor?.id) payload.vendor_id = newVendor.id
      }

      await onImport(payload)
    } finally {
      setImporting(false)
    }
  }

  return (
    <div class="fiq-import-panel">
      <div class="fiq-import-header">
        <span class="fiq-import-title">
          SpoolmanDB
          {dbLoading && (
            <span style={{ marginLeft: '8px', color: 'var(--hint)', fontSize: '10px' }}>
              Loading database...
            </span>
          )}
          {!dbLoading && dbRef.current && (
            <span style={{ marginLeft: '8px', color: 'var(--hint)', fontSize: '10px' }}>
              {dbRef.current.length.toLocaleString()} filaments
            </span>
          )}
        </span>
        <button class="fiq-icon-btn" onClick={onCancel}>✕</button>
      </div>

      <input
        class="fiq-import-search"
        type="text"
        placeholder="Type to search — e.g. 'bambu red pla' or 'overture petg gray'"
        value={query}
        onInput={handleQuery}
        disabled={dbLoading}
        autoFocus
      />

      {!dbLoading && query.length >= 2 && results.length === 0 && (
        <div class="fiq-import-status">No results for "{query}"</div>
      )}

      {results.length > 0 && (
        <div class="fiq-import-results">
          {results.map(r => (
            <div
              key={r.id}
              class={`fiq-import-row${selected?.id === r.id ? ' selected' : ''}`}
              onClick={() => handleSelect(r)}
            >
              <div
                class="fiq-color-dot"
                style={{ background: r.color_hex ? `#${r.color_hex}` : '#555' }}
              />
              <div>
                <div class="fiq-import-row-name">{r.manufacturer} — {r.name}</div>
                <div class="fiq-import-row-sub">{r.material} · {r.weight}g · {r.diameter}mm</div>
              </div>
              {r.color_hex && (
                <div class="fiq-import-row-hex">#{r.color_hex}</div>
              )}
            </div>
          ))}
        </div>
      )}

      {selected && (
        <div class="fiq-import-preview">
          <div class="fiq-import-preview-title">{selected.manufacturer} — {selected.name}</div>
          <div class="fiq-import-preview-fields">
            <div><div class="fiq-id-key">Material</div><div class="fiq-id-val">{selected.material}</div></div>
            <div><div class="fiq-id-key">Color</div><div class="fiq-id-val">#{selected.color_hex}</div></div>
            <div><div class="fiq-id-key">Weight</div><div class="fiq-id-val">{selected.weight}g</div></div>
            <div><div class="fiq-id-key">Diameter</div><div class="fiq-id-val">{selected.diameter}mm</div></div>
            <div><div class="fiq-id-key">Density</div><div class="fiq-id-val">{selected.density} g/cm³</div></div>
            <div><div class="fiq-id-key">Extruder</div><div class="fiq-id-val">{selected.extruder_temp || '—'}°C</div></div>
            <div><div class="fiq-id-key">Bed</div><div class="fiq-id-val">{selected.bed_temp || '—'}°C</div></div>
            <div><div class="fiq-id-key">Spool wt</div><div class="fiq-id-val">{selected.spool_weight || '—'}g</div></div>
          </div>
          <div class="fiq-import-preview-footer">
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span class="fiq-field-label" style={{ margin: 0 }}>Vendor</span>
              <select class="fiq-select" style={{ width: 'auto' }}
                value={vendorId}
                onChange={e => setVendorId(e.target.value)}
              >
                <option value="">+ Create "{selected.manufacturer}"</option>
                {(vendors || []).map(v => (
                  <option key={v.id} value={String(v.id)}>{v.name}</option>
                ))}
              </select>
            </div>
            <div style={{ display: 'flex', gap: '6px' }}>
              <button class="fiq-btn-cancel" onClick={onCancel}>Cancel</button>
              <button class="fiq-btn-save" onClick={handleImport} disabled={importing}>
                {importing ? 'Importing...' : 'Import filament'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
