// Unregister HA service worker to ensure fresh JS loads after deploys
;(function() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.getRegistrations()
      .then(regs => regs.forEach(r => r.unregister()))
      .catch(() => {})
  }
})()

import { render, h } from 'preact'
import { FilamentIQCard } from './FilamentIQCard'
import { HassProvider } from './provider/HassProvider'
import cardCSS from './styles/card.css?inline'

// Printer serial comes from card config (`printer_serial`). This default is
// only a fallback when config omits it (e.g. card embedded with empty config).
const DEFAULT_SERIAL = '01p00c5b2201397'

class FilamentIQManagerElement extends HTMLElement {
  constructor() {
    super()
    this._hass = null
    this._config = {}
    this._rendered = false
    this._navIntent = null
    this._provider = new HassProvider(() => this._hass, () => this._serial())
  }

  setConfig(config) {
    this._config = config || {}
  }

  _serial() {
    return String(this._config?.printer_serial || DEFAULT_SERIAL).toLowerCase()
  }

  set hass(hass) {
    this._hass = hass
    if (!this._rendered && hass) {
      this._rendered = true
      this._injectStyles()
      const self = this
      const rawIntent = hass?.states?.['input_text.filament_iq_nav_intent']?.state || ''
      this._navIntent = rawIntent && rawIntent !== '—' ? rawIntent : null
      render(h(FilamentIQCard, { provider: self._provider, navIntent: self._navIntent, onNavIntentConsumed: () => self._clearNavIntent(), config: self._config }), this)
    }
  }

  // Nav-intent read (above) and clear (here) are a matched pair, both
  // outside HassProvider — this is UI plumbing for a single hardcoded
  // input_text, not a BFF-worthy operation.
  _clearNavIntent() {
    this._hass?.callService('input_text', 'set_value', {
      entity_id: 'input_text.filament_iq_nav_intent',
      value: '',
    })
  }

  get hass() {
    return this._hass
  }

  _injectStyles() {
    const style = document.createElement('style')
    style.textContent = cardCSS
    this.appendChild(style)
  }

  getCardSize() { return 8 }
  static getConfigElement() { return document.createElement('div') }
  static getStubConfig() { return {} }
}

if (!customElements.get('filament-iq-manager')) {
  customElements.define('filament-iq-manager', FilamentIQManagerElement)
}

window.customCards = window.customCards || []
if (!window.customCards.find(c => c.type === 'filament-iq-manager')) {
  window.customCards.push({
    type: 'filament-iq-manager',
    name: 'Filament IQ Manager',
    description: 'Spoolman CRUD management',
  })
}
