#!/usr/bin/env node
// Starts the preview replica on an ephemeral port, runs the Playwright suite
// against it, and always tears the server down. The replica URL reaches the
// suite through MDH_PREVIEW_URL; playwright.config.ts reads it.

import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

const app = fileURLToPath(new URL('..', import.meta.url));
const server = spawn(
  process.execPath,
  [fileURLToPath(new URL('./preview-server.mjs', import.meta.url))],
  { env: { ...process.env, PORT: '0' }, stdio: ['ignore', 'pipe', 'inherit'] },
);

let baseURL = '';
server.stdout.setEncoding('utf8');
for await (const line of createInterface({ input: server.stdout })) {
  const match = /http:\/\/127\.0\.0\.1:\d+/.exec(line);
  if (match) { baseURL = match[0]; break; }
}
if (!baseURL) {
  server.kill();
  throw new Error('the preview replica did not report its port');
}

const cli = fileURLToPath(new URL('../node_modules/@playwright/test/cli.js', import.meta.url));
const suite = spawn(
  process.execPath,
  [cli, 'test', ...process.argv.slice(2)],
  { cwd: app, stdio: 'inherit', env: { ...process.env, MDH_PREVIEW_URL: baseURL } },
);
const [code] = await once(suite, 'exit');
server.kill();
process.exit(code ?? 1);
