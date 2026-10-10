import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { createPublicServer } from '../server/dev.mjs';
import { getStats, loadCatalog, loadNews, searchNews, searchRecords } from '../server/public-data.mjs';
import { validMcpRequest } from '../api/mcp.mjs';
import recordHandler from '../api/v1/records/[id].mjs';
import unknownV1Handler from '../api/v1/[...path].mjs';
import { NEWS_DOSSIER_FIELDS, pendingNewsDossier } from '../shared/news-dossier.mjs';

let server, base;
before(async () => {
  server = createPublicServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${server.address().port}`;
});
after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
const get = async path => { const response = await fetch(`${base}${path}`); return { response, body: await response.json() }; };

async function vercelRequest(handler, url, query = {}) {
  const res = { statusCode: 200, headers: {}, setHeader(key, value) { this.headers[key.toLowerCase()] = value; }, end(value) { this.body = value ? JSON.parse(value) : null; } };
  await handler({ method: 'GET', url, query }, res);
  return res;
}

test('Vercel injected route ids work without permitting query-based record substitution', async () => {
  const id = 'mdh-ruth-lopez-2025';
  const routed = await vercelRequest(recordHandler, `/api/v1/records/${id}?id=${id}`, { id });
  assert.equal(routed.statusCode, 200);
  assert.equal(routed.body.data.id, id);
  const http = await get(`/api/v1/records/${id}?id=${id}`);
  assert.equal(http.response.status, 200);
  assert.equal(http.body.data.id, id);
  for (const url of [`/api/v1/records/${id}?id=other`, `/api/v1/records/${id}?id=${id}&id=${id}`, `/api/v1/records/${id}?id=${id}&limit=1`]) {
    assert.equal((await vercelRequest(recordHandler, url, { id })).statusCode, 400);
  }
  assert.equal((await vercelRequest(recordHandler, `/api/v1/records/${id}`, { id: 'other' })).statusCode, 400);
  assert.equal((await vercelRequest(recordHandler, `/api/v1/records/${id}?id=${id}`, { id: [id, id] })).statusCode, 400);
});

test('versioned Vercel fallback returns JSON even with injected catch-all routing parameters', async () => {
  const response = await vercelRequest(unknownV1Handler, '/api/v1/not-found?path=not-found', { path: 'not-found' });
  assert.equal(response.statusCode, 404);
  assert.equal(response.headers['content-type'], 'application/json; charset=utf-8');
  assert.equal(response.body.error.code, 'endpoint_not_found');
});

test('public REST pagination is deterministic and retains sources, review status and dossiers', async () => {
  const first = await get('/api/v1/records?limit=2');
  const second = await get('/api/v1/records?limit=2&offset=2');
  assert.equal(first.response.status, 200);
  assert.equal(first.response.headers.get('access-control-allow-origin'), '*');
  assert.equal(first.body.data.length, 2);
  assert.equal(first.body.pagination.next_offset, 2);
  assert.equal(new Set([...first.body.data, ...second.body.data].map(record => record.id)).size, 4);
  const id = first.body.data[0].id;
  const detail = await get(`/api/v1/records/${id}`);
  assert.deepEqual(detail.body.data, first.body.data[0]);
  assert.ok(detail.body.data.source_url.startsWith('https://'));
  assert.equal(typeof detail.body.data.human_reviewed, 'boolean');
  assert.ok(detail.body.data.review_status);
  assert.deepEqual(detail.body.data.dossier, loadCatalog().records.find(record => record.id === id).dossier);
});

test('REST filters combine type, location, review, text and inclusive publication dates', async () => {
  const record = loadCatalog().records.find(item => item.department && item.record_type === 'incident');
  const query = new URLSearchParams({ record_type: 'incident', department: record.department, review_status: record.review_status, publisher: record.publisher, date_from: record.published_date, date_to: record.published_date, q: record.title });
  const { response, body } = await get(`/api/v1/records?${query}`);
  assert.equal(response.status, 200);
  assert.ok(body.data.some(item => item.id === record.id));
  assert.ok(body.data.every(item => item.record_type === 'incident' && item.department === record.department));
  const empty = await get('/api/v1/records?q=definitely-no-such-source-987');
  assert.equal(empty.body.pagination.total, 0);
});

test('institutional queries distinguish publication dates from event intervals and unknown dates', () => {
  const catalog = { schema_version: 1, records: [
    { id: 'known', published_date: '2026-01-01', incident_date: '2025-01-01', incident_date_end: '2025-02-01' },
    { id: 'unknown', published_date: '2026-01-01', incident_date: null },
  ] };
  assert.equal(searchRecords({ date_from: '2026-01-01' }, catalog).pagination.total, 2);
  assert.deepEqual(searchRecords({ incident_date_from: '2025-01-15', incident_date_to: '2025-01-16' }, catalog).data.map(record => record.id), ['known']);
  assert.equal(searchRecords({ incident_date_from: '2026-01-01' }, catalog).pagination.total, 0);
});

test('invalid and ambiguous REST input fails closed with JSON errors', async () => {
  for (const query of ['limit=0', 'limit=101', 'offset=-1', 'limit=2.5', 'limit=NaN', 'date_from=2025-02-30', 'date_from=2026-10-01&date_to=2025-01-01', 'incident_date_from=2026-01-01&incident_date_to=2025-01-01', 'record_type=victim', 'review_status=pending', 'q=a&q=b', 'secret=true', 'q=', `q=${'a'.repeat(201)}`, '__proto__=x']) {
    const { response, body } = await get(`/api/v1/records?${query}`);
    assert.equal(response.status, 400, query);
    assert.equal(body.error.code, 'invalid_query', query);
  }
  assert.equal((await get('/api/v1/records/absent')).response.status, 404);
  assert.equal((await get('/api/v1/records/not_valid')).response.status, 400);
  assert.equal((await get('/api/v1/unknown')).response.status, 404);
  assert.equal((await get('/api/v1/records/absent?id=other')).response.status, 400);
});

test('REST method and CORS handling permits anonymous reads and denies changes', async () => {
  const preflight = await fetch(`${base}/api/v1/records`, { method: 'OPTIONS', headers: { origin: 'https://research.example.org', 'access-control-request-method': 'GET' } });
  assert.equal(preflight.status, 204);
  assert.equal(preflight.headers.get('access-control-allow-origin'), '*');
  assert.equal((await fetch(`${base}/api/v1/records`, { method: 'POST', body: '{}' })).status, 405);
  const head = await fetch(`${base}/api/v1/records`, { method: 'HEAD' });
  assert.equal(head.status, 200);
  assert.equal(await head.text(), '');
});

test('stats preserve record units and disclose documentary review', async () => {
  const { response, body } = await get('/api/v1/stats');
  assert.equal(response.status, 200);
  assert.equal(body.data.total_records, loadCatalog().records.length);
  assert.equal(Object.values(body.data.by_record_type).reduce((a, b) => a + b, 0), body.data.total_records);
  assert.equal(Object.values(body.data.by_review_status).reduce((a, b) => a + b, 0), body.data.total_records);
  assert.ok(body.meta.limitations.includes('víctimas'));
  const filtered = getStats({ record_type: 'publication' });
  assert.equal(filtered.data.total_records, filtered.data.by_record_type.publication);
});

test('news is a separate metadata archive with coverage and explicit unreviewed state', () => {
  const archive = { schema_version: 1, coverage: { start_date: '2025-03-24', end_date: '2026-10-09', status: 'partial' }, records: [
    { id: 'news-1', title: 'Noticia encontrada', publisher: 'Medio', source_url: 'https://example.org/news', published_date: '2026-10-01', review_status: 'not_reviewed', topics: ['detención'] },
  ] };
  const result = searchNews({ q: 'detencion' }, archive);
  assert.equal(result.data[0].review_status, 'not_reviewed');
  assert.equal(result.meta.coverage.status, 'partial');
  assert.ok(result.meta.review_notice.includes('no constituyen casos validados'));
  assert.deepEqual(Object.keys(result.data[0].news_dossier.fields), NEWS_DOSSIER_FIELDS);
  assert.ok(Object.values(result.data[0].news_dossier.fields).every(field => field.status === 'pending' && field.value === null));
});

test('every news result has a worksheet, including international or unclassified items, without inferring country or private processing', () => {
  const current = loadNews();
  for (const scope of ['el_salvador', 'international_context', 'undetermined']) {
    const response = searchNews({ scope_hint: scope, limit: 2 }, current, { schema_version: 1, dossiers: [] });
    assert.ok(response.data.length > 0);
    for (const reference of response.data) {
      assert.equal(reference.news_dossier.kind, 'news_dossier');
      assert.equal(reference.news_dossier.candidate_id, reference.id);
      assert.equal(reference.news_dossier.source_title, reference.title);
      assert.equal(reference.news_dossier.fields.country.status, 'pending');
      assert.equal(reference.news_dossier.fields.country.value, null);
      assert.equal(reference.news_dossier.classification.jurisdiction, 'unclear');
      assert.equal(reference.news_dossier.human_reviewed, false);
      assert.equal(reference.news_dossier.extraction_status, 'pending');
    }
  }
});

test('public news enrichment uses only approved dossiers bound to the exact bibliography and never changes case counts', () => {
  const source = loadNews().records[0];
  const archive = { ...loadNews(), records: [source] };
  const dossier = pendingNewsDossier(source);
  Object.assign(dossier, { extraction_status: 'processed', human_reviewed: true, reviewed_by: 'reviewer', reviewed_at: '2026-10-10', review_notes: 'Fixture de revisión editorial explícita de fuente y privacidad.', source_title: 'Título ficticio minimizado por privacidad' });
  dossier.classification = { jurisdiction: 'other', relevance: 'not_relevant', record_type: null };
  dossier.fields.summary = { value: 'APPROVED_TEST_VALUE', status: 'reported', evidence_refs: ['bibliography'], note: null };
  const baseline = getStats();
  const approved = searchNews({}, archive, { schema_version: 1, dossiers: [dossier] });
  assert.equal(approved.data[0].review_status, 'not_reviewed');
  assert.equal(approved.data[0].news_dossier.human_reviewed, true);
  assert.equal(approved.data[0].news_dossier.fields.summary.value, 'APPROVED_TEST_VALUE');
  assert.equal(approved.data[0].news_dossier.classification.relevance, 'not_relevant');
  assert.deepEqual(getStats(), baseline);
  for (const change of [
    value => { value.human_reviewed = false; },
    value => { value.publisher = 'Editorial equivocada'; },
    value => { value.published_date = '2025-01-01'; },
    value => { value.source_url = 'https://www.diariocolatino.com/wrong-source/'; },
    value => { value.fields.summary.evidence_refs = ['unknown']; },
    value => { value.private_model_output = 'PRIVATE_TEST_VALUE'; },
  ]) {
    const unsafe = structuredClone(dossier); change(unsafe);
    const response = searchNews({}, archive, { schema_version: 1, dossiers: [unsafe] });
    assert.equal(response.data[0].news_dossier.human_reviewed, false);
    assert.equal(response.data[0].news_dossier.fields.summary.status, 'pending');
    assert.ok(!JSON.stringify(response).includes('APPROVED_TEST_VALUE'));
    assert.ok(!JSON.stringify(response).includes('PRIVATE_TEST_VALUE'));
  }
});

test('OpenAPI is a public, versioned executable contract', async () => {
  const { response, body } = await get('/api/v1/openapi.json');
  assert.equal(response.status, 200);
  assert.equal(body.openapi, '3.1.0');
  assert.deepEqual(body.security, []);
  assert.ok(body.paths['/api/v1/records/{id}']);
  assert.ok(body.paths['/api/v1/news']);
  assert.ok(body.components.schemas.News.required.includes('news_dossier'));
  assert.deepEqual(body.components.schemas.NewsDossier.properties.fields.required, NEWS_DOSSIER_FIELDS);
  assert.equal(body.components.schemas.Record.properties.review_status.enum.includes('not_reviewed'), false);
});

test('historical news endpoint serves the actual public snapshot and preserves collection policy', async () => {
  const archive = loadNews();
  const { response, body } = await get(`/api/v1/news?limit=2&date_from=${archive.coverage.start_date}&date_to=${archive.coverage.end_date}`);
  assert.equal(response.status, 200);
  assert.ok(body.pagination.total > 0);
  assert.ok(body.data.every(record => record.review_status === 'not_reviewed' && record.source_url));
  assert.deepEqual(body.meta.coverage, archive.coverage);
  assert.deepEqual(body.meta.paper, archive.paper);
  assert.deepEqual(body.meta.policy, archive.policy);
  assert.equal((await get('/api/v1/news?record_type=incident')).response.status, 400);
  assert.equal((await get('/api/v1/news?scope_hint=confirmed')).response.status, 400);
  for (const scope of ['el_salvador', 'international_context', 'undetermined']) {
    const { response: scopedResponse, body: scoped } = await get(`/api/v1/news?scope_hint=${scope}&limit=100`);
    assert.equal(scopedResponse.status, 200);
    assert.equal(scoped.pagination.total, archive.records.filter(record => record.scope_hint === scope).length);
    assert.ok(scoped.data.every(record => record.scope_hint === scope && record.jurisdiction_status === 'not_reviewed'));
  }
});

test('MCP officially connects, discovers read-only tools, calls and reads resources over HTTP', async () => {
  const client = new Client({ name: 'mdh-integration-test', version: '1.0.0' });
  await client.connect(new StreamableHTTPClientTransport(new URL(`${base}/mcp`)));
  try {
    const listed = await client.listTools();
    assert.deepEqual(listed.tools.map(tool => tool.name).sort(), ['get_record', 'get_statistics', 'search_news', 'search_records']);
    assert.ok(listed.tools.every(tool => tool.annotations.readOnlyHint && !tool.annotations.destructiveHint));
    const result = await client.callTool({ name: 'search_records', arguments: { limit: 1, record_type: 'incident' } });
    assert.equal(result.isError, undefined);
    assert.equal(result.structuredContent.data.length, 1);
    const record = await client.callTool({ name: 'get_record', arguments: { id: result.structuredContent.data[0].id } });
    assert.equal(record.structuredContent.data.source_url, result.structuredContent.data[0].source_url);
    const bad = await client.callTool({ name: 'search_records', arguments: { limit: 1000 } });
    assert.equal(bad.isError, true);
    const news = await client.callTool({ name: 'search_news', arguments: { limit: 1, scope_hint: 'el_salvador' } });
    assert.equal(news.structuredContent.data[0].review_status, 'not_reviewed');
    assert.equal(news.structuredContent.data[0].scope_hint, 'el_salvador');
    assert.ok(Array.isArray(news.structuredContent.data[0].scope_terms));
    assert.ok(news.structuredContent.meta.policy.notice);
    assert.deepEqual(Object.keys(news.structuredContent.data[0].news_dossier.fields), NEWS_DOSSIER_FIELDS);
    assert.equal(news.structuredContent.data[0].news_dossier.kind, 'news_dossier');
    const resources = await client.listResources();
    assert.equal(resources.resources.length, 2);
    const resource = await client.readResource({ uri: 'mdh-sv://catalog/metadata' });
    assert.equal(JSON.parse(resource.contents[0].text).data.total_records, loadCatalog().records.length);
  } finally { await client.close(); }
});

test('MCP validates origins and hostnames without blocking native public clients', () => {
  const prod = { VERCEL: '1', VERCEL_URL: 'mdh-deploy.vercel.app' };
  assert.equal(validMcpRequest({ host: 'mdh-sv.vercel.app' }, prod), true);
  assert.equal(validMcpRequest({ host: 'mdh-sv.vercel.app', origin: 'https://research.example.org' }, prod), true);
  assert.equal(validMcpRequest({ host: 'mdh-deploy.vercel.app', origin: 'https://research.example.org' }, prod), true);
  for (const origin of ['null', 'http://research.example.org', 'https://127.0.0.1', 'https://evil.example/path', 'https://evil.example https://other.example', 'file:///secret', 'https://host.local']) {
    assert.equal(validMcpRequest({ host: 'mdh-sv.vercel.app', origin }, prod), false, origin);
  }
  assert.equal(validMcpRequest({ host: 'attacker.example', origin: 'https://research.example.org' }, prod), false);
  assert.equal(validMcpRequest({ host: 'localhost:3001', origin: 'https://attacker.example' }, {}), false);
  assert.equal(validMcpRequest({ host: '127.0.0.1:3001', origin: 'http://localhost:3000' }, {}), true);
  assert.equal(validMcpRequest({ host: '127.0.0.1:3001' }, prod), false);
});

test('MCP rejects malformed, oversized, batched requests and unsupported methods', async () => {
  const headers = { 'content-type': 'application/json', accept: 'application/json, text/event-stream' };
  assert.equal((await fetch(`${base}/mcp`)).status, 405);
  assert.equal((await fetch(`${base}/mcp`, { method: 'DELETE' })).status, 405);
  assert.equal((await fetch(`${base}/mcp`, { method: 'POST', headers: { ...headers, origin: 'https://evil.example' }, body: '{}' })).status, 403);
  assert.equal((await fetch(`${base}/mcp`, { method: 'POST', headers, body: '[' })).status, 400);
  assert.equal((await fetch(`${base}/mcp`, { method: 'POST', headers, body: '[]' })).status, 400);
  assert.equal((await fetch(`${base}/mcp`, { method: 'POST', headers, body: JSON.stringify({ payload: 'x'.repeat(33000) }) })).status, 413);
  assert.equal((await fetch(`${base}/mcp`, { method: 'POST', headers: { 'content-type': 'text/plain' }, body: '{}' })).status, 415);
});
