import { useState, useEffect } from 'preact/hooks'

// Mirrors the `@container fiq-shell (min-width: ...)` breakpoint in card.css,
// but as a JS boolean for the handful of components that must choose which
// markup to MOUNT (not just how to style it) -- e.g. swapping Sidebar for
// TabBar, or deciding whether SlotRow's profileLookup effect should run once
// against a grouped mobile list or once against a flat desktop grid. Tracks
// .fiq-card's own box via ResizeObserver, never window, for the same reason
// the CSS uses a container query: an HA Lovelace card can be narrow inside a
// wide browser window, and the standalone shell can be resized independent
// of any of that.
const FIQ_DESKTOP_BREAKPOINT = 860

export function useIsDesktop(ref) {
  const [isDesktop, setIsDesktop] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect?.width ?? 0
      setIsDesktop(width >= FIQ_DESKTOP_BREAKPOINT)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [ref])

  return isDesktop
}
