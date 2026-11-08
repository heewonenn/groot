import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'node:path'

const server = process.env.CQA_E2E_MODEL === '1' ? 'serve_model_e2e' : 'serve_e2e'

function testPort(name: string, fallback: number): number {
  const value = Number(process.env[name] ?? fallback)
  if (!Number.isInteger(value) || value < 1 || value > 65535) {
    throw new Error(`${name} must be an integer between 1 and 65535`)
  }
  return value
}

const apiPort = testPort('CQA_E2E_API_PORT', 8765)
const uiPort = testPort('CQA_E2E_UI_PORT', 5173)
if (apiPort === uiPort) throw new Error('E2E API and UI ports must be different')

export default defineConfig({
  testDir: './tests',
  use: { baseURL: `http://127.0.0.1:${uiPort}`, trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    { command: `.\\.venv\\Scripts\\python.exe -m backend.tests.${server}`, cwd: resolve('..'), url: `http://127.0.0.1:${apiPort}/api/health`, env: { CQA_E2E_API_PORT: String(apiPort) }, reuseExistingServer: false },
    { command: `npm run dev -- --host 127.0.0.1 --port ${uiPort} --strictPort`, url: `http://127.0.0.1:${uiPort}`, env: { CQA_E2E_API_PORT: String(apiPort) }, reuseExistingServer: false },
  ],
})
