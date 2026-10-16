import { createHash, createPrivateKey, sign, timingSafeEqual } from 'node:crypto';
import { instance } from '../../server/instance.mjs';
import { validateInstanceConfig } from '../../shared/instance-config.mjs';

const GITHUB_API = 'https://api.github.com';
const ENDPOINT_PATH = '/api/internal/hourly-ingestion';
const CRON_SCHEDULE = '7 * * * *';
const NETWORK_TIMEOUT_MS = 8_000;

function reply(response, status, body) {
  response.statusCode = status;
  response.setHeader('Content-Type', 'application/json; charset=utf-8');
  response.setHeader('Cache-Control', 'no-store, max-age=0');
  response.setHeader('X-Content-Type-Options', 'nosniff');
  response.end(JSON.stringify(body));
}

function authenticated(header, secret) {
  if (typeof header !== 'string' || header.length > 8_192) return false;
  const supplied = createHash('sha256').update(header).digest();
  const expected = createHash('sha256').update(`Bearer ${secret}`).digest();
  return timingSafeEqual(supplied, expected);
}

function numericId(value) {
  if (typeof value !== 'string' || !/^[1-9]\d{0,14}$/.test(value)) throw new Error('invalid_config');
  const id = Number(value);
  if (!Number.isSafeInteger(id)) throw new Error('invalid_config');
  return id;
}

function configuration(env) {
  const appId = numericId(env.MDH_DISPATCH_APP_ID);
  const installationId = numericId(env.MDH_DISPATCH_INSTALLATION_ID);
  const repositoryId = numericId(env.MDH_DISPATCH_REPOSITORY_ID);
  const pem = env.MDH_DISPATCH_PRIVATE_KEY;
  if (typeof pem !== 'string' || pem.length > 16_384) throw new Error('invalid_config');
  const key = createPrivateKey(pem);
  if (key.asymmetricKeyType !== 'rsa' || key.asymmetricKeyDetails.modulusLength < 2_048) {
    throw new Error('invalid_config');
  }
  return { appId, installationId, repositoryId, key };
}

function githubJwt(config, currentTime) {
  const issuedAt = Math.floor(currentTime.getTime() / 1_000);
  const encode = value => Buffer.from(JSON.stringify(value)).toString('base64url');
  const unsigned = `${encode({ alg: 'RS256', typ: 'JWT' })}.${encode({
    iat: issuedAt - 30,
    exp: issuedAt + 540,
    iss: String(config.appId),
  })}`;
  return `${unsigned}.${sign('RSA-SHA256', Buffer.from(unsigned), config.key).toString('base64url')}`;
}

function githubHeaders(credential) {
  return {
    Accept: 'application/vnd.github+json',
    Authorization: `Bearer ${credential}`,
    'Content-Type': 'application/json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'MDH-SV-hourly-dispatcher',
  };
}

function emptyRequest(request) {
  if (request.url !== ENDPOINT_PATH) return false;
  const length = request.headers?.['content-length'];
  if (length !== undefined && length !== '0') return false;
  if (request.headers?.['transfer-encoding'] !== undefined) return false;
  return request.body === undefined || request.body === null || request.body === '';
}

/** Request one private workflow run. The runner owns hourly deduplication and quota enforcement. */
export function createHourlyIngestionHandler({
  env = process.env,
  fetchImpl = globalThis.fetch,
  now = () => new Date(),
  timeoutMs = NETWORK_TIMEOUT_MS,
  instanceConfig = instance,
} = {}) {
  const target = validateInstanceConfig(instanceConfig);
  const repositoryPath = `/repos/${target.private_repository}`;
  return async function hourlyIngestion(request, response) {
    const secret = env.CRON_SECRET;
    if (env.VERCEL_ENV !== 'production' || typeof secret !== 'string' || secret.length < 32 || secret.length > 4_096) {
      return reply(response, 503, { error: 'dispatcher_unavailable' });
    }
    if (!authenticated(request.headers?.authorization, secret)) {
      return reply(response, 401, { error: 'unauthorized' });
    }
    if (request.method !== 'GET') {
      response.setHeader('Allow', 'GET');
      return reply(response, 405, { error: 'method_not_allowed' });
    }
    if (!emptyRequest(request)) return reply(response, 400, { error: 'invalid_request' });
    // Provider metadata distinguishes ordinary manual probes after authentication;
    // it is an attribution hint, not a separate signature or authorization check.
    const triggerSource = request.headers?.['x-vercel-cron-schedule'] === CRON_SCHEDULE ? 'vercel_cron' : 'manual';

    let config;
    let jwt;
    let scheduledHour;
    try {
      config = configuration(env);
      const currentTime = now();
      jwt = githubJwt(config, currentTime);
      scheduledHour = new Date(Math.floor(currentTime.getTime() / 3_600_000) * 3_600_000)
        .toISOString().replace('.000Z', 'Z');
    } catch {
      return reply(response, 503, { error: 'dispatcher_unavailable' });
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    timer.unref?.();
    try {
      const tokenResponse = await fetchImpl(`${GITHUB_API}/app/installations/${config.installationId}/access_tokens`, {
        method: 'POST',
        redirect: 'error',
        signal: controller.signal,
        headers: githubHeaders(jwt),
        body: JSON.stringify({ repository_ids: [config.repositoryId], permissions: { actions: 'write' } }),
      });
      if (tokenResponse.status !== 201) throw new Error('token_unavailable');
      const tokenData = await tokenResponse.json();
      if (typeof tokenData.token !== 'string' || !tokenData.token || tokenData.token.length > 4_096
          || tokenData.permissions?.actions !== 'write'
          || Object.entries(tokenData.permissions).some(([name, permission]) => name !== 'actions'
            && (name !== 'metadata' || permission !== 'read'))) throw new Error('invalid_token_response');

      // A numeric installation scope is insufficient if the operator copied a
      // different repository name. Prove both identities and private visibility.
      const repositoryResponse = await fetchImpl(`${GITHUB_API}${repositoryPath}`, {
        method: 'GET', redirect: 'error', signal: controller.signal,
        headers: githubHeaders(tokenData.token),
      });
      if (repositoryResponse.status !== 200) throw new Error('repository_unavailable');
      const repository = await repositoryResponse.json();
      if (repository.private !== true || repository.id !== config.repositoryId
          || typeof repository.full_name !== 'string'
          || repository.full_name.toLowerCase() !== target.private_repository.toLowerCase()) throw new Error('invalid_repository');

      const dispatchResponse = await fetchImpl(`${GITHUB_API}${repositoryPath}/actions/workflows/extract.yml/dispatches`, {
        method: 'POST',
        redirect: 'error',
        signal: controller.signal,
        headers: githubHeaders(tokenData.token),
        body: JSON.stringify({
          ref: 'main',
          inputs: {
            batch_size: '50',
            retry_failed: 'false',
            scheduled_hour: scheduledHour,
            trigger_source: triggerSource,
          },
        }),
      });
      if (dispatchResponse.status !== 204) throw new Error('dispatch_failed');
      // Acknowledgement is not evidence that the queued workflow has executed.
      return reply(response, 202, { accepted: true, trigger_source: triggerSource, scheduled_hour: scheduledHour });
    } catch {
      return reply(response, 502, { error: 'dispatch_failed' });
    } finally {
      clearTimeout(timer);
    }
  };
}

export default createHourlyIngestionHandler();
