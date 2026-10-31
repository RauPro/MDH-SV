import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { JSONRPCErrorResponseSchema } from '@modelcontextprotocol/sdk/types.js';
import { createPublicServer } from '../server/dev.mjs';
import { searchNews } from '../server/public-data.mjs';
import { getOfficialStatistics } from '../server/official-statistics.mjs';
import { rest } from '../server/http.mjs';
import { openapi } from '../server/openapi.mjs';
import { pendingNewsDossier } from '../shared/news-dossier.mjs';
import fixtureArchive from '../apps/web/src/__fixtures__/newsArchive.json' with { type: 'json' };
import municipalityReference from '../apps/pipeline/src/mdh_pipeline/data/el_salvador_municipalities.json' with { type: 'json' };
import { responseContracts } from './openapi-validator.mjs';
import { loadContractCases } from './contract-fixtures.mjs';
import { parseCatalog } from '../shared/catalog.mjs';

let server, base, contracts, documented, firstRecord;
async function listen(server) {
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return `http://127.0.0.1:${server.address().port}`;
}
async function close(server) {
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
async function request(path, route = path.split('?')[0], status = 200, method = 'GET') {
  const response = await fetch(base + path, { method });
  assert.equal(response.status, status, `${method} ${path.slice(0, 150)}`);
  const text = await response.text();
  const body = text ? JSON.parse(text) : '';
  if (text) assert.match(response.headers.get('content-type'), /^application\/json/);
  // Unsupported methods are intentionally absent as operations; their 405
  // envelope is described with each public read operation.
  contracts.validate(route, status, body, method === 'POST' ? 'GET' : method);
  return body;
}

before(async () => {
  server = createPublicServer();
  base = await listen(server);
  documented = await (await fetch(base + '/api/v1/openapi.json')).json();
  assert.deepEqual(documented, openapi);
  contracts = responseContracts(documented);
  firstRecord = (await request('/api/v1/records?limit=1')).data[0];
});
after(async () => { if (server) await close(server); });

test('every actual REST operation, method and declared response schema is executable', async () => {
  const paths = Object.keys(documented.paths);
  assert.equal(paths.length, 7);
  for (const route of paths) {
    const path = route.replace('{id}', firstRecord.id);
    await request(path, route);
    await request(path, route, 200, 'HEAD');
    await request(path, route, 204, 'OPTIONS');
    await request(path, route, 405, 'POST');
    await request(path + '?unknown=1', route, 400);
    await request(path + '?unknown=1', route, 400, 'HEAD');
    await request(path + '?padding=' + 'a'.repeat(4100), route, 414);
  }
  await request('/api/v1/records/absent', '/api/v1/records/{id}', 404);
  await request('/api/v1/records/not_valid', '/api/v1/records/{id}', 400);
  const missing = await fetch(base + '/api/v1/unknown');
  assert.equal(missing.status, 404);
  assert.ok(contracts.components.Error(await missing.json()));
});

test('every current record and all news pages conform, retaining filters and terminal pagination', async () => {
  for (const route of ['/api/v1/records', '/api/v1/news']) {
    let path = route + '?limit=100';
    const ids = new Set();
    let total;
    do {
      const response = await request(path, route);
      total ??= response.pagination.total;
      assert.equal(response.pagination.total, total);
      assert.ok(response.data.length <= response.pagination.limit);
      for (const item of response.data) {
        assert.ok(!ids.has(item.id)); ids.add(item.id);
        if (route.endsWith('/records')) {
          const detail = await request(`${route}/${item.id}`, '/api/v1/records/{id}');
          assert.deepEqual(detail.data, item);
        }
      }
      if (response.pagination.next !== null) {
        const next = new URL(response.pagination.next);
        assert.equal(next.origin, documented.servers[0].url);
        assert.equal(next.searchParams.get('limit'), '100');
        assert.equal(Number(next.searchParams.get('offset')), response.pagination.offset + 100);
        path = next.pathname + next.search;
      } else path = null;
    } while (path);
    assert.equal(ids.size, total);
    const empty = await request(route + '?q=NONEXISTENT-CONTRACT-RESULT', route);
    assert.equal(empty.pagination.total, 0);
    assert.deepEqual(empty.data, []);
    assert.equal(empty.pagination.next, null);
  }
});

test('all documented filters are exercised through real HTTP and validate without coercing responses', async () => {
  const recordValues = {
    q: firstRecord.title.slice(0, 150), publisher: firstRecord.publisher,
    date_from: firstRecord.published_date, date_to: firstRecord.published_date,
    incident_date_from: '2010-01-01', incident_date_to: firstRecord.published_date,
    record_type: firstRecord.record_type, department: firstRecord.department ?? 'unknown',
    category: firstRecord.category, review_status: firstRecord.review_status, limit: 1, offset: 0,
  };
  const news = (await request('/api/v1/news?limit=1')).data[0];
  // Default (pre-sunset), summary and full responses all validate against the
  // declared contracts; the coverage endpoint ships the full object.
  const summary = await request('/api/v1/news?limit=1&coverage=summary', '/api/v1/news');
  assert.equal(summary.meta.coverage_detail, 'summary');
  assert.equal(Object.hasOwn(summary.meta.coverage, 'months'), false);
  assert.equal(Object.hasOwn(summary.meta.coverage.sources[0] ?? {}, 'months'), false);
  const coverage = await request('/api/v1/news/coverage');
  assert.ok(coverage.data.months.length > 0);
  assert.equal(coverage.meta.coverage_detail, undefined);
  const values = {
    '/api/v1/records': recordValues,
    '/api/v1/stats': recordValues,
    '/api/v1/news': { q: news.title.slice(0, 150), publisher: news.publisher, date_from: news.published_date, date_to: news.published_date, scope_hint: news.scope_hint, coverage: 'full', limit: 1, offset: 0 },
    '/api/v1/official-statistics': { year: 2020, department: 'national' },
  };
  for (const [route, available] of Object.entries(values)) {
    const combined = {};
    for (const parameter of documented.paths[route].get.parameters) {
      assert.ok(Object.hasOwn(available, parameter.name), `Missing exercised filter: ${parameter.name}`);
      combined[parameter.name] = available[parameter.name];
      await request(route + '?' + new URLSearchParams({ [parameter.name]: available[parameter.name] }), route);
    }
    await request(route + '?' + new URLSearchParams(combined), route);
    for (const query of ['unknown=1', 'q=a&q=b', 'date_from=2026-02-30']) {
      await request(`${route}?${query}`, route, 400);
    }
  }
  for (const route of ['/api/v1/records', '/api/v1/news']) {
    for (const query of ['limit=0', 'limit=101', 'offset=-1', 'q=', 'q=%20', 'date_from=2026-01-02&date_to=2026-01-01']) {
      await request(`${route}?${query}`, route, 400);
    }
  }
});

test('503 snapshot errors and unexpected 500 failures use the declared sanitized JSON envelope', async () => {
  const handlers = {
    '/api/v1/official-statistics': rest(() => getOfficialStatistics({}, { private_payload: 'PRIVATE_SENTINEL' })),
    '/api/v1/news': rest(() => searchNews({}, { private_payload: 'PRIVATE_SENTINEL' })),
    '/api/v1/stats': rest(() => { throw new Error('PRIVATE_SENTINEL'); }),
  };
  const failing = createServer((req, res) => handlers[req.url](req, res));
  const address = await listen(failing);
  try {
    for (const path of Object.keys(handlers)) {
      const response = await fetch(address + path);
      const status = path.endsWith('/stats') ? 500 : 503;
      assert.equal(response.status, status);
      const body = await response.json();
      contracts.validate(path, status, body);
      assert.ok(!JSON.stringify(body).includes('PRIVATE_SENTINEL'));
      assert.equal(response.headers.get('cache-control'), 'no-store');
    }
  } finally { await close(failing); }
});

test('MCP successful tool payloads execute the same OpenAPI schemas as their REST counterparts', async () => {
  const client = new Client({ name: 'openapi-contract-test', version: '1.0.0' });
  await client.connect(new StreamableHTTPClientTransport(new URL(base + '/mcp')));
  const mappings = [
    ['search_records', '/api/v1/records', { limit: 2 }],
    ['get_record', '/api/v1/records/{id}', { id: firstRecord.id }],
    ['get_statistics', '/api/v1/stats', {}],
    ['get_official_statistics', '/api/v1/official-statistics', { year: 2020, department: 'national' }],
    ['search_news', '/api/v1/news', { limit: 2 }],
    ['get_news_coverage', '/api/v1/news/coverage', {}],
  ];
  try {
    const tools = await client.listTools();
    assert.deepEqual(tools.tools.map(tool => tool.name).sort(), mappings.map(([name]) => name).sort());
    for (const [name, route, args] of mappings) {
      const result = await client.callTool({ name, arguments: args });
      assert.equal(result.isError, undefined);
      contracts.validate(route, 200, result.structuredContent);
      assert.deepEqual(JSON.parse(result.content[0].text), result.structuredContent);
      if (name === 'search_news') assert.equal(result.structuredContent.meta.coverage_detail, 'summary');
    }
    const unavailable = await client.callTool({ name: 'get_record', arguments: { id: 'absent' } });
    assert.equal(unavailable.isError, true);
    assert.equal(unavailable.structuredContent, undefined);
    assert.equal(typeof unavailable.content[0].text, 'string');
    const resource = await client.readResource({ uri: 'mdh-sv://api/openapi' });
    assert.deepEqual(JSON.parse(resource.contents[0].text), documented);
  } finally { await client.close(); }
  const headers = { 'content-type': 'application/json', accept: 'application/json, text/event-stream' };
  for (const [body, extraHeaders, status] of [
    ['[', {}, 400], ['[]', {}, 400], ['{}', { 'content-type': 'text/plain' }, 415],
    ['{}', { origin: 'https://attacker.example.org' }, 403], ['x'.repeat(33000), {}, 413],
  ]) {
    const response = await fetch(base + '/mcp', { method: 'POST', headers: { ...headers, ...extraHeaders }, body });
    assert.equal(response.status, status);
    const error = await response.json();
    assert.ok(JSONRPCErrorResponseSchema.safeParse(error).success);
    assert.equal(Object.hasOwn(error, 'id'), false);
  }
  for (const id of ['contract-request', 42]) {
    const response = await fetch(base + '/mcp', { method: 'POST', headers, body: JSON.stringify({ jsonrpc: '2.0', id, method: 'unknown_contract_method' }) });
    const error = await response.json();
    assert.ok(JSONRPCErrorResponseSchema.safeParse(error).success);
    assert.equal(error.id, id);
  }
});

test('unexpected MCP transport failures preserve readable IDs and omit invalid IDs', async context => {
  context.mock.method(StreamableHTTPServerTransport.prototype, 'handleRequest', async () => { throw new Error('PRIVATE_TRANSPORT_SENTINEL'); });
  const log = context.mock.method(console, 'error', () => {});
  const headers = { 'content-type': 'application/json', accept: 'application/json, text/event-stream' };
  for (const id of ['readable-request', 27, null, false, 1.5, []]) {
    const response = await fetch(base + '/mcp', { method: 'POST', headers,
      body: JSON.stringify({ jsonrpc: '2.0', id, method: 'tools/list' }) });
    assert.equal(response.status, 500);
    const error = await response.json();
    assert.ok(JSONRPCErrorResponseSchema.safeParse(error).success);
    if (typeof id === 'string' || Number.isSafeInteger(id)) assert.equal(error.id, id);
    else assert.equal(Object.hasOwn(error, 'id'), false);
    assert.ok(!JSON.stringify(error).includes('PRIVATE_TRANSPORT_SENTINEL'));
  }
  assert.ok(log.mock.calls.every(call => call.arguments.join(' ') === 'MCP request failure: Error'));
});

test('legacy location variants and pending, reviewed and automatically published dossiers remain valid', () => {
  const source = fixtureArchive.records[0];
  const archive = structuredClone(fixtureArchive);
  const empty = { schema_version: 1, dossiers: [] };
  const now = '2026-10-10T12:00:00Z';
  const location = { candidate_id: source.id, source_url: source.source_url, publisher: source.publisher,
    published_date: source.published_date, department: 'San Salvador', review_status: 'automated_unreviewed', human_reviewed: false,
    method: 'gemini_explicit_department', extracted_at: now, source_checked_at: now,
    evidence: { paragraph_id: 'p1', content_sha256: source.selection.evidence.content_sha256 } };
  const municipal = { ...location, department: 'Ahuachapán', method: 'gemini_municipality_reference', resolution: {
    type: 'municipality_to_department', municipality: 'Jujutla', municipality_kind: 'district',
    reference_version: municipalityReference.reference_version, source_url: `${municipalityReference.source.url}#page=6`,
  } };
  const { department, evidence: locationEvidence, ...locationBase } = location;
  const plural = { ...locationBase, method: 'source_administrative_mentions', rule_version: 2,
    evidence: { content_sha256: locationEvidence.content_sha256 }, mentions: [
      { method: 'source_explicit_department', department, paragraph_id: 'p1' },
      { method: 'source_municipality_reference', department: municipal.department, paragraph_id: 'p2', resolution: municipal.resolution },
    ] };
  const versions = [
    [1, location], [2, municipal],
    [3, { ...location, method: 'gemini_department_corroborated', evidence: { ...location.evidence, paragraph_id: 'p3' }, corroboration: { strategy: 'same_article_explicit_department', model_paragraph_id: 'p1', explicit_paragraph_id: 'p3', rule_version: 1 } }],
    [4, { ...location, method: 'source_explicit_department', rule_version: 1 }],
    [4, { ...municipal, method: 'source_municipality_reference', rule_version: 1 }], [5, plural],
  ];
  for (const [schema_version, item] of versions) {
    const collection = { schema_version, generated_at: now, locations: [item] };
    assert.ok(contracts.components.AutomaticNewsLocationCollection(collection));
    const privateCopy = structuredClone(collection);
    privateCopy.locations[0].evidence.source_quote = 'PRIVATE_SENTINEL';
    assert.equal(contracts.components.AutomaticNewsLocationCollection(privateCopy), false);
    const response = searchNews({}, archive, empty, collection, null);
    assert.deepEqual(response.data.find(record => record.id === source.id).automatic_location, item);
    contracts.validate('/api/v1/news', 200, response);
  }
  const human = { ...pendingNewsDossier(source), human_reviewed: true, extraction_status: 'processed', reviewed_by: 'reviewer', reviewed_at: '2026-10-10', review_notes: 'Revisión ficticia del contrato y privacidad.', classification: { jurisdiction: 'SV', relevance: 'relevant', record_type: null } };
  human.fields.summary = { value: 'Resumen ficticio atribuido a su fuente.', status: 'reported', evidence_refs: ['bibliography'], note: null };
  const automatic = { ...human, schema_version: 2, human_reviewed: false, reviewed_by: null, reviewed_at: null, review_notes: null,
    automation: { policy_version: 'auto-news-v1', source_content_sha256: source.selection.evidence.content_sha256, validated_at: now, method: 'gemini_evidence_review', evaluator_model: 'gemini-test', corroboration: 'single_source' } };
  for (const dossier of [null, human, automatic]) {
    const response = searchNews({}, archive, { schema_version: 1, dossiers: dossier ? [dossier] : [] }, null, null);
    assert.equal(response.data.find(record => record.id === source.id).news_dossier.schema_version, dossier?.schema_version ?? 1);
    contracts.validate('/api/v1/news', 200, response);
  }
});

test('accepted shared Python/JavaScript fixtures also satisfy the advertised wire schemas', () => {
  const schemas = { 'news-selection': 'NewsSelection', 'news-dossier': 'NewsDossier',
    'news-location': 'AutomaticNewsLocationCollection', 'news-processing': 'NewsProcessingCollection' };
  for (const [contract, schema] of Object.entries(schemas)) {
    for (const { name, valid, document } of loadContractCases(new URL(`./fixtures/contracts/${contract}.json`, import.meta.url))) {
      if (valid) assert.ok(contracts.components[schema](document), `${contract}: ${name}`);
    }
  }
  for (const { name, valid, document } of loadContractCases(new URL('./fixtures/contracts/catalogue.json', import.meta.url))) {
    if (!valid) continue;
    // Canonical parsers materialize optional defaults before JSON serialization.
    for (const record of parseCatalog(document).records) {
      assert.ok(contracts.components.Record(record), `catalogue: ${name}`);
    }
  }
});

test('malformed responses, missing evidence, private extras and invalid formats are rejected without repair', async () => {
  const recordResponse = await request('/api/v1/records?limit=1');
  const schema = documented.paths['/api/v1/records'].get.responses['200'].content['application/json'].schema;
  const validate = contracts.compile(schema);
  const changes = [
    value => { delete value.data[0].source_locator; },
    value => { delete value.data[0].dossier.hypothesis; },
    value => { value.data[0].victim_identity = 'PRIVATE_SENTINEL'; },
    value => { value.data[0].dossier.private_notes = 'PRIVATE_SENTINEL'; },
    value => { value.data[0].geometry = { type: 'Point', coordinates: [0, 0] }; },
    value => { value.data[0].published_date = '2026-02-30'; },
    value => { value.data[0].source_url = 'not a URI'; },
    value => { value.data[0].dossier.facts = { value: 'PRIVATE_SENTINEL', status: 'withheld', evidence_refs: [], note: 'No publicar datos identificativos.' }; },
    value => { value.data[0].dossier.facts = { value: 'Atribución sin evidencia.', status: 'reported', evidence_refs: [], note: null }; },
    value => { value.pagination.limit = '1'; },
    value => { value.pagination.total = -1; },
    value => { value.pagination.next = 'relative-page'; },
    value => { value.private_payload = 'PRIVATE_SENTINEL'; },
    value => { value.meta.private_payload = 'PRIVATE_SENTINEL'; },
  ];
  for (const change of changes) {
    const value = structuredClone(recordResponse); change(value);
    const original = structuredClone(value);
    assert.equal(validate(value), false, change.toString());
    assert.deepEqual(value, original, 'Schema validation must not coerce, strip or default a response.');
  }
  const news = await request('/api/v1/news?limit=1');
  const newsSchema = contracts.compile(documented.paths['/api/v1/news'].get.responses['200'].content['application/json'].schema);
  const unreviewed = structuredClone(news);
  unreviewed.data[0].news_dossier = pendingNewsDossier(unreviewed.data[0]);
  unreviewed.data[0].news_dossier.extraction_status = 'processed';
  assert.equal(newsSchema(unreviewed), false);
  news.data[0].selection = structuredClone(fixtureArchive.records[0].selection);
  news.data[0].selection.classified_at = '2026-02-30T12:00:00Z';
  assert.equal(newsSchema(news), false);
  const official = await request('/api/v1/official-statistics');
  official.data[0].source.private_quote = 'PRIVATE_SENTINEL';
  assert.equal(contracts.components.OfficialStatisticsResponse(official), false);
  const drift = structuredClone(documented);
  drift.components.schemas.Record.required.push('new_required_public_field');
  assert.throws(() => responseContracts(drift).validate('/api/v1/records', 200, recordResponse));
  const unknownKeyword = structuredClone(schema); unknownKeyword.requried = ['data'];
  assert.throws(() => contracts.compile(unknownKeyword), /unknown keyword/);
  assert.equal(contracts.components.Error({ error: { code: 'failure', message: 'Safe', stack: 'PRIVATE_SENTINEL' } }), false);
});
