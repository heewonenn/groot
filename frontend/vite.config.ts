import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { host: '127.0.0.1', proxy: { '/api': `http://127.0.0.1:${Number(process.env.CQA_E2E_API_PORT ?? 8765)}` } },
})
