#!/usr/bin/env node
/**
 * Filament IQ — Dashboard Setup Script
 *
 * Generates a configured Lovelace dashboard YAML for any Bambu Lab printer.
 * Reads templates from dashboards/templates/ and assembles based on user's
 * AMS configuration.
 */

import { readFileSync, writeFileSync, existsSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'
import { createInterface } from 'readline'

const __dirname = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(__dirname, '..')
const TEMPLATE_DIR = resolve(REPO_ROOT, 'dashboards/templates')
const OUTPUT = resolve(REPO_ROOT, 'filament-iq-dashboard.yaml')

// ── Readline helper ─────────────────────────────────────────
function createRL() {
  return createInterface({ input: process.stdin, output: process.stdout })
}

function ask(rl, question) {
  return new Promise(resolve => rl.question(question, resolve))
}

// ── Template loading ────────────────────────────────────────
function loadTemplate(name) {
  const path = resolve(TEMPLATE_DIR, name)
  if (!existsSync(path)) {
    console.error(`Template not found: ${path}`)
    process.exit(1)
  }
  return readFileSync(path, 'utf8')
}

// ── Substitution ────────────────────────────────────────────
function sub(text, vars) {
  let result = text
  for (const [key, val] of Object.entries(vars)) {
    result = result.replaceAll(key, val)
  }
  return result
}

// ── Test mode config ────────────────────────────────────────
function getTestConfig() {
  return {
    serial: process.env.TEST_SERIAL || '01P00A000000000',
    amsUnits: JSON.parse(process.env.TEST_AMS || JSON.stringify([
      { name: 'AMS 2 Pro', type: 'pro', amsIndex: 0 },
      { name: 'AMS HT 1', type: 'ht', amsIndex: 128 },
      { name: 'AMS HT 2', type: 'ht', amsIndex: 129 },
    ])),
  }
}

// ── Main ────────────────────────────────────────────────────
async function main() {
  const isTest = process.argv.includes('--test')

  let serialUpper, serialLower, amsUnits = [], slotNum = 1

  if (isTest) {
    const cfg = getTestConfig()
    serialUpper = cfg.serial.toUpperCase()
    serialLower = cfg.serial.toLowerCase()
    for (const u of cfg.amsUnits) {
      const slotsInUnit = u.type === 'pro' ? 4 : 1
      const slots = []
      for (let s = 0; s < slotsInUnit; s++) {
        slots.push({ slotNum, trayIndex: s, amsIndex: u.amsIndex })
        slotNum++
      }
      amsUnits.push({ ...u, slots })
    }
    console.log(`  Test mode: serial=${serialUpper}, ${amsUnits.length} AMS units, ${slotNum - 1} slots`)
  } else {
    console.log('')
    console.log('  Filament IQ — Dashboard Setup')
    console.log('  ──────────────────────────────────────')
    console.log('')

    const rl = createRL()

    // 1. Printer serial
    console.log('  Find your printer serial in:')
    console.log('  HA → Settings → Integrations → Bambu Lab → [your printer]')
    console.log('')
    const serial = await ask(rl, '  Printer serial (e.g. 01P00A000000000): ')
    if (!serial.trim()) { console.error('  Error: serial cannot be empty'); process.exit(1) }
    serialUpper = serial.trim().toUpperCase()
    serialLower = serial.trim().toLowerCase()

    // 2. AMS configuration
    console.log('')
    console.log('  AMS Configuration')
    console.log('  ─────────────────')
    console.log('  Bambu AMS types:')
    console.log('    Pro/Lite = 4 filament slots')
    console.log('    HT       = 1 filament slot')
    console.log('')
    const amsCountStr = await ask(rl, '  How many AMS units do you have? (1-4): ')
    const amsCount = parseInt(amsCountStr)
    if (isNaN(amsCount) || amsCount < 1 || amsCount > 4) {
      console.error('  Error: must be 1-4'); process.exit(1)
    }

    console.log('')
    console.log('  For each AMS, you need the AMS index from HA.')
    console.log('  Find it in: Developer Tools → States → search "ams_"')
    console.log('  Look at sensor names like sensor.p1s_XXXXX_ams_INDEX_humidity')
    console.log('  Common indices: 0 (first Pro/Lite), 128/129/130/131 (HT units)')
    console.log('')

    for (let i = 0; i < amsCount; i++) {
      console.log(`  AMS Unit ${i + 1}:`)
      const typeStr = await ask(rl, '    Type — [P]ro/Lite (4-slot) or [H]T (1-slot): ')
      const type = typeStr.trim().toUpperCase().startsWith('H') ? 'ht' : 'pro'
      const slotsInUnit = type === 'pro' ? 4 : 1

      const indexStr = await ask(rl, '    AMS index (from HA sensor names): ')
      const amsIndex = parseInt(indexStr.trim())
      if (isNaN(amsIndex)) { console.error('  Error: index must be a number'); process.exit(1) }

      const nameStr = await ask(rl, `    Display name (e.g. "AMS Pro" or "AMS HT ${i + 1}"): `)
      const name = nameStr.trim() || (type === 'pro' ? `AMS Pro ${i + 1}` : `AMS HT ${i + 1}`)

      const slots = []
      for (let s = 0; s < slotsInUnit; s++) {
        slots.push({ slotNum, trayIndex: s, amsIndex })
        slotNum++
      }

      amsUnits.push({ name, type, amsIndex, slots })
      console.log(`    → ${name}: ${slotsInUnit} slot(s), slots ${slots.map(s => s.slotNum).join(',')}`)
      console.log('')
    }

    rl.close()
  }

  const totalSlots = slotNum - 1
  console.log(`  Total slots: ${totalSlots}`)
  console.log('')

  // 3. Load templates and assemble
  const header = loadTemplate('header.yaml')
  const printerTop = loadTemplate('printer-top.yaml')
  const cameraButtons = loadTemplate('camera-buttons.yaml')
  const amsHeader = loadTemplate('ams-header.yaml')
  const slotCard = loadTemplate('slot-card.yaml')
  const dryingCard = loadTemplate('drying-card.yaml')
  const filamentIqView = loadTemplate('filament-iq-view.yaml')

  const globalVars = {
    'YOUR_PRINTER_SERIAL': serialUpper,
    'your_printer_serial': serialLower,
  }

  let output = sub(header, globalVars) + '\n'
  output += 'views:\n'

  // 3D Printer view
  output += sub(printerTop, globalVars)

  // Camera + buttons section
  output += sub(cameraButtons, globalVars)

  // AMS sections
  for (const unit of amsUnits) {
    const amsSensorPrefix = `ams_${unit.amsIndex}_`
    const amsVars = {
      ...globalVars,
      'AMS_DISPLAY_NAME': unit.name,
      'AMS_SENSOR_PREFIX': amsSensorPrefix,
    }

    // AMS header
    output += sub(amsHeader, amsVars)

    // Slot cards
    for (const slot of unit.slots) {
      const iconNum = unit.type === 'pro' ? slot.trayIndex + 1 : ''
      const iconFilled = unit.type === 'pro'
        ? `mdi:numeric-${iconNum}-circle`
        : 'mdi:circle'
      const iconOutline = unit.type === 'pro'
        ? `mdi:numeric-${iconNum}-circle-outline`
        : 'mdi:circle-outline'

      const slotVars = {
        ...amsVars,
        'SLOT_NUM': String(slot.slotNum),
        'TRAY_INDEX': String(slot.trayIndex),
        'AMS_INDEX_NUM': String(slot.amsIndex),
        'ICON_FILLED': iconFilled,
        'ICON_OUTLINE': iconOutline,
      }
      output += sub(slotCard, slotVars)
    }

    // Drying card
    output += sub(dryingCard, amsVars)
    output += '\n'
  }

  // Close the 3D Printer view
  output += '    cards: []\n'

  // Filament IQ view
  output += sub(filamentIqView, globalVars)

  // 4. Write output
  writeFileSync(OUTPUT, output)

  console.log(`  Created: filament-iq-dashboard.yaml`)
  console.log('')
  console.log('  Next steps:')
  console.log('  1. Copy filament-iq-dashboard.yaml to your HA /config/ directory')
  console.log('  2. Add to configuration.yaml:')
  console.log('')
  console.log('       lovelace:')
  console.log('         dashboards:')
  console.log('           filament-iq:')
  console.log('             mode: yaml')
  console.log('             title: Filament IQ')
  console.log('             icon: mdi:brain')
  console.log('             filename: filament-iq-dashboard.yaml')
  console.log('')
  console.log('  3. Restart Home Assistant')
  console.log('  4. See README.md for prerequisites')
  console.log('')
}

main().catch(e => { console.error(e); process.exit(1) })
