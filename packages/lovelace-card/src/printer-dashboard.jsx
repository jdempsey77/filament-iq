;(function () {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker
      .getRegistrations()
      .then(regs => regs.forEach(r => r.unregister()))
      .catch(() => {})
  }
})()

import { render, h } from 'preact'
import { PrinterDashboardCard } from './PrinterDashboardCard.jsx'
import cardCSS from './styles/printer-dashboard.css?inline'

class PrinterDashboardElement extends HTMLElement {
  constructor() {
    super()
    this._hass = null
    this._config = {}
    this._mounted = false
  }

  set hass(hass) {
    this._hass = hass
    if (!this._mounted && this.isConnected) {
      this._mount()
    }
  }

  connectedCallback() {
    if (!this._mounted && this._hass) {
      this._mount()
    }
  }

  _mount() {
    this._mounted = true
    this._injectStyles()
    const self = this
    render(
      h(PrinterDashboardCard, {
        config: this._config,
        getHass: () => self._hass,
      }),
      this
    )
  }

  get hass() {
    return this._hass
  }

  setConfig(config) {
    this._config = config || {}
  }

  _injectStyles() {
    const style = document.createElement('style')
    style.textContent = cardCSS
    this.appendChild(style)
  }

  getCardSize() {
    return 10
  }

  static getConfigElement() {
    return document.createElement('div')
  }

  static getStubConfig() {
    return {}
  }
}

if (!customElements.get('printer-dashboard')) {
  customElements.define('printer-dashboard', PrinterDashboardElement)
}

window.customCards = window.customCards || []
if (!window.customCards.find(c => c.type === 'printer-dashboard')) {
  window.customCards.push({
    type: 'printer-dashboard',
    name: 'Printer Dashboard',
    description: 'Combined Printer · Slots · Filament IQ view',
    preview: false,
    documentationURL: 'https://github.com/jdempsey77/filament-iq',
  })
}
