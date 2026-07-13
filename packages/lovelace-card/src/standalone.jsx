import { render, h } from 'preact'
import { FilamentIQCard } from './FilamentIQCard'
import { BffProvider } from './provider/BffProvider'
import cardCSS from './styles/card.css?inline'
import './styles/standalone.css'

// Same-origin relative, never an absolute host/port -- filament-iq is a
// public repo. import.meta.env.BASE_URL is Vite's configured `base` (set
// once, in vite.standalone.config.js) -- the one place this path lives.
const provider = new BffProvider(`${import.meta.env.BASE_URL}api`)

// Icon mark lifted from components/FilamentIQLogo.jsx, on a solid card-bg
// square. Inlined as a data: URI (below) rather than served as a separate
// file: manifest/icon fetches behind an auth gate can't follow an
// interactive login redirect (the actual bug this replaced a nginx bypass
// for) -- inlining means nothing is ever fetched, so there's nothing for
// any gate to intercept.
const ICON_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72" width="512" height="512">
  <rect width="72" height="72" rx="14" fill="#111113"/>
  <circle cx="36" cy="36" r="32" fill="none" stroke="#5B8AF0" stroke-width="3.5"/>
  <path d="M36 6 A30 30 0 1 1 35.99 6Z" fill="none" stroke="#F97316" stroke-width="11" opacity="0.9"/>
  <circle cx="36" cy="36" r="11" fill="#111113" stroke="#5B8AF0" stroke-width="2.5"/>
  <line x1="36" y1="4" x2="36" y2="25" stroke="#5B8AF0" stroke-width="2.5" stroke-linecap="round"/>
  <line x1="63.7" y1="52" x2="47.6" y2="42.5" stroke="#5B8AF0" stroke-width="2.5" stroke-linecap="round"/>
  <line x1="8.3" y1="52" x2="24.4" y2="42.5" stroke="#5B8AF0" stroke-width="2.5" stroke-linecap="round"/>
  <circle cx="36" cy="36" r="4.5" fill="#5B8AF0"/>
  <path d="M65 19 Q71 11 69 5" fill="none" stroke="#F97316" stroke-width="2.5" stroke-linecap="round" opacity="0.75"/>
  <circle cx="69" cy="5" r="3" fill="#F97316" opacity="0.75"/>
</svg>`
const iconDataUri = `data:image/svg+xml,${encodeURIComponent(ICON_SVG)}`

const manifest = {
  name: 'Filament IQ',
  short_name: 'Filament IQ',
  description: 'Spoolman CRUD + AMS slot management',
  start_url: `${import.meta.env.BASE_URL}`,
  scope: `${import.meta.env.BASE_URL}`,
  display: 'standalone',
  orientation: 'portrait',
  background_color: '#111113',
  theme_color: '#111113',
  icons: [{ src: iconDataUri, sizes: 'any', type: 'image/svg+xml' }],
}
// btoa is fine unescaped here -- manifest content above is pure ASCII.
const manifestDataUri = `data:application/manifest+json;base64,${btoa(JSON.stringify(manifest))}`

function addLink(rel, href, extra = {}) {
  const link = document.createElement('link')
  link.rel = rel
  link.href = href
  Object.entries(extra).forEach(([k, v]) => link.setAttribute(k, v))
  document.head.appendChild(link)
}
addLink('manifest', manifestDataUri)
addLink('icon', iconDataUri, { type: 'image/svg+xml' })
addLink('apple-touch-icon', iconDataUri)

const style = document.createElement('style')
style.textContent = cardCSS
document.head.appendChild(style)

render(
  h(FilamentIQCard, { provider, navIntent: null, onNavIntentConsumed: () => {}, config: {} }),
  document.getElementById('app'),
)
