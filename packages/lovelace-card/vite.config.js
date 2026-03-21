import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'

export default defineConfig({
  plugins: [preact()],
  define: {
    __BUILD_VERSION__: JSON.stringify(Date.now().toString()),
  },
  build: {
    lib: {
      entry: 'src/main.jsx',
      name: 'FilamentIQManager',
      fileName: () => 'filament-iq-manager.js',
      formats: ['iife'],
    },
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
      },
    },
  },
})
