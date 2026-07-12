// Stale-while-revalidate cache, localStorage-backed. Pattern cloned from
// d5-hub's D5_CACHE (lovelace/d5-hub.js): seed a useState initializer from
// the cache so the first paint is instant, then unconditionally revalidate
// in the background and write through on completion. d5-hub's own D5_CACHE
// is a plain in-memory module variable — it survives a custom-element
// remount within the same JS heap but NOT a full process kill. This adds
// the localStorage write-through d5-hub doesn't have, since a backgrounded
// Android PWA getting its process killed is exactly the case this project
// needs to survive.
const PREFIX = 'fiq_cache_'

export function cacheGet(key) {
  try {
    const raw = localStorage.getItem(PREFIX + key)
    if (!raw) return null
    return JSON.parse(raw)
  } catch (_) {
    return null
  }
}

export function cacheSet(key, value) {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value))
  } catch (_) {
    // Best-effort — a full/unavailable localStorage just means no cache,
    // not a functional failure (unconditional revalidation still runs).
  }
}
