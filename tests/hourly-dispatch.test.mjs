import assert from 'node:assert/strict';
import { generateKeyPairSync, verify } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { createHourlyIngestionHandler } from '../api/internal/hourly-ingestion.mjs';

const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2_048 });
const secret = 'test-cron-authorization-'.repeat(3);
const currentTime = new Date('2026-10-11T23:59:59.999-06:00');
const baseEnv = {
  VERCEL_ENV: 'production',
  CRON_SECRET: secret,
  MDH_DISPATCH_APP_ID: '12345',
  MDH_DISPATCH_INSTALLATION_ID: '23456',
  MDH_DISPATCH_REPOSITORY_ID: '34567',
  MDH_DISPATCH_PRIVATE_KEY: privateKey.export({ format: 'pem', type: 'pkcs8' }),
};

async function invoke({ env = {}, request = {}, fetchImpl, timeoutMs } = {}) {
  const headers = {};
  const response = {
    statusCode: null,
    setHeader(name, value) { headers[name] = value; },
    end(body) { this.body = JSON.parse(body); },
  };
  let calls = 0;
  const handler = createHourlyIngestionHandler({
    env: { ...baseEnv, ...env },
    now: () => currentTime,
    fetchImpl: async (...args) => {
      calls += 1;
      if (!fetchImpl) throw new Error('Network was not expected');
      return fetchImpl(...args);
    },
    ...(timeoutMs === undefined ? {} : { timeoutMs }),
  });
  await handler({
    method: 'GET',
    url: '/api/internal/hourly-ingestion',
    headers: { authorization: `Bearer ${secret}` },
    ...request,
  }, response);
  return { status: response.statusCode, body: response.body, headers, calls };
}

const installationToken = () => new Response(JSON.stringify({
  token: 'ephemeral-test-installation-token',
  permissions: { actions: 'write', metadata: 'read' },
}), { status: 201 });

test('cron dispatch is scoped to one repository, one workflow and the current UTC hour', async () => {
  const requests = [];
  const result = await invoke({ fetchImpl: async (url, options) => {
    requests.push({ url, ...options });
    return requests.length === 1 ? installationToken() : new Response(null, { status: 204 });
  } });
  assert.equal(result.status, 202);
  assert.deepEqual(result.body, {
    accepted: true,
    trigger_source: 'vercel_cron',
    scheduled_hour: '2026-10-12T05:00:00Z',
  });
  assert.equal(result.body.executed, undefined);
  assert.equal(result.headers['Cache-Control'], 'no-store, max-age=0');
  assert.equal(requests.length, 2);
  assert.equal(requests[0].url, 'https://api.github.com/app/installations/23456/access_tokens');
  assert.deepEqual(JSON.parse(requests[0].body), {
    repository_ids: [34567], permissions: { actions: 'write' },
  });
  const jwt = requests[0].headers.Authorization.slice('Bearer '.length);
  const [header, payload, signature] = jwt.split('.');
  assert.deepEqual(JSON.parse(Buffer.from(header, 'base64url')), { alg: 'RS256', typ: 'JWT' });
  assert.deepEqual(JSON.parse(Buffer.from(payload, 'base64url')), {
    iat: Math.floor(currentTime.getTime() / 1_000) - 30,
    exp: Math.floor(currentTime.getTime() / 1_000) + 540,
    iss: '12345',
  });
  assert.ok(verify('RSA-SHA256', Buffer.from(`${header}.${payload}`), publicKey, Buffer.from(signature, 'base64url')));
  assert.equal(requests[1].url, 'https://api.github.com/repos/RauPro/MDH-SV-review/actions/workflows/extract.yml/dispatches');
  assert.equal(requests[1].headers.Authorization, 'Bearer ephemeral-test-installation-token');
  assert.deepEqual(JSON.parse(requests[1].body), {
    ref: 'main',
    inputs: {
      batch_size: '50', retry_failed: 'false',
      scheduled_hour: '2026-10-12T05:00:00Z', trigger_source: 'vercel_cron',
    },
  });
  for (const request of requests) {
    assert.equal(request.method, 'POST');
    assert.equal(request.redirect, 'error');
    assert.equal(request.headers['X-GitHub-Api-Version'], '2022-11-28');
  }
  assert.equal(requests[0].signal, requests[1].signal);
});

test('preview, development and missing or weak authorization configuration fail closed', async () => {
  for (const env of [
    { VERCEL_ENV: 'preview' }, { VERCEL_ENV: 'development' }, { VERCEL_ENV: undefined },
    { CRON_SECRET: undefined }, { CRON_SECRET: 'short' }, { CRON_SECRET: 'x'.repeat(4_097) },
  ]) {
    const result = await invoke({ env });
    assert.equal(result.status, 503);
    assert.deepEqual(result.body, { error: 'dispatcher_unavailable' });
    assert.equal(result.calls, 0);
  }
});

test('missing, malformed, overlong and wrong bearer credentials never call GitHub', async () => {
  for (const authorization of [undefined, '', ['Bearer incorrect'], `bearer ${secret}`, `Bearer ${secret} `, 'x'.repeat(8_193)]) {
    const result = await invoke({ request: { headers: { authorization } } });
    assert.equal(result.status, 401);
    assert.deepEqual(result.body, { error: 'unauthorized' });
    assert.equal(result.calls, 0);
  }
});

