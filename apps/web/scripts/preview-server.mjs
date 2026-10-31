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
import { configureLocalListener } from '../../../server/instance.mjs';

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

// The API handlers capture the trusted origin at module init, so the port
// must be bound before server/dev.mjs is imported (see the listen callback).
let api;

const listener = async (request, response) => {
  const path = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
  applyHeaders(path, response);
  // Rewrites whose destination is an /api path expose API aliases like /mcp.
  // createPublicServer already maps the public paths (e.g. /mcp itself and
  // /api/v1/openapi.json), so the original URL is what the handler sees.
  const rewritten = rewrites.find(rule => rule.pattern.test(path));
  if (path.startsWith('/api/') || (rewritten && rewritten.destination.startsWith('/api/'))) {
    if (!api) { response.writeHead(503).end(); return; }
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

const requestedPort = Number(process.env.PORT ?? 0);
server.listen(requestedPort, '127.0.0.1', async () => {
  const address = server.address();
  const bound = typeof address === 'object' && address ? address.port : requestedPort;
  configureLocalListener(bound);
  const { createPublicServer } = await import('../../../server/dev.mjs');
  api = createPublicServer();
  console.log(`Preview replica: http://127.0.0.1:${bound}`);
});
