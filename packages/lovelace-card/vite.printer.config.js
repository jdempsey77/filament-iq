import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'

export default defineConfig({
  plugins: [preact()],
  define: {
    __BUILD_VERSION__: JSON.stringify(Date.now().toString()),
  },
  build: {
    emptyOutDir: false,
    lib: {
      entry: 'src/printer-dashboard.jsx',
      name: 'PrinterDashboard',
      fileName: () => 'printer-dashboard.js',
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
