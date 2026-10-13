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
import fixtureArchive from '../apps/web/src/__fixtures__/newsArchive.json' with { type: 'json' };
import { assertSelectedNewsArchive, validateNewsSelection } from '../shared/news-selection.mjs';
import municipalityReference from '../apps/pipeline/src/mdh_pipeline/data/el_salvador_municipalities.json' with { type: 'json' };

let server, base;
before(async () => {
  server = createPublicServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${server.address().port}`;
});
after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
const selectedArchive = (records, base = fixtureArchive) => ({ ...base, coverage: { ...base.coverage, records: records.length }, policy: { ...base.policy, selection_gate: { ...base.policy.selection_gate, selected: records.length, candidates: Math.max(base.policy.selection_gate.candidates, records.length) } }, records });
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
  const archive = selectedArchive([{ ...fixtureArchive.records[0], topics: ['detención'] }]);
  const result = searchNews({ q: 'detencion' }, archive);
  assert.equal(result.data[0].review_status, 'not_reviewed');
  assert.equal(result.meta.coverage.status, 'partial');
  assert.ok(result.meta.review_notice.includes('no constituyen casos validados'));
  assert.deepEqual(Object.keys(result.data[0].news_dossier.fields), NEWS_DOSSIER_FIELDS);
  assert.ok(Object.values(result.data[0].news_dossier.fields).every(field => field.status === 'pending' && field.value === null));
});

test('every selected news result has a worksheet regardless of discovery hint, without inferring public dossier fields', () => {
  const current = selectedArchive(fixtureArchive.records.map((reference, index) => ({ ...reference, scope_hint: ['el_salvador', 'international_context', 'undetermined'][index] })));
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
  const source = { ...fixtureArchive.records[0], published_date: '2026-09-01' };
  const archive = selectedArchive([source]);
  const dossier = pendingNewsDossier(source);
  Object.assign(dossier, { extraction_status: 'processed', human_reviewed: true, reviewed_by: 'reviewer', reviewed_at: '2026-10-10', review_notes: 'Fixture de revisión editorial explícita de fuente y privacidad.', source_title: 'Título ficticio minimizado por privacidad' });
  dossier.classification = { jurisdiction: 'SV', relevance: 'relevant', record_type: null };
  dossier.fields.summary = { value: 'APPROVED_TEST_VALUE', status: 'reported', evidence_refs: ['bibliography'], note: null };
  const baseline = getStats();
  const approved = searchNews({}, archive, { schema_version: 1, dossiers: [dossier] });
  assert.equal(approved.data[0].review_status, 'not_reviewed');
  assert.equal(approved.data[0].news_dossier.human_reviewed, true);
  assert.equal(approved.data[0].news_dossier.fields.summary.value, 'APPROVED_TEST_VALUE');
  assert.equal(approved.data[0].news_dossier.classification.relevance, 'relevant');
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

test('public API rejects unselected candidates and never uses title hints as relevance evidence', () => {
  const archive = selectedArchive([fixtureArchive.records[0]]);
  assert.equal(assertSelectedNewsArchive(archive), archive);
  for (const mutate of [
    value => { delete value.policy.selection_gate; },
    value => { delete value.records[0].selection; },
    value => { value.records[0].selection.relevance = 'not_relevant'; },
    value => { value.records[0].selection.jurisdiction = 'other'; },
    value => { value.records[0].selection.evidence.relevance_paragraph_ids = []; },
    value => { value.records[0].selection.private_article = 'PRIVATE_TEST_VALUE'; },
    value => { value.policy.selection_gate.selected = 999; },
  ]) {
    const unsafe = structuredClone(archive); mutate(unsafe);
    unsafe.records[0].title = 'Derechos humanos en El Salvador';
    unsafe.records[0].scope_hint = 'el_salvador';
    assert.throws(() => searchNews({}, unsafe, { schema_version: 1, dossiers: [] }), error => error.status === 503 && error.code === 'archive_selection_invalid' && !error.message.includes('PRIVATE_TEST_VALUE'));
  }
});

test('a later human decision excluding Salvadoran human-rights relevance takes precedence over automatic selection', () => {
  const source = fixtureArchive.records[0];
  const archive = selectedArchive([source]);
  for (const classification of [
    { jurisdiction: 'other', relevance: 'relevant', record_type: null },
    { jurisdiction: 'SV', relevance: 'not_relevant', record_type: null },
    { jurisdiction: 'unclear', relevance: 'relevant', record_type: null },
  ]) {
    const dossier = pendingNewsDossier(source);
    Object.assign(dossier, { extraction_status: 'processed', human_reviewed: true, classification, reviewed_by: 'reviewer', reviewed_at: '2026-10-10', review_notes: 'Fixture: revisión editorial excluye la referencia del alcance público.' });
    const response = searchNews({}, archive, { schema_version: 1, dossiers: [dossier] });
    assert.deepEqual(response.data, []);
    assert.equal(response.pagination.total, 0);
  }
});

test('automatic locations are a separate unreviewed projection and never override a reviewed dossier', () => {
  const source = { ...fixtureArchive.records[0], published_date: '2026-09-01' };
  const archive = selectedArchive([source]);
  const location = {
    candidate_id: source.id, source_url: source.source_url, publisher: source.publisher,
    published_date: source.published_date, department: 'San Salvador',
    review_status: 'automated_unreviewed', human_reviewed: false, method: 'gemini_explicit_department',
    extracted_at: '2026-10-10T12:00:00Z', source_checked_at: '2026-10-10T12:00:00Z',
    evidence: { paragraph_id: 'p1', content_sha256: source.selection.evidence.content_sha256 },
  };
  const payload = { schema_version: 1, generated_at: '2026-10-10T12:00:00Z', locations: [location] };
  const empty = { schema_version: 1, dossiers: [] };
  const result = searchNews({}, archive, empty, payload);
  assert.deepEqual(result.data[0].automatic_location, location);
  assert.equal(result.data[0].news_dossier.fields.department.value, null);
  assert.equal(result.data[0].news_dossier.human_reviewed, false);
  assert.equal(result.meta.automatic_locations_status, 'available');
  const municipal = { ...location, department: 'Ahuachapán', method: 'gemini_municipality_reference', resolution: {
    type: 'municipality_to_department', municipality: 'Jujutla', municipality_kind: 'district',
    reference_version: municipalityReference.reference_version, source_url: `${municipalityReference.source.url}#page=6`,
  } };
  const fromMunicipality = searchNews({}, archive, empty, { ...payload, schema_version: 2, locations: [municipal] });
  assert.deepEqual(fromMunicipality.data[0].automatic_location, municipal);
  assert.equal(fromMunicipality.data[0].news_dossier.fields.department.value, null);
  const wrongDepartment = { ...municipal, department: 'San Salvador' };
  assert.equal(searchNews({}, archive, empty, { ...payload, schema_version: 2, locations: [wrongDepartment] }).data[0].automatic_location, null);
  const corroborated = { ...location, method: 'gemini_department_corroborated', evidence: { paragraph_id: 'p3', content_sha256: 'a'.repeat(64) },
    corroboration: { strategy: 'same_article_explicit_department', model_paragraph_id: 'p1', explicit_paragraph_id: 'p3', rule_version: 1 },
  };
  const corroboratedPayload = { ...payload, schema_version: 3, locations: [corroborated] };
  const fromCorroboration = searchNews({}, archive, empty, corroboratedPayload);
  assert.deepEqual(fromCorroboration.data[0].automatic_location, corroborated);
  assert.equal(fromCorroboration.data[0].news_dossier.fields.department.value, null);
  assert.equal(fromCorroboration.data[0].news_dossier.human_reviewed, false);
  const sourceLocations = [
    { ...location, method: 'source_explicit_department', rule_version: 1 },
    { ...municipal, method: 'source_municipality_reference', rule_version: 1 },
  ];
  const { department: directDepartment, evidence: directEvidence, ...commonLocation } = location;
  const pluralLocation = { ...commonLocation, method: 'source_administrative_mentions', rule_version: 2,
    evidence: { content_sha256: directEvidence.content_sha256 }, mentions: [
      { method: 'source_explicit_department', department: directDepartment, paragraph_id: 'p1' },
      { method: 'source_municipality_reference', department: municipal.department, paragraph_id: 'p2', resolution: municipal.resolution },
    ],
  };
  const pluralPayload = { ...payload, schema_version: 5, locations: [pluralLocation] };
  const pluralResult = searchNews({}, archive, empty, pluralPayload);
  assert.deepEqual(pluralResult.data[0].automatic_location, pluralLocation);
  assert.equal(pluralResult.data.length, 1);
  assert.equal(pluralResult.data[0].automatic_location.department, undefined);
  assert.equal(pluralResult.data[0].news_dossier.fields.department.value, null);
  for (const schema_version of [1, 2, 3, 4]) {
    assert.equal(searchNews({}, archive, empty, { ...pluralPayload, schema_version }).data[0].automatic_location, null);
  }
  for (const change of [
    value => { value.department = 'San Salvador'; },
    value => { value.rule_version = 1; },
    value => { value.evidence.paragraph_id = 'p1'; },
    value => { value.evidence.content_sha256 = 'f'.repeat(64); },
    value => { value.mentions[1].department = 'San Salvador'; },
    value => { value.mentions[1].private_quote = 'PRIVATE_SOURCE_VALUE'; },
  ]) {
    const unsafe = structuredClone(pluralPayload); change(unsafe.locations[0]);
    const response = searchNews({}, archive, empty, unsafe);
    assert.equal(response.data[0].automatic_location, null);
    assert.ok(!JSON.stringify(response).includes('PRIVATE_SOURCE_VALUE'));
  }
  for (const sourceLocation of sourceLocations) {
    const sourcePayload = { ...payload, schema_version: 4, locations: [sourceLocation] };
    const response = searchNews({}, archive, empty, sourcePayload);
    assert.deepEqual(response.data[0].automatic_location, sourceLocation);
    assert.equal(response.data[0].automatic_location.human_reviewed, false);
    assert.equal(response.data[0].automatic_location.corroboration, undefined);
    assert.equal(response.data[0].news_dossier.fields.department.value, null);
    assert.equal(response.data[0].news_dossier.human_reviewed, false);
    assert.deepEqual(searchNews({}, archive, empty, { ...sourcePayload, schema_version: 5,
      locations: [{ ...sourceLocation, rule_version: 2 }] }).data[0].automatic_location, { ...sourceLocation, rule_version: 2 });
    for (const schema_version of [1, 2, 3]) {
      const incompatible = searchNews({}, archive, empty, { ...sourcePayload, schema_version });
      assert.equal(incompatible.data[0].automatic_location, null);
      assert.equal(incompatible.meta.automatic_locations_status, 'unavailable');
    }
    for (const patch of [
      { rule_version: undefined }, { rule_version: true }, { rule_version: 2 },
      { corroboration: corroborated.corroboration }, { source_body: 'PRIVATE_SOURCE_VALUE' },
      { evidence: { ...sourceLocation.evidence, content_sha256: 'f'.repeat(64) } },
    ]) {
      const unsafe = searchNews({}, archive, empty, { ...sourcePayload, locations: [{ ...sourceLocation, ...patch }] });
      assert.equal(unsafe.data[0].automatic_location, null);
      assert.ok(!JSON.stringify(unsafe).includes('PRIVATE_SOURCE_VALUE'));
    }
  }
  assert.deepEqual(searchNews({}, archive, empty, { ...corroboratedPayload, schema_version: 4 }).data[0].automatic_location, corroborated);
  for (const schema_version of [1, 2]) {
    const response = searchNews({}, archive, empty, { ...corroboratedPayload, schema_version });
    assert.equal(response.data[0].automatic_location, null);
    assert.equal(response.meta.automatic_locations_status, 'unavailable');
  }
  for (const change of [
    value => { value.corroboration.model_paragraph_id = 'p3'; },
    value => { value.evidence.paragraph_id = 'p1'; },
    value => { value.corroboration.article_body = 'PRIVATE_TEST_VALUE'; },
    value => { delete value.corroboration; },
    value => { value.method = 'gemini_explicit_department'; },
  ]) {
    const unsafe = structuredClone(corroboratedPayload); change(unsafe.locations[0]);
    const response = searchNews({}, archive, empty, unsafe);
    assert.equal(response.data[0].automatic_location, null);
    assert.ok(!JSON.stringify(response).includes('PRIVATE_TEST_VALUE'));
  }
  const approved = pendingNewsDossier(source);
  Object.assign(approved, { extraction_status: 'processed', human_reviewed: true, reviewed_by: 'reviewer', reviewed_at: '2026-10-10', review_notes: 'Fixture: ubicación deliberadamente omitida tras revisión editorial.' });
  approved.classification = { jurisdiction: 'SV', relevance: 'relevant', record_type: null };
  approved.fields.department = { value: null, status: 'withheld', evidence_refs: [], note: 'Ubicación protegida tras revisión editorial explícita.' };
  assert.equal(searchNews({}, archive, { schema_version: 1, dossiers: [approved] }, payload).data[0].automatic_location, null);
  assert.equal(searchNews({}, archive, null, payload).data[0].automatic_location, null);
  assert.equal(searchNews({}, archive, { schema_version: 1, dossiers: [approved] }, corroboratedPayload).data[0].automatic_location, null);
  assert.equal(searchNews({}, archive, { schema_version: 1, dossiers: [approved] }, pluralPayload).data[0].automatic_location, null);
  for (const sourceLocation of sourceLocations) {
    assert.equal(searchNews({}, archive, { schema_version: 1, dossiers: [approved] }, { ...payload, schema_version: 4, locations: [sourceLocation] }).data[0].automatic_location, null);
  }
  for (const change of [
    value => { value.human_reviewed = true; },
    value => { value.raw_article = 'PRIVATE_TEST_VALUE'; },
    value => { value.publisher = 'Medio equivocado'; },
    value => { value.department = 'Otra región'; },
    value => { value.evidence.content_sha256 = 'f'.repeat(64); },
  ]) {
    const unsafe = structuredClone(payload); change(unsafe.locations[0]);
    const response = searchNews({}, archive, empty, unsafe);
    assert.equal(response.data[0].automatic_location, null);
    assert.ok(!JSON.stringify(response).includes('PRIVATE_TEST_VALUE'));
  }
});

