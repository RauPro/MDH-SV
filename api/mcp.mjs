import { isIP } from 'node:net';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { createMcpServer } from '../server/mcp-server.mjs';
import { cors } from '../server/http.mjs';

const MAX_BODY = 32768;
const loopback = hostname => ['localhost', '127.0.0.1', '[::1]'].includes(hostname);

// Public data supports any well-formed HTTPS browser origin in production, without cookies.
// Exact Host validation plus loopback-only origins locally prevents DNS rebinding.
export function validMcpRequest(headers, environment = process.env) {
  const host = headers.host;
  if (typeof host !== 'string' || !/^[a-zA-Z0-9.:[\]-]+$/.test(host)) return false;
  const production = environment.VERCEL === '1' || environment.NODE_ENV === 'production';
  const knownHosts = ['mdh-sv.vercel.app', environment.VERCEL_URL, environment.VERCEL_PROJECT_PRODUCTION_URL].filter(Boolean);
  let hostUrl;
  try { hostUrl = new URL(`https://${host}`); } catch { return false; }
  const local = !production && loopback(hostUrl.hostname);
  if (!local && !knownHosts.includes(host)) return false;
  if (!headers.origin) return true; // Native MCP clients do not send Origin.
  if (typeof headers.origin !== 'string') return false;
  try {
    const origin = new URL(headers.origin);
    if (headers.origin !== origin.origin || origin.username || origin.password) return false;
    if (local) return loopback(origin.hostname) && ['http:', 'https:'].includes(origin.protocol);
    return origin.protocol === 'https:' && !loopback(origin.hostname) && !isIP(origin.hostname) && !origin.hostname.startsWith('[') && origin.hostname.includes('.') && !origin.hostname.endsWith('.local');
  } catch { return false; }
}

function failure(res, status, code, message) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.end(JSON.stringify({ jsonrpc: '2.0', error: { code, message }, id: null }));
}

async function body(req) {
  if (Number(req.headers['content-length'] ?? 0) > MAX_BODY) {
    req.resume();
    throw Object.assign(new Error('Request body exceeds 32 KiB.'), { status: 413 });
  }
  if (req.body !== undefined) {
    const raw = typeof req.body === 'string' ? req.body : Buffer.isBuffer(req.body) ? req.body.toString('utf8') : JSON.stringify(req.body);
    if (Buffer.byteLength(raw) > MAX_BODY) throw Object.assign(new Error('Request body exceeds 32 KiB.'), { status: 413 });
    return JSON.parse(raw);
  }
  return new Promise((resolve, reject) => {
    const chunks = [];
    let bytes = 0;
    let oversized = false;
    req.on('data', chunk => {
      bytes += chunk.length;
      if (bytes > MAX_BODY) { oversized = true; chunks.length = 0; }
      else if (!oversized) chunks.push(chunk);
    });
    req.on('end', () => {
      if (oversized) { reject(Object.assign(new Error('Request body exceeds 32 KiB.'), { status: 413 })); return; }
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); } catch (error) { reject(error); }
    });
    req.on('error', reject);
  });
}

export default async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  if (!validMcpRequest(req.headers)) { failure(res, 403, -32000, 'Invalid Host or Origin.'); return; }
  cors(res, 'POST, OPTIONS');
  if (req.method === 'OPTIONS') { res.statusCode = 204; res.end(); return; }
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST, OPTIONS');
    failure(res, 405, -32000, 'Use Streamable HTTP POST. This stateless endpoint does not offer a standalone SSE stream.');
    return;
  }
  if (String(req.headers['content-type'] ?? '').split(';')[0].trim().toLowerCase() !== 'application/json') { failure(res, 415, -32000, 'Content-Type must be application/json.'); return; }
  let parsed;
  try { parsed = await body(req); } catch (error) { failure(res, error.status ?? 400, -32700, error.status ? error.message : 'Invalid JSON.'); return; }
  if (Array.isArray(parsed)) { failure(res, 400, -32600, 'JSON-RPC batch requests are not supported.'); return; }
  const server = createMcpServer();
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
  const cleanup = () => { void server.close(); };
  res.once('close', cleanup);
  try {
    await server.connect(transport);
    await transport.handleRequest(req, res, parsed);
  } catch (error) {
    console.error('MCP request failure:', error.name);
    if (!res.headersSent) failure(res, 500, -32603, 'Internal server error.');
    await server.close();
  }
}
