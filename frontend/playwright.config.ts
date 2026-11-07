import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'node:path'

const server = process.env.CQA_E2E_MODEL === '1' ? 'serve_model_e2e' : 'serve_e2e'

export default defineConfig({
  testDir: './tests',
  use: { baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    { command: `.\\.venv\\Scripts\\python.exe -m backend.tests.${server}`, cwd: resolve('..'), url: 'http://127.0.0.1:8765/api/health', reuseExistingServer: false },
    { command: 'npm run dev -- --host 127.0.0.1 --port 5173 --strictPort', url: 'http://127.0.0.1:5173', reuseExistingServer: false },
  ],
})
