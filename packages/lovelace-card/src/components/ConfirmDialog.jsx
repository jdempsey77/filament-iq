export function ConfirmDialog({ message, confirmLabel = 'Delete', onConfirm, onCancel }) {
  return (
    <div class="fiq-confirm-overlay" onClick={onCancel}>
      <div class="fiq-confirm-box" onClick={e => e.stopPropagation()}>
        <div class="fiq-confirm-msg">{message}</div>
        <div class="fiq-confirm-actions">
          <button class="fiq-btn-cancel" onClick={onCancel}>Cancel</button>
          <button class="fiq-btn-del" onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  )
}
