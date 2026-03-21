import { useState } from 'preact/hooks'

export function AddRow({ fields, onCreate, onCancel }) {
  const [values, setValues] = useState(() => {
    const init = {}
    fields.forEach((f) => {
      init[f.key] = f.default ?? ''
    })
    return init
  })
  const [saving, setSaving] = useState(false)

  const set = (key, val) => setValues((prev) => ({ ...prev, [key]: val }))

  const handleCreate = async () => {
    setSaving(true)
    try {
      const data = {}
      fields.forEach((f) => {
        const v = values[f.key]
        if (v !== '' && v != null) {
          data[f.key] = f.type === 'number' ? Number(v) : v
        }
      })
      await onCreate(data)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div class="fiq-add-row">
      <div class="fiq-edit-fields">
        {fields.map((f) =>
          f.type === 'select' ? (
            <div key={f.key} class="fiq-edit-field">
              <label class="fiq-label">{f.label}</label>
              <select
                class="fiq-input"
                value={values[f.key]}
                onChange={(e) => set(f.key, e.target.value)}
              >
                <option value="">— Select —</option>
                {(f.options || []).map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
          ) : (
            <div key={f.key} class="fiq-edit-field">
              <label class="fiq-label">{f.label}</label>
              <input
                class="fiq-input"
                type={f.type === 'number' ? 'number' : 'text'}
                value={values[f.key]}
                onInput={(e) => set(f.key, e.target.value)}
                placeholder={f.placeholder}
                step={f.step}
              />
            </div>
          )
        )}
      </div>
      <div class="fiq-edit-actions">
        <button class="fiq-btn fiq-btn--ghost" onClick={onCancel} disabled={saving}>Cancel</button>
        <button class="fiq-btn fiq-btn--primary" onClick={handleCreate} disabled={saving}>
          {saving ? 'Creating...' : 'Create'}
        </button>
      </div>
    </div>
  )
}
