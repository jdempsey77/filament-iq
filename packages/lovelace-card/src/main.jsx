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
import cardCSS from './styles/card.css?inline'

class FilamentIQManagerElement extends HTMLElement {
  constructor() {
    super()
    this._hass = null
    this._config = {}
    this._rendered = false
  }

  setConfig(config) {
    this._config = config || {}
  }

  set hass(hass) {
    this._hass = hass
    if (!this._rendered && hass) {
      this._rendered = true
      this._injectStyles()
      const self = this
      render(h(FilamentIQCard, { hass, getHass: () => self._hass }), this)
    }
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
