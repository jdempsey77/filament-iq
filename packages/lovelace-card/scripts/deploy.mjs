import { execSync, execFileSync } from 'child_process'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'
import { config } from 'dotenv'

const __dirname = dirname(fileURLToPath(import.meta.url))
config({ path: resolve(__dirname, '../.env') })

const SSH_HOST = '192.168.4.124'
const SSH_PORT = '2222'
const SSH_KEY = `${process.env.HOME}/.ssh/id_ed25519_ha`
const HA_TOKEN = process.env.HA_TOKEN
const HA_HOST = process.env.HA_HOST || '192.168.4.124'
const HA_PORT = process.env.HA_PORT || '8123'

const LOCAL_JS = resolve(__dirname, '../dist/filament-iq-manager.js')
const LOCAL_COMPONENT = resolve(__dirname, '../../../custom_components/filament_iq_proxy/')
const REMOTE_JS = '/config/www/filament-iq-manager.js'
const REMOTE_COMPONENT = '/config/custom_components/filament_iq_proxy/'
const REMOTE_RESOURCES = '/config/.storage/lovelace_resources'

const version = Date.now()

const scp = (local, remote) =>
  execFileSync('scp', ['-P', SSH_PORT, '-i', SSH_KEY, local, `root@${SSH_HOST}:${remote}`], {
    stdio: 'inherit',
  })

const scpDir = (local, remote) => {
  execFileSync('ssh', ['-p', SSH_PORT, '-i', SSH_KEY, `root@${SSH_HOST}`, `mkdir -p ${remote}`], {
    stdio: 'pipe',
  })
  execFileSync('scp', ['-r', '-P', SSH_PORT, '-i', SSH_KEY, local, `root@${SSH_HOST}:${dirname(remote)}/`], {
    stdio: 'inherit',
  })
}

const ssh = (cmd) =>
  execFileSync('ssh', ['-p', SSH_PORT, '-i', SSH_KEY, `root@${SSH_HOST}`, cmd], {
    stdio: 'inherit',
  })

function step(num, label, fn) {
  try {
    console.log(`${num}. ${label}...`)
    fn()
    return true
  } catch (e) {
    console.error(`   FAILED: ${label}`)
    return false
  }
}

let ok = true

// 1. SCP the built JS
ok = step(1, 'Deploying filament-iq-manager.js', () => scp(LOCAL_JS, REMOTE_JS)) && ok

// 2. Update lovelace_resources with version suffix (busts 31-day HTTP cache)
ok = step(2, `Updating lovelace_resources ?v=${version}`, () =>
  ssh(`sed -i 's|/local/filament-iq-manager.js[^"]*|/local/filament-iq-manager.js?v=${version}|g' ${REMOTE_RESOURCES}`)
) && ok

// 3. Deploy custom component
ok = step(3, 'Deploying filament_iq_proxy custom component', () =>
  scpDir(LOCAL_COMPONENT, REMOTE_COMPONENT)
) && ok

// 4. Deploy configuration.yaml
ok = step(4, 'Deploying configuration.yaml', () =>
  scp(resolve(__dirname, '../../../configuration.yaml'), '/config/configuration.yaml')
) && ok

// 5. Bust browser cache via browser_mod.javascript
if (ok && HA_TOKEN) {
  ok = step(5, 'Clearing browser cache and reloading via browser_mod', () => {
    execSync(
      `curl -sf -X POST -H "Authorization: Bearer ${HA_TOKEN}" -H "Content-Type: application/json" -d '${JSON.stringify({
        code: "navigator.serviceWorker.getRegistrations().then(r=>Promise.all(r.map(s=>s.unregister()))).then(()=>location.reload())"
      })}' http://${HA_HOST}:${HA_PORT}/api/services/browser_mod/javascript`,
      { stdio: 'pipe' }
    )
  }) && ok
} else if (ok) {
  console.log('5. Skipping browser reload — no HA_TOKEN in .env')
}

console.log('')
if (!ok) {
  console.error('Deploy finished with errors — check above.')
  process.exit(1)
}

console.log('============================================================')
console.log('Deploy complete. Browsers reloaded automatically.')
console.log('============================================================')
console.log(`  JS:      ${REMOTE_JS}`)
console.log(`  Version: ?v=${version}`)
console.log(`  URL:     http://${HA_HOST}:${HA_PORT}/lovelace-stage/filament-manager`)
console.log('============================================================')
