import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'
export default defineConfig({
 root: fileURLToPath(new URL('.', import.meta.url)), plugins: [vue()],
 build: { outDir: '../dist', emptyOutDir: true, manifest: true },
 server: { host: '127.0.0.1', port: 5173, strictPort: true,
  fs: { strict: true, allow: [fileURLToPath(new URL('.', import.meta.url)), fileURLToPath(new URL('../node_modules', import.meta.url))] },
  proxy: { '/api': { target: 'http://127.0.0.1:8016', changeOrigin: false } } },
 test: { environment: 'node', include: ['src/tests/**/*.test.ts'] }
})
