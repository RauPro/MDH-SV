import { createServer } from 'node:http';
import { pathToFileURL } from 'node:url';
import { configureLocalListener } from './instance.mjs';

const executable = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
const port = Number(process.env.PORT ?? 3001);
if (executable) configureLocalListener(port);
// OpenAPI and MCP capture the trusted origin during module initialization.
const [records, record, stats, officialStatistics, news, newsCoverage, openapi, mcp, missing] = await Promise.all([
  '../api/v1/records.mjs', '../api/v1/records/[id].mjs', '../api/v1/stats.mjs',
  '../api/v1/official-statistics.mjs', '../api/v1/news.mjs', '../api/v1/news/coverage.mjs',
  '../api/v1/openapi.mjs', '../api/mcp.mjs', '../api/[...path].mjs',
].map(async path => (await import(path)).default));

export function createPublicServer() {
  return createServer((req, res) => {
    const path = new URL(req.url, 'http://localhost').pathname;
    const handler = {
      '/api/v1/records': records, '/api/v1/stats': stats, '/api/v1/news': news,
      '/api/v1/news/coverage': newsCoverage,
      '/api/v1/official-statistics': officialStatistics,
      '/api/v1/openapi.json': openapi, '/mcp': mcp, '/api/mcp': mcp,
    }[path] ?? (/^\/api\/v1\/records\/[^/]+$/.test(path) ? record : missing);
    void handler(req, res);
  });
}

if (executable) {
  createPublicServer().listen(port, '127.0.0.1', () => console.log(`Public API and MCP: http://127.0.0.1:${port}`));
}
