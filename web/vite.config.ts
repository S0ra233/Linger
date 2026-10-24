import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { '/api': 'http://127.0.0.1:18765', '/healthz': 'http://127.0.0.1:18765' },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
