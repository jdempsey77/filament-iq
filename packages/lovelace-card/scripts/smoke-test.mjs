import { config } from 'dotenv'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
config({ path: resolve(__dirname, '../.env') })

const { HA_TOKEN, HA_HOST = '192.168.4.124', HA_PORT = '8123' } = process.env

async function test(name, fn) {
  try {
    const result = await fn()
    console.log(`  PASS  ${name}: ${result}`)
    return true
  } catch (e) {
    console.log(`  FAIL  ${name}: ${e.message}`)
    return false
  }
}

console.log('Filament IQ Manager — Smoke Test')
console.log('=================================')
console.log('')

let allPassed = true

// Test 1: HA is reachable
allPassed = await test('HA reachable', async () => {
  const r = await fetch(`http://${HA_HOST}:${HA_PORT}/manifest.json`)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return 'OK'
}) && allPassed

// Test 2: Spoolman is reachable
allPassed = await test('Spoolman reachable', async () => {
  const r = await fetch(`http://${HA_HOST}:7912/api/v1/spool?limit=1`)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  const data = await r.json()
  return `${Array.isArray(data) ? data.length : '?'} spool(s) in response`
}) && allPassed

// Test 3: filament_iq_proxy service registered in HA
if (HA_TOKEN) {
  allPassed = await test('filament_iq_proxy.api_call service registered', async () => {
    const r = await fetch(`http://${HA_HOST}:${HA_PORT}/api/services`, {
      headers: { 'Authorization': `Bearer ${HA_TOKEN}` },
    })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const services = await r.json()
    const domain = services.find(s => s.domain === 'filament_iq_proxy')
    if (!domain) throw new Error('filament_iq_proxy domain not found in services')
    if (!domain.services?.api_call) throw new Error('api_call service not found')
    return 'registered'
  }) && allPassed

  // Test 4: End-to-end proxy call
  allPassed = await test('proxy can reach Spoolman (e2e)', async () => {
    const r = await fetch(`http://${HA_HOST}:${HA_PORT}/api/services/filament_iq_proxy/api_call`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${HA_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        request_id: 'smoke-test-001',
        method: 'GET',
        path: '/api/v1/health',
      }),
    })
    if (r.status >= 400) throw new Error(`Service call returned ${r.status}`)
    return 'OK — service call accepted'
  }) && allPassed
} else {
  console.log('  SKIP  filament_iq_proxy service check — no HA_TOKEN in .env')
  console.log('  SKIP  proxy e2e check — no HA_TOKEN in .env')
}

console.log('')
if (allPassed) {
  console.log('All checks passed.')
} else {
  console.log('Some checks failed — see above.')
  process.exit(1)
}