test('OpenAPI is a public, versioned executable contract', async () => {
  const { response, body } = await get('/api/v1/openapi.json');
  assert.equal(response.status, 200);
  assert.equal(body.openapi, '3.1.0');
  const locationSchema = body.components.schemas.AutomaticNewsLocation;
  assert.ok(locationSchema.properties.method.enum.includes('gemini_department_corroborated'));
  assert.equal(locationSchema.properties.corroboration.additionalProperties, false);
  assert.deepEqual(locationSchema.properties.corroboration.required, ['strategy', 'model_paragraph_id', 'explicit_paragraph_id', 'rule_version']);
  assert.equal(locationSchema.properties.corroboration.properties.rule_version.const, 1);
  assert.ok(locationSchema.allOf.some(rule => rule.if.properties.method.const === 'gemini_department_corroborated' && rule.then.required.includes('corroboration')));
  assert.ok(locationSchema.properties.method.enum.includes('source_explicit_department'));
  assert.ok(locationSchema.properties.method.enum.includes('source_municipality_reference'));
  assert.deepEqual(locationSchema.properties.rule_version.enum, [1, 2]);
  assert.equal(locationSchema.properties.rule_version.type, 'integer');
  assert.ok(locationSchema.allOf.some(rule => rule.if.properties.method.enum?.includes('source_municipality_reference') && rule.then.required.includes('resolution')));
  assert.ok(locationSchema.allOf.some(rule => rule.if.properties.method.enum?.includes('source_explicit_department') && rule.then.required.includes('rule_version')));
  const locationCollection = body.components.schemas.AutomaticNewsLocationCollection;
  assert.deepEqual(locationCollection.properties.schema_version.enum, [1, 2, 3, 4, 5]);
  assert.equal(locationCollection.additionalProperties, false);
  assert.ok(locationCollection.allOf.some(rule => JSON.stringify(rule.if.properties.schema_version.enum) === '[1,2,3]'
    && rule.then.properties.locations.items.not.properties.method.enum.includes('source_explicit_department')));
  assert.ok(locationSchema.properties.method.enum.includes('source_administrative_mentions'));
  assert.equal(locationSchema.properties.mentions.minItems, 2);
  assert.equal(locationSchema.properties.mentions.maxItems, 14);
  assert.equal(locationSchema.properties.mentions.items.$ref, '#/components/schemas/AutomaticNewsLocationMention');
  const pluralRule = locationSchema.allOf.find(rule => rule.if.properties.method.const === 'source_administrative_mentions');
  assert.deepEqual(pluralRule.then.not.required, ['department']);
  assert.deepEqual(pluralRule.then.properties.evidence.not.required, ['paragraph_id']);
  assert.equal(pluralRule.then.properties.rule_version.const, 2);
  assert.equal(body.components.schemas.AutomaticNewsLocationMention.additionalProperties, false);

  assert.deepEqual(body.security, []);
  assert.ok(body.paths['/api/v1/records/{id}']);
  assert.ok(body.paths['/api/v1/news']);
  assert.ok(body.components.schemas.News.required.includes('news_dossier'));
  assert.ok(body.components.schemas.News.required.includes('selection'));
  assert.equal(body.components.schemas.NewsSelection.oneOf.length, 2);
  assert.ok(body.components.schemas.NewsSelection.oneOf.every(schema => schema.additionalProperties === false && schema.properties.jurisdiction.const === 'SV' && schema.properties.relevance.const === 'relevant'));
  assert.equal(body.components.schemas.NewsSelectionGate.additionalProperties, false);
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
    assert.equal(validateNewsSelection(news.structuredContent.data[0].selection).jurisdiction, 'SV');
    assert.equal(news.structuredContent.meta.public_scope, 'human_rights_el_salvador');
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
