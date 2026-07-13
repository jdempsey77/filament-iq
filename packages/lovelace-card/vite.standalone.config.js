import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'

// Third Vite entry from the same src/ (precedent: vite.config.js +
// vite.printer.config.js already build two IEEE-lib bundles from one src/
// tree for HA's Lovelace custom elements). This one is a real standalone
// app -- its own index.html, not a `lib` custom-element build -- served at
// /filament-iq-ops/ behind Authelia, same origin as the BFF so there's no
// CORS. `base` is the one place that mount path is a literal string; the
// app itself only ever reads it back via import.meta.env.BASE_URL.
export default defineConfig({
  root: 'standalone',
  base: '/filament-iq-ops/',
  plugins: [preact()],
  define: {
    __BUILD_VERSION__: JSON.stringify(Date.now().toString()),
  },
  build: {
    outDir: '../dist-standalone',
    emptyOutDir: true,
  },
})
