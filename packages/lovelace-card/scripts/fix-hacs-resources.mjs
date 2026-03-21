import { execFileSync } from 'child_process'
import { readFileSync, writeFileSync } from 'fs'

const SSH_KEY = `${process.env.HOME}/.ssh/id_ed25519_ha`
const SSH_HOST = 'root@192.168.4.124'
const SSH_PORT = '2222'
const TMP = '/tmp/lr_fix.json'

const sshArgs = ['-p', SSH_PORT, '-i', SSH_KEY, SSH_HOST]

// Download current resources file
execFileSync('scp', ['-P', SSH_PORT, '-i', SSH_KEY, `${SSH_HOST}:/config/.storage/lovelace_resources`, TMP])
const data = JSON.parse(readFileSync(TMP, 'utf8'))

// Get all JS files in community folder
const communityFiles = execFileSync('ssh', [...sshArgs,
  "find /config/www/community -maxdepth 2 -name '*.js' | grep -v chunk | grep -v node_modules | grep -v rollup | grep -v webpack | sort"
], { encoding: 'utf8' }).trim().split('\n').filter(Boolean)

console.log('Community JS files found:', communityFiles.length)
console.log('')

let fixed = 0, ok = 0, removed = 0

data.data.items = data.data.items.filter(item => {
  if (!item.url.includes('/hacsfiles/')) return true

  const urlPath = item.url.split('?')[0]
  const filename = urlPath.split('/').pop()
  const match = communityFiles.find(f => f.endsWith('/' + filename))

  if (match) {
    const correctUrl = match.replace('/config/www/community/', '/hacsfiles/')
    if (correctUrl !== urlPath) {
      console.log(`FIXED: ${item.url} -> ${correctUrl}`)
      item.url = correctUrl
      fixed++
    } else {
      ok++
    }
    return true
  } else {
    console.log(`REMOVED (not on disk): ${item.url}`)
    removed++
    return false
  }
})

console.log('')
console.log(`OK: ${ok}, Fixed: ${fixed}, Removed: ${removed}`)
console.log(`Total resources: ${data.data.items.length}`)

// Write back via SCP (not pipe, to avoid empty file bug)
writeFileSync(TMP, JSON.stringify(data))
execFileSync('scp', ['-P', SSH_PORT, '-i', SSH_KEY, TMP, `${SSH_HOST}:/config/.storage/lovelace_resources`])
console.log('Written to HA successfully.')
