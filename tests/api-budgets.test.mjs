import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { createPublicServer } from '../server/dev.mjs';

const KiB = 1024;
let server, base;

before(async () => {
  server = createPublicServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});
after(async () => {
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
});

async function bytes(path) {
  const response = await fetch(base + path);
  assert.equal(response.status, 200, path);
  const body = await response.arrayBuffer();
  return body.byteLength;
}

test('small news queries stay inside the byte budgets', async () => {
  const budgets = [
    ['/api/v1/news?limit=1&coverage=summary', 32 * KiB],
    ['/api/v1/news?coverage=summary', 384 * KiB],
    ['/api/v1/news?limit=100&coverage=summary', 1024 * KiB],
    ['/api/v1/news/coverage', 1024 * KiB],
  ];
  for (const [path, budget] of budgets) {
    const size = await bytes(path);
    console.log(`${path}: ${size} bytes (budget ${budget})`);
    assert.ok(size <= budget, `${path} is ${size} bytes, over ${budget}`);
  }
});

test('the MCP search_news JSON-RPC response stays inside the byte budget', async () => {
  const client = new Client({ name: 'api-budgets', version: '1.0.0' });
  await client.connect(new StreamableHTTPClientTransport(new URL(base + '/mcp')));
  try {
    const result = await client.callTool({ name: 'search_news', arguments: { limit: 1 } });
    // The text block duplicates structuredContent, which doubles the payload.
    const size = Buffer.byteLength(JSON.stringify(result));
    console.log(`MCP search_news {limit:1}: ${size} bytes (budget ${64 * KiB})`);
    assert.ok(size <= 64 * KiB, `response is ${size} bytes, over ${64 * KiB}`);
  } finally { await client.close(); }
});

test('news search latency stays inside the cold and warm budgets', () => {
  const child = spawnSync(process.execPath,
    ['--import', './tests/setup-instance.mjs', 'tests/api-budgets-child.mjs'],
    { encoding: 'utf8', timeout: 60000 });
  assert.equal(child.status, 0, child.stderr);
  const measured = JSON.parse(child.stdout.trim().split('\n').pop());
  console.log(`cold (import + first summary query): ${measured.cold.toFixed(1)} ms (budget 3000)`);
  console.log(`warm p95 over 40 mixed queries: ${measured.warm_p95.toFixed(2)} ms (budget 150)`);
  assert.ok(measured.cold <= 3000, `cold ${measured.cold} ms over 3000`);
  assert.ok(measured.warm_p95 <= 150, `warm p95 ${measured.warm_p95} ms over 150`);
});
