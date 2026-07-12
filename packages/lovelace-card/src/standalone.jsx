import { render, h } from 'preact'
import { FilamentIQCard } from './FilamentIQCard'
import { BffProvider } from './provider/BffProvider'
import cardCSS from './styles/card.css?inline'
import './styles/standalone.css'

// Same-origin relative, never an absolute host/port -- filament-iq is a
// public repo. import.meta.env.BASE_URL is Vite's configured `base` (set
// once, in vite.standalone.config.js) -- the one place this path lives.
const provider = new BffProvider(`${import.meta.env.BASE_URL}api`)

const style = document.createElement('style')
style.textContent = cardCSS
document.head.appendChild(style)

render(
  h(FilamentIQCard, { provider, navIntent: null, onNavIntentConsumed: () => {}, config: {} }),
  document.getElementById('app'),
)

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register(`${import.meta.env.BASE_URL}sw.js`).catch(() => {})
  })
}
