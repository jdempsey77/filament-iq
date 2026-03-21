import { useState } from 'preact/hooks'
import { ConfirmDialog } from './ConfirmDialog'

export function InlineEditRow({ entity, entityType, fields, onSave, onDelete, onCancel }) {
  const [values, setValues] = useState(() => {
    const init = {}
    fields.forEach((f) => {
      init[f.key] = f.value ?? ''
    })
    return init
  })
  const [confirming, setConfirming] = useState(false)
  const [saving, setSaving] = useState(false)

  const set = (key, val) => setValues((prev) => ({ ...prev, [key]: val }))

  const handleSave = async () => {
    setSaving(true)
    try {
      const patch = {}
      fields.forEach((f) => {
        if (!f.readOnly) {
          const v = values[f.key]
          patch[f.key] = f.type === 'number' ? (v === '' ? null : Number(v)) : v
        }
      })
      await onSave(patch)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div class="fiq-edit-panel">
      <div class="fiq-edit-fields">
        {fields.map((f) =>
          f.readOnly ? (
            <div key={f.key} class="fiq-edit-field">
              <label class="fiq-label">{f.label}</label>
              <span class="fiq-readonly">{f.value}</span>
            </div>
          ) : f.type === 'select' ? (
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
              <div class="fiq-input-wrap">
                {f.key === 'color_hex' && (
                  <span
                    class="fiq-color-preview"
                    style={{ background: `#${values[f.key] || '888'}` }}
                  />
                )}
                <input
                  class="fiq-input"
                  type={f.type === 'number' ? 'number' : 'text'}
                  value={values[f.key]}
                  onInput={(e) => set(f.key, e.target.value)}
                  step={f.step}
                />
              </div>
            </div>
          )
        )}
      </div>
      <div class="fiq-edit-actions">
        <button class="fiq-btn fiq-btn--ghost" onClick={onCancel} disabled={saving}>Cancel</button>
        <button class="fiq-btn fiq-btn--danger-ghost" onClick={() => setConfirming(true)} disabled={saving}>Delete</button>
        <button class="fiq-btn fiq-btn--primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
      {confirming && (
        <ConfirmDialog
          message={`Delete this ${entityType}?`}
          onConfirm={() => { setConfirming(false); onDelete() }}
          onCancel={() => setConfirming(false)}
        />
      )}
    </div>
  )
}
