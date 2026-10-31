#!/usr/bin/env node
// Local production replica for the browser suite: serves apps/web/dist with
// the exact headers and rewrites parsed from vercel.json (never duplicated
// here) and routes /api/* — plus any rewrite whose destination is an /api
// path, such as /mcp — to the real handlers through server/dev.mjs. Unknown
// paths return 404 with no SPA fallback. Binds 127.0.0.1 only.

import { createServer } from 'node:http';
import { createReadStream } from 'node:fs';
import { readFile, stat } from 'node:fs/promises';
import { join, normalize, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createPublicServer } from '../../../server/dev.mjs';

const dist = fileURLToPath(new URL('../dist', import.meta.url));
const vercel = JSON.parse(await readFile(new URL('../../../vercel.json', import.meta.url), 'utf8'));

const sourcePattern = source => new RegExp(`^${source}$`);
const headerRules = (vercel.headers ?? []).map(rule => ({
  pattern: sourcePattern(rule.source), headers: rule.headers,
}));
const rewrites = (vercel.rewrites ?? []).map(rule => ({
  pattern: sourcePattern(rule.source), destination: rule.destination,
}));

const contentTypes = new Map(Object.entries({
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.geojson': 'application/geo+json',
  '.csv': 'text/csv; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.webmanifest': 'application/manifest+json',
  '.parquet': 'application/octet-stream',
}));

const extensionOf = path => {
  const index = path.lastIndexOf('.');
  return index === -1 ? '' : path.slice(index);
};

const applyHeaders = (path, response) => {
  for (const { pattern, headers } of headerRules) {
    if (!pattern.test(path)) continue;
    for (const { key, value } of headers) response.setHeader(key, value);
  }
};

const api = createPublicServer();

const listener = async (request, response) => {
  const path = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
  applyHeaders(path, response);
  // Rewrites whose destination is an /api path expose API aliases like /mcp.
  // createPublicServer already maps the public paths (e.g. /mcp itself and
  // /api/v1/openapi.json), so the original URL is what the handler sees.
  const rewritten = rewrites.find(rule => rule.pattern.test(path));
  if (path.startsWith('/api/') || (rewritten && rewritten.destination.startsWith('/api/'))) {
    api.emit('request', request, response);
    return;
  }
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    response.writeHead(405).end();
    return;
  }
  const resolved = normalize(join(dist, path));
  if (!resolved.startsWith(dist + sep)) {
    response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' }).end('404');
    return;
  }
  let file = resolved;
  try {
    if ((await stat(file)).isDirectory()) file = join(file, 'index.html');
    await stat(file);
  } catch {
    response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' }).end('404');
    return;
  }
  response.writeHead(200, { 'Content-Type': contentTypes.get(extensionOf(file)) ?? 'application/octet-stream' });
  if (request.method === 'HEAD') { response.end(); return; }
  createReadStream(file).pipe(response);
};

const server = createServer((request, response) => {
  listener(request, response).catch(() => {
    if (!response.headersSent) response.writeHead(500).end();
    else response.end();
  });
});

const port = Number(process.env.PORT ?? 0);
server.listen(port, '127.0.0.1', () => {
  const address = server.address();
  console.log(`Preview replica: http://127.0.0.1:${typeof address === 'object' && address ? address.port : port}`);
});
