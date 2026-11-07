// Resolve the browser revision bundled with MCP independently of @playwright/test.
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { spawn } from 'node:child_process'
import { homedir } from 'node:os'

const require = createRequire(import.meta.url)
const mcpPackage = require.resolve('@playwright/mcp/package.json')
const mcpRequire = createRequire(mcpPackage)
const coreDir = dirname(mcpRequire.resolve('playwright-core/package.json'))
const browsers = JSON.parse(readFileSync(join(coreDir, 'browsers.json'), 'utf8'))
const revision = browsers.browsers.find(browser => browser.name === 'chromium-headless-shell').revision
const cache = process.env.PLAYWRIGHT_BROWSERS_PATH || join(process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local'), 'ms-playwright')
const executable = join(cache, `chromium_headless_shell-${revision}`, 'chrome-headless-shell-win64', 'chrome-headless-shell.exe')
const child = spawn(process.execPath, [join(dirname(mcpPackage), 'cli.js'), '--headless', '--isolated', '--browser', 'chromium', '--executable-path', executable], { stdio: 'inherit', windowsHide: true })
child.on('error', error => { console.error(error.message); process.exitCode = 1 })
child.on('exit', code => { process.exitCode = code ?? 1 })
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill())
