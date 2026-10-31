import { test } from 'node:test';
import assert from 'node:assert/strict';
import { newsHandler } from '../api/v1/news.mjs';
import { newsCoverage, searchNews } from '../server/public-data.mjs';
import { createPublicServer } from '../server/dev.mjs';
import { instance, PUBLIC_URL } from '../server/instance.mjs';
import fixtureArchive from '../apps/web/src/__fixtures__/newsArchive.json' with { type: 'json' };

const empty = { schema_version: 1, dossiers: [] };
const PRE_SUNSET = '2027-01-15T12:00:00Z';
const POST_SUNSET = '2027-03-01T12:00:00Z';

function call(handler, path = '/api/v1/news') {
  const headers = {};
  const response = { statusCode: 0, headers,
    setHeader(key, value) { headers[key] = value; }, end() {} };
  return handler({ url: path, method: 'GET', headers: {} }, response)
    .then(() => ({ headers }));
}

test('an unparameterized request ships full coverage with deprecation headers before the sunset', async () => {
  const { headers } = await call(newsHandler(() => new Date(PRE_SUNSET)));
  assert.equal(headers.Deprecation, '@1793491200');
  assert.equal(headers.Sunset, 'Mon, 01 Feb 2027 00:00:00 GMT');
  assert.match(headers.Link, /<[^>]*\/api\/v1\/news\/coverage>; rel="alternate"; type="application\/json"/);
  assert.match(headers.Link, /docs\/API\.md#cobertura-del-archivo>; rel="deprecation"; type="text\/html"/);
});

test('an unparameterized request ships summary coverage without deprecation headers from the sunset on', async () => {
  const result = searchNews({ limit: 1 }, fixtureArchive, empty, null, null, new Date(POST_SUNSET));
  assert.equal(result.meta.coverage_detail, 'summary');
  assert.equal(Object.hasOwn(result.meta.coverage, 'months'), false);
  const { headers } = await call(newsHandler(() => new Date(POST_SUNSET)));
  assert.equal(headers.Deprecation, undefined);
  assert.equal(headers.Sunset, undefined);
  assert.equal(headers.Link, undefined);
});

test('an explicit coverage parameter wins over the clock and suppresses the deprecation headers', async () => {
  for (const coverage of ['summary', 'full']) {
    const detail = searchNews({ limit: 1, coverage }, fixtureArchive, empty, null, null, new Date(POST_SUNSET));
    assert.equal(detail.meta.coverage_detail, coverage);
    const { headers } = await call(newsHandler(() => new Date(PRE_SUNSET)), `/api/v1/news?coverage=${coverage}`);
    assert.equal(headers.Deprecation, undefined);
    assert.equal(headers.Sunset, undefined);
    assert.equal(headers.Link, undefined);
  }
});

test('summary keeps every scalar and per-source field and drops only the months lists', () => {
  const full = searchNews({ limit: 1, coverage: 'full' }, fixtureArchive, empty, null, null);
  const summary = searchNews({ limit: 1, coverage: 'summary' }, fixtureArchive, empty, null, null);
  assert.equal(summary.meta.coverage_detail, 'summary');
  const { months: _aggregateMonths, ...scalars } = full.meta.coverage;
  assert.deepEqual({ ...summary.meta.coverage, sources: undefined }, { ...scalars, sources: undefined });
  for (const [index, source] of summary.meta.coverage.sources.entries()) {
    const { months: _months, ...sourceScalars } = full.meta.coverage.sources[index];
    assert.deepEqual(source, sourceScalars);
  }
  // A summary response never mutates the archive's own coverage.
  assert.ok(fixtureArchive.coverage.months.length > 0);
});

test('the coverage endpoint ships the full coverage object plus generation metadata', () => {
  const response = newsCoverage(fixtureArchive);
  assert.deepEqual(response.data, fixtureArchive.coverage);
  assert.ok(response.data.months.length > 0);
  assert.equal(response.meta.generated_at, fixtureArchive.generated_at);
  assert.equal(response.meta.api_version, '1');
});

test('real HTTP: the coverage endpoint rejects every query parameter', async () => {
  const server = createPublicServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    for (const query of ['?x=1', '?coverage=full']) {
      const response = await fetch(`${base}/api/v1/news/coverage${query}`);
      assert.equal(response.status, 400);
      assert.equal((await response.json()).error.code, 'invalid_query');
    }
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});

test('real HTTP: unparameterized news ships the deprecation headers before the sunset and summary does not', async () => {
  const server = createPublicServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const before = new Date('2027-02-01T00:00:00Z') > new Date();
    const response = await fetch(base + '/api/v1/news?limit=1');
    assert.equal(response.status, 200);
    const body = await response.json();
    if (before) {
      assert.equal(response.headers.get('deprecation'), '@1793491200');
      assert.equal(response.headers.get('sunset'), 'Mon, 01 Feb 2027 00:00:00 GMT');
      const link = response.headers.get('link');
      assert.ok(link.includes(`${PUBLIC_URL}/api/v1/news/coverage`));
      assert.ok(link.includes(`https://github.com/${instance.public_repository}/blob/main/docs/API.md#cobertura-del-archivo`));
      assert.equal(body.meta.coverage_detail, 'full');
      assert.ok(Array.isArray(body.meta.coverage.months) && body.meta.coverage.months.length > 0);
    } else {
      assert.equal(body.meta.coverage_detail, 'summary');
    }
    const exposed = response.headers.get('access-control-expose-headers') ?? '';
    for (const header of ['Deprecation', 'Sunset', 'Link']) assert.ok(exposed.includes(header), `exposes ${header}`);

    const summary = await fetch(base + '/api/v1/news?limit=1&coverage=summary');
    assert.equal(summary.headers.get('deprecation'), null);
    assert.equal(summary.headers.get('sunset'), null);
    assert.equal(summary.headers.get('link'), null);
    assert.equal((await summary.json()).meta.coverage_detail, 'summary');
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});
