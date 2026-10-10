import { createServer } from 'node:http';
import { pathToFileURL } from 'node:url';
import records from '../api/v1/records.mjs';
import record from '../api/v1/records/[id].mjs';
import stats from '../api/v1/stats.mjs';
import news from '../api/v1/news.mjs';
import openapi from '../api/v1/openapi.mjs';
import mcp from '../api/mcp.mjs';
import missing from '../api/[...path].mjs';

export function createPublicServer() {
  return createServer((req, res) => {
    const path = new URL(req.url, 'http://localhost').pathname;
    const handler = {
      '/api/v1/records': records, '/api/v1/stats': stats, '/api/v1/news': news,
      '/api/v1/openapi.json': openapi, '/mcp': mcp, '/api/mcp': mcp,
    }[path] ?? (/^\/api\/v1\/records\/[^/]+$/.test(path) ? record : missing);
    void handler(req, res);
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const port = Number(process.env.PORT ?? 3001);
  createPublicServer().listen(port, '127.0.0.1', () => console.log(`Public API and MCP: http://127.0.0.1:${port}`));
}
