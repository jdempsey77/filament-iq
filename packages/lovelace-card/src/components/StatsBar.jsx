export function StatsBar({ spools }) {
  if (!spools) return null
  const total = spools.length
  const active = spools.filter(s => (s.remaining_weight || 0) > 0 && !s.archived).length
  const low = spools.filter(s => (s.remaining_weight || 0) > 0 && (s.remaining_weight || 0) < 100 && !s.archived).length
  const empty = spools.filter(s => (s.remaining_weight || 0) === 0 || s.archived).length

  return (
    <div class="fiq-stats">
      <div class="fiq-stat">
        <div class="fiq-stat-n">{total}</div>
        <div class="fiq-stat-l">Total</div>
      </div>
      <div class="fiq-stat">
        <div class="fiq-stat-n s-active">{active}</div>
        <div class="fiq-stat-l">Active</div>
      </div>
      <div class="fiq-stat">
        <div class={`fiq-stat-n${low > 0 ? ' s-warn' : ''}`}>{low}</div>
        <div class="fiq-stat-l">Low</div>
      </div>
      <div class="fiq-stat">
        <div class={`fiq-stat-n${empty > 0 ? ' s-empty' : ''}`}>{empty}</div>
        <div class="fiq-stat-l">Empty</div>
      </div>
    </div>
  )
}
