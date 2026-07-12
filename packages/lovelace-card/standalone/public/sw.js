// Caches the static shell only (HTML/JS/CSS/icons) so a cold PWA launch has
// something to paint from even before the network responds. Never touches
// /api/* -- /state, /events (SSE), /rpc/* must always hit the network
// fresh; the data-level stale-while-revalidate cache is a separate,
// localStorage-based layer in application code (src/cache.js), not this
// service worker.
const CACHE_NAME = 'filament-iq-shell-v1'
const SHELL_ASSETS = ['./', './index.html', './manifest.json']

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)).catch(() => {})
  )
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  )
  self.clients.claim()
})

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url)
  if (url.pathname.includes('/api/')) return
  if (event.request.method !== 'GET') return

  event.respondWith(
    caches.match(event.request).then((cached) => {
      const fetchPromise = fetch(event.request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone()
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy))
          }
          return response
        })
        .catch(() => cached)
      return cached || fetchPromise
    })
  )
})
