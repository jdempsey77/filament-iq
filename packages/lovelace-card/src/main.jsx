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

const HT_DRYING_ENTITIES = [
  'binary_sensor.p1s_01p00c5a3101668_ams_128_drying',
  'binary_sensor.p1s_01p00c5a3101668_ams_129_drying',
  'binary_sensor.p1s_01p00c5a3101668_ams_130_drying',
]

class FilamentIQManagerElement extends HTMLElement {
  constructor() {
    super()
    this._hass = null
    this._config = {}
    this._rendered = false
    this._navIntent = null
    this._dryingUnsub = null
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
      const rawIntent = hass?.states?.['input_text.filament_iq_nav_intent']?.state || ''
      this._navIntent = rawIntent && rawIntent !== '—' ? rawIntent : null
      render(h(FilamentIQCard, { hass, getHass: () => self._hass, navIntent: self._navIntent, config: self._config }), this)
      this._subscribeDrying(hass)
    }
  }

  _subscribeDrying(hass) {
    if (!hass?.connection) return
    const self = this
    hass.connection.subscribeEvents((event) => {
      if (HT_DRYING_ENTITIES.includes(event.data?.entity_id) && self._rendered) {
        render(h(FilamentIQCard, { hass: self._hass, getHass: () => self._hass, navIntent: self._navIntent, config: self._config }), self)
      }
    }, 'state_changed').then(unsub => {
      self._dryingUnsub = unsub
    }).catch(() => {})
  }

  disconnectedCallback() {
    if (this._dryingUnsub) {
      try { this._dryingUnsub() } catch (_) {}
      this._dryingUnsub = null
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
