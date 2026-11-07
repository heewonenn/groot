import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js'
import { writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

const results = {}
async function verify(name, transport, run) {
  const client = new Client({ name: 'hackathon-smoke', version: '1.0.0' })
  try {
    await client.connect(transport)
    const { tools } = await client.listTools()
    results[name] = { connected: true, toolCount: tools.length }
    await run(client, tools)
    results[name].functionalTest = 'passed'
  } catch (error) {
    results[name] = { ...results[name], functionalTest: 'failed', error: error.message }
    process.exitCode = 1
  } finally {
    await client.close()
  }
}

await verify('openaiDeveloperDocs', new StreamableHTTPClientTransport(new URL('https://developers.openai.com/mcp')), async (client, tools) => {
  const tool = tools.find(t => /search/i.test(t.name))
  if (!tool) throw new Error('No documentation search tool')
  const result = await client.callTool({ name: tool.name, arguments: { query: 'Codex skills', limit: 1 } })
  if (result.isError || !result.content?.length) throw new Error('Documentation search failed')
  results.openaiDeveloperDocs.test = `Called ${tool.name}; received content`
})

await verify('playwright', new StdioClientTransport({
  command: process.execPath,
  args: [fileURLToPath(new URL('./playwright-mcp.mjs', import.meta.url))],
  stderr: 'pipe',
}), async (client) => {
  const result = await client.callTool({ name: 'browser_navigate', arguments: { url: 'about:blank' } })
  if (result.isError) throw new Error(JSON.stringify(result.content))
  const snapshot = await client.callTool({ name: 'browser_snapshot', arguments: {} })
  if (snapshot.isError) throw new Error('Browser snapshot failed')
  await client.callTool({ name: 'browser_close', arguments: {} })
  results.playwright.test = 'Opened isolated Chromium, navigated about:blank, captured snapshot, closed browser'
})

await writeFile(new URL('../../docs/mcp-audit.json', import.meta.url), JSON.stringify(results, null, 2))
console.log(JSON.stringify(results, null, 2))