test('only GET on the exact endpoint without query or body is accepted', async () => {
  for (const method of ['POST', 'PUT', 'HEAD', 'OPTIONS', undefined]) {
    const result = await invoke({ request: { method } });
    assert.equal(result.status, 405);
    assert.equal(result.headers.Allow, 'GET');
    assert.equal(result.calls, 0);
  }
  for (const request of [
    { url: '/api/internal/hourly-ingestion?batch_size=999' },
    { url: '/api/internal/hourly-ingestion?' },
    { url: '/api/internal/hourly-ingestion/' },
    { body: { repository: 'another/repo' } },
    { body: 'payload' },
    { headers: { authorization: `Bearer ${secret}`, 'content-length': '1' } },
    { headers: { authorization: `Bearer ${secret}`, 'transfer-encoding': 'chunked' } },
  ]) {
    const result = await invoke({ request });
    assert.equal(result.status, 400);
    assert.deepEqual(result.body, { error: 'invalid_request' });
    assert.equal(result.calls, 0);
  }
});

test('invalid GitHub App configuration is sanitized before any network request', async () => {
  const weakKey = generateKeyPairSync('rsa', { modulusLength: 1_024 }).privateKey.export({ format: 'pem', type: 'pkcs8' });
  const ecKey = generateKeyPairSync('ec', { namedCurve: 'prime256v1' }).privateKey.export({ format: 'pem', type: 'pkcs8' });
  for (const env of [
    { MDH_DISPATCH_APP_ID: undefined }, { MDH_DISPATCH_APP_ID: '0' },
    { MDH_DISPATCH_INSTALLATION_ID: '../other' }, { MDH_DISPATCH_REPOSITORY_ID: '9007199254740992' },
    { MDH_DISPATCH_PRIVATE_KEY: undefined }, { MDH_DISPATCH_PRIVATE_KEY: 'sensitive-invalid-pem' },
    { MDH_DISPATCH_PRIVATE_KEY: 'x'.repeat(16_385) }, { MDH_DISPATCH_PRIVATE_KEY: weakKey },
    { MDH_DISPATCH_PRIVATE_KEY: ecKey },
  ]) {
    const result = await invoke({ env });
    assert.equal(result.status, 503);
    assert.deepEqual(result.body, { error: 'dispatcher_unavailable' });
    assert.equal(result.calls, 0);
  }
});

test('token errors, unexpected grants and malformed responses cannot dispatch or leak provider details', async () => {
  for (const response of [
    new Response('sensitive-provider-error', { status: 403 }),
    new Response('', { status: 302, headers: { Location: 'https://unexpected.example' } }),
    new Response('{invalid-json', { status: 201 }),
    new Response(JSON.stringify({ token: 'private-test-token', permissions: { actions: 'read' } }), { status: 201 }),
    new Response(JSON.stringify({ token: 'private-test-token', permissions: { actions: 'write', contents: 'write' } }), { status: 201 }),
    new Response(JSON.stringify({ token: '', permissions: { actions: 'write' } }), { status: 201 }),
  ]) {
    const result = await invoke({ fetchImpl: async () => response });
    assert.equal(result.status, 502);
    assert.deepEqual(result.body, { error: 'dispatch_failed' });
    assert.equal(result.calls, 1);
  }
});

test('workflow dispatch failure never returns a success acknowledgement', async () => {
  for (const status of [200, 202, 302, 401, 404, 422, 429, 500]) {
    let count = 0;
    const result = await invoke({ fetchImpl: async () => ++count === 1
      ? installationToken() : new Response('private-provider-detail', { status }) });
    assert.equal(result.status, 502);
    assert.deepEqual(result.body, { error: 'dispatch_failed' });
    assert.equal(result.calls, 2);
  }
});

test('network errors are sanitized and the entire network operation has a bounded deadline', async () => {
  const failed = await invoke({ fetchImpl: async () => { throw new Error('secret-token-sensitive-error'); } });
  assert.equal(failed.status, 502);
  assert.deepEqual(failed.body, { error: 'dispatch_failed' });
  let aborted = false;
  const timedOut = await invoke({ timeoutMs: 20, fetchImpl: async (_url, { signal }) => new Promise((resolve, reject) => {
    const fallback = setTimeout(() => reject(new Error('timeout was not enforced')), 500);
    signal.addEventListener('abort', () => {
      aborted = true;
      clearTimeout(fallback);
      reject(signal.reason);
    }, { once: true });
  }) });
  assert.equal(timedOut.status, 502);
  assert.equal(aborted, true);
  assert.equal(timedOut.calls, 1);
  assert.deepEqual(timedOut.body, { error: 'dispatch_failed' });
});

test('deployment config schedules the authenticated endpoint hourly within function timeout', async () => {
  const config = JSON.parse(await readFile(new URL('../vercel.json', import.meta.url), 'utf8'));
  assert.deepEqual(config.crons, [{ path: '/api/internal/hourly-ingestion', schedule: '7 * * * *' }]);
  assert.ok(config.functions['api/**/*.mjs'].maxDuration > 8);
});
